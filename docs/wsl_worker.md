# WSL Worker Guide

The optional worker lets Windows Host PyMOL submit structure text to calculation
backends running in WSL2. It can also stage a structure from an explicitly
allowlisted WSL root into a Windows-local cache. It is intentionally a small
HTTP boundary:

```text
Windows PyMOL (`pymolplugins/remote/commands.py`)
    -> HTTP + JSON/binary + optional Bearer token
    -> WSL (`pymolplugins/worker/app.py`)
    -> optional PLIP or separately licensed PyRosetta backend
```

Calculation requests carry PDB text and still reject all path/file fields. File
staging is a separate, default-off endpoint: it accepts only a configured root
alias and a canonical relative POSIX path. There is no endpoint for shell
commands, arbitrary Python, arbitrary absolute paths, or arbitrary
RosettaScripts XML.

## Current scope and limitations

- Core health/capability endpoints use only the Python standard library.
- PLIP is optional.  The canonical Host exporter validates a lossless atom map,
  the worker returns `pymolplugins.interactions/1.0`, and Qt requests are
  asynchronous.  A reviewed 1A4W calculation has passed direct, loopback HTTP,
  and Windows-PyMOL-to-WSL-to-Host-render paths with PLIP 3.0.0; this does not
  make PLIP a core dependency or validate every PLIP/OpenBabel build.
- PyRosetta is optional, separately licensed, and never downloaded by this
  repository.  Metrics require a wheel/environment obtained legally by the user.
- Backend work is serialized where required. A Host socket timeout does not stop
  an in-progress C++ calculation. Hard cancellation will require process
  isolation; do not treat the current timeout as computation cancellation.
- Default networking assumes Windows-to-WSL localhost forwarding.  A WSL IP is
  an explicit fallback, not a stable address.
- File staging is disabled unless at least one `--stage-root ALIAS=PATH` is
  supplied. It serves regular files only, resolves symlinks before enforcing
  root containment, and applies file-size and transfer-time limits.
- The staging deadline is checked before and after each synchronous file read;
  Python cannot forcibly cancel a kernel/FUSE read that is itself stuck. The
  Host has an independent socket/total deadline, but a persistently hung SSHFS
  mount must still be repaired or unmounted.

See [implementation status](implementation_status.md) before relying on a
metric in production.

## 1. Prepare an isolated environment

Use a worker-specific environment; do not install optional scientific packages
into Windows PyMOL's embedded Python.

The Python version must match the optional backend's supported binary.  The
repository does not prescribe a PyRosetta version or public download URL.

Example environment creation with `uv`:

```bash
cd /home/admin/gitrepo/PymolPlugins
uv venv .venv-worker --python <backend-supported-python>
. .venv-worker/bin/activate
python --version
python scripts/probe_worker_env.py --pretty
```

Environment creation does not install PLIP or PyRosetta.  For PLIP/OpenBabel,
follow the upstream installation and license documentation, then verify the
same worker interpreter can import them.  For PyRosetta, use only the licensed
artifact and instructions supplied to you by RosettaCommons.

### Tested PLIP fallback on this WSL host

The preferred installation remains PLIP's supported upstream path with an
upstream/system OpenBabel build.  On this Debian WSL host, a normal
`uv pip install plip` attempted an isolated OpenBabel source build and failed
because `openbabel-3.pc`, headers, and SWIG build prerequisites were absent.
The following pinned fallback was then validated in the ignored
`.venv-worker`; it is recorded for reproducibility, not prescribed as the
default supply chain:

```bash
sudo apt-get install -y libxrender1

uv pip install --python .venv-worker/bin/python \
  openbabel-wheel==3.1.1.23 \
  numpy==2.2.6 \
  lxml==6.0.2 \
  setuptools \
  wheel

uv pip install --python .venv-worker/bin/python \
  --no-build-isolation --no-deps \
  plip==3.0.0

.venv-worker/bin/python scripts/probe_worker_env.py \
  --import-optional --pretty
```

`libxrender1` was required by the wheel's `pdbformat.so`; without it, OpenBabel
failed during PDB loading with `not enough values to unpack (expected 2, got
1)`.  `openbabel-wheel` is a community binary distribution rather than an
Open Babel or PLIP upstream release, and `--no-deps` deliberately bypasses the
resolver.  Before using this route beyond an isolated workstation, review the
publisher, package hashes, bundled-library licenses, CPU/glibc compatibility,
and your redistribution obligations.  Do not commit the environment or wheels.

Optional isolated import probes run in child processes and never initialize
PyRosetta:

```bash
python scripts/probe_worker_env.py --import-optional --pretty
```

The probe reports discovery/importability; it does not prove that a metric API
matches the installed Rosetta build.

## 2. Configure authentication

Generate a high-entropy token in WSL:

```bash
export PYMOLPLUGINS_WORKER_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

Pass the same value to the Windows process that launches PyMOL.  For a temporary
PowerShell session:

```powershell
$env:PYMOLPLUGINS_WORKER_TOKEN = '<same token>'
$env:PYMOLPLUGINS_WORKER_URL = 'http://127.0.0.1:8765'
```

Restart PyMOL after changing inherited environment variables.  Do not:

- pass the token through a command-line `--token` argument, where process tools
  can expose it;
- paste it into a `.pml`, session file, notebook, issue, or shell history;
- store it in this repository.  `.env*` and worker virtual environments are
  ignored, but OS credential storage is preferable to a plaintext file.

## 3. Start the worker

From the repository root with the worker environment active:

```bash
scripts/start_worker.sh
```

The script selects, in order, `PYMOLPLUGINS_WORKER_PYTHON`,
`.venv-worker/bin/python`, then `python3`.  Override non-secret settings when
needed:

```bash
scripts/start_worker.sh --python /path/to/python --port 8765 --max-body-mb 10
```

To opt into single-file staging for a known dataset root:

```bash
scripts/start_worker.sh \
  --stage-root structures=/home/user/structures \
  --max-stage-mb 256 \
  --stage-timeout 60
```

`--stage-root` is repeatable. The alias is exposed in capabilities, but the
server path is not. CSV rows and Host clients must send `structures` plus a
relative path such as `run_01/model.pdb`; they cannot send
`/home/user/structures/run_01/model.pdb`. Omitting every `--stage-root` leaves
the endpoint disabled.

For Structure Annotator, the CSV may still contain the full WSL path. In its
**Source** tab set:

```text
WSL root:  /home/user/structures
alias:     structures
```

The controller verifies that each row is below that root, derives only the
relative suffix, and sends `structures` plus that suffix. `stage_root` and
`stage_alias` are saved per CSV, so opening another CSV does not inherit them.
The Windows PyMOL process must inherit `PYMOLPLUGINS_WORKER_URL` and
`PYMOLPLUGINS_WORKER_TOKEN`; WSL's ordinary Python cannot host the PyMOL/Qt
controller. See the [step-by-step annotator walkthrough](structure_annotator_csv_walkthrough.md).

It requires a token by default.  For an isolated loopback-only diagnostic, an
explicit unauthenticated start is possible:

```bash
scripts/start_worker.sh --allow-no-token
```

Do not use that mode for routine work.  Stop the foreground service with
`Ctrl-C`.  The worker logs request path/status but is designed not to log the
structure body or token.

The underlying module can also be inspected directly:

```bash
python -m pymolplugins.worker.app --help
```

The startup wrapper is recommended because it adds safer binding/token checks.

## 4. Verify inside WSL

With the token still in the environment:

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $PYMOLPLUGINS_WORKER_TOKEN" \
  http://127.0.0.1:8765/v1/health

python scripts/probe_worker_env.py --check-worker --pretty
```

Expected health behavior:

- top-level `status` is `ok` when the worker itself is healthy;
- each optional backend independently reports `available: true/false`;
- missing PLIP or PyRosetta is a capability state, not a worker crash;
- when authentication is configured, a missing/wrong token returns HTTP 401.

## 5. Verify from Windows and PyMOL

PowerShell health probe:

```powershell
$headers = @{ Authorization = "Bearer $env:PYMOLPLUGINS_WORKER_TOKEN" }
Invoke-RestMethod -Headers $headers -Uri "$env:PYMOLPLUGINS_WORKER_URL/v1/health"
```

After loading `plugins/pymol_plugins.py` in Windows PyMOL:

```pymol
pp_worker_health
pp_worker_capabilities
```

Rosetta metrics, when the backend is legally installed and reported available:

```pymol
pp_rosetta_metrics selection=complex, target_chains=A, binder_chains=B, metrics="cms,bunsat_bound_heavy,bunsat_interface_delta"
pp_rosetta_metrics_async selection=complex, target_chains=A, binder_chains=B, metrics="cms,bunsat_bound_heavy,bunsat_interface_delta"
```

The Host adapter exports the selection as PDB text and restores temporary PDB
connectivity settings afterward.  Use the async command in the GUI; the
synchronous command remains available for console/batch use.

Canonical PLIP analysis is also asynchronous by default:

```pymol
pp_interact_run plip_overview, source=complex, partner2="resn LIG and chain X and resi 501"
pp_worker_jobs
pp_worker_cancel job_id
```

For a deliberate headless/batch call without a Qt event loop, pass
`async_mode=0`; that call blocks until the HTTP request finishes.

PDB export and identity-map construction occur on the main thread.  The Qt
worker performs HTTP only; validation, exact-ligand filtering, and PyMOL output
creation occur on the main thread after a complete response.  Cancellation
suppresses result handling but does not claim to interrupt a running PLIP or
PyRosetta C++ call.

## 6. Endpoints and limits

| Method/path | Purpose |
| --- | --- |
| `GET /v1/health` | Worker and backend availability. |
| `GET /v1/capabilities` | Supported endpoints/formats/metrics. |
| `POST /v1/analyze` | Rosetta metric request with inline PDB text. |
| `POST /v1/interactions/plip` | Canonical interaction result from inline PDB text plus an explicit atom map. |
| `POST /v1/stage/stat` | Conditional stat identity lookup from an allowlisted root without file content. |
| `POST /v1/stage/stat-batch` | Ordered stat identities/errors for up to 256 unique allowlisted relative paths. |
| `POST /v1/stage/file` | Conditional binary download from an allowlisted root alias plus a relative path. |

Default request-body limit is 10 MiB and can be lowered with
`--max-body-mb`.  The service accepts schema version 1.  It is not a general
file server or job scheduler.  `/v1/capabilities` reports the effective request
body and socket-read limits. The JSON Host client independently caps requests
at 10 MiB and responses at 20 MiB and refuses redirects.

When enabled, staging defaults to one 256 MiB file and a 60-second server-side
deadline; use `--max-stage-mb` and `--stage-timeout` to change those bounds.
The response is sequential `application/octet-stream` with `Content-Length`
and path-free source stat identity headers. The Host staging client independently
checks its response limit, schema, root alias, size, and identity headers, writes
to an adjacent `.part`, and calls `os.replace` only after a complete transfer.
It also refuses redirects. This endpoint is a cache transport, not a general
file server: there is no directory listing, absolute path, write, delete, or
shell surface.

For a persistent cache, call `stat_staged_file(root_alias, relative_path)`
first. It returns the same `StagedSourceIdentity` fields as
`download_staged_file(...).source` without transferring structure content. Use
that identity for the warm-cache lookup and download only on a miss. Because a
source can change between the stat and download requests, a cache miss must be
stored under the identity returned by the completed download, not blindly under
the earlier stat identity. The opaque complete `v1-<64-hex>` identity token is
the SHA-256 of device/inode/size/mtime/mode metadata, not a structure-content
digest. It is part of the persistent cache key in addition to provider,
canonical alias/path, size, and mtime; do not truncate or reconstruct it on the
Host. Normal replacement/metadata changes invalidate the entry, but an in-place
rewrite that deliberately preserves every identity field is outside this
stat-based freshness guarantee.

For a CSV with at least two unique WSL paths, Structure Annotator uses
`stat_staged_files(root_alias, relative_paths)`. The Host deduplicates while
preserving first occurrence order, then chunks at both 256 items and 64 KiB;
each response is capped at 2 MiB and must preserve exact path order and
cardinality. A valid batch envelope returns HTTP 200 even when individual
paths are missing, unreadable, invalid, or too large; each such item carries a
bounded typed error. Version 1 accepts only the worker's exact per-item
code/status pairs (`invalid_stage_path`/400, `stage_not_regular_file`/400,
`stage_path_forbidden`/403, `stage_file_unreadable`/403,
`stage_file_not_found`/404, and `stage_file_too_large`/413); a request-level
code inside an item or a mismatched status is a malformed response.
Authentication, unknown aliases, malformed envelopes,
request limits, and the batch-wide deadline remain request-level failures.
Only an old worker's exact HTTP 404 with `code=not_found` triggers sequential
`stage/stat` compatibility fallback. `staging_disabled`, authentication,
transport, schema, or malformed-response errors are never hidden by fallback.

## 7. Networking modes

### Preferred: localhost forwarding

Keep the worker on `127.0.0.1` and use
`http://127.0.0.1:8765` from Windows.  This is the narrowest boundary.

If Windows cannot reach the worker:

1. Confirm WSL can reach `/v1/health` locally.
2. Confirm Windows has not disabled WSL localhost forwarding.
3. Check that port 8765 is not occupied in WSL or Windows.
4. Restart WSL networking/PyMOL after configuration changes.

### Fallback: exact WSL interface address

Only if localhost forwarding is unavailable, discover the current WSL IPv4
address and bind that exact address (the conservative startup wrapper accepts
IPv4 addresses only):

```bash
hostname -I
scripts/start_worker.sh --host <exact-wsl-ip> --allow-non-loopback
```

This mode requires a non-empty token.  Set the Host URL to that IP, restrict the
Windows/WSL firewall to the Host, and expect the IP to change after WSL restarts.
Never expose this plaintext HTTP service to a LAN, bind `0.0.0.0` for
convenience, or reuse its token for another service.

## 8. Troubleshooting

| Symptom | Checks / action |
| --- | --- |
| `connection refused` | Worker process/port, WSL-local curl, localhost forwarding, Host URL, firewall. |
| HTTP 401 | Ensure WSL and the Windows process inherited the exact same token; restart PyMOL. |
| HTTP 413 | Reduce the PyMOL selection or deliberately raise `--max-body-mb` after reviewing memory/risk. |
| HTTP 422 `PyRosetta is unavailable` | Run the probe with the exact worker interpreter; verify the licensed wheel supports its Python/CPU/WSL environment. |
| HTTP 422 PLIP/OpenBabel error | Verify `import plip`, OpenBabel plugin/data discovery, and PDB format support in the same worker environment. |
| PLIP imports but PDB loading reports `not enough values to unpack` | Inspect native dependencies with the exact worker environment; the tested community wheel needed `libXrender.so.1` from Debian `libxrender1`. |
| Host timeout but worker remains busy | Expected current limitation: socket timeout does not cancel C++ work. Wait or stop the worker; use a smaller input. |
| `pp_worker_health` unknown | Load `plugins/pymol_plugins.py` and run `pp_load`; inspect `pp_doctor`. |
| Worker works in WSL but not Windows | Test PowerShell separately, then check forwarding or use the exact-IP fallback with token/firewall. |
| Backend says importable but metric fails | Capture backend versions, init flags, request parameters, input SHA256, and traceback; APIs vary across Rosetta builds. |

Do not solve a Windows Host failure by pointing PyMOL at
`/home/.../.venv-worker/bin/python`; Windows cannot execute that path.  Keep the
HTTP boundary.

## 9. License and provenance requirements

- PyRosetta has separate licensing terms and is not distributed here.
- PLIP/OpenBabel, ANVIL, and MSMS have their own licenses and supported install
  channels; an adapter does not grant redistribution rights.
- Do not commit wheels, binaries, credentials, license files, or tokens.
- Record worker/backend versions, init flags, input SHA256, partner definitions,
  and effective metric options with results.
- The repository itself still requires an explicit top-level license before
  making redistribution assumptions.
