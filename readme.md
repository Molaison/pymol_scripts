# PymolPlugins

PymolPlugins is a structure-inspection toolkit for PyMOL 2.x.  Its canonical
commands use the `pp_` prefix and are designed to compose safely in an existing
session: navigation owns only its managed objects, transforms have explicit
preview/commit/undo boundaries, styles can be reverted, and interaction output
is tracked by an exact manifest.

The project has two runtime surfaces:

- **Windows Host PyMOL** owns the GUI, objects, selections, Qt, CGO rendering,
  and all `cmd.*` mutations.
- **WSL2** is the development/test surface and can run an optional HTTP worker
  for PLIP or a separately licensed PyRosetta installation.

Windows PyMOL never executes a WSL Python path.  Remote calculations send PDB
text in JSON; they do not send Host/WSL file paths, shell commands, or arbitrary
RosettaScripts XML.

## Status

The 333 Python/fake-PyMOL tests run in WSL. An automated Windows Host smoke run
also passes on PyMOL 3.1.3.1 with all 63 canonical commands, including
`matrix_mode=0/1/2`, static CGO/map/measurement followers, group navigation,
checked object/group command failures, anchor/motif/ligand alignment,
PCA/cyclic orientation undo, style revert, and exact local interaction cleanup.
The same Host gate now also covers Structure Annotator directory and CSV
sources, row-level sidecar annotations, merged export, dataset/global state,
Windows hidden-directory behavior, cursor/config restoration, and fuzzy
history. A separate visible PyQt5 smoke verifies the real three-tab panel,
automatic popup display, mouse/keyboard candidate acceptance, and that a CSV
preview row cannot navigate a previously loaded dataset. Real `K:` FUSE input
passes cold/warm Windows-local cache checks. The authenticated WSL staging
client/cache/materializer chain separately passes a real worker cold/warm E2E;
Windows-PyMOL-to-worker Structure Annotator staging remains a manual Host gate.
Windows PyMOL also reached the live WSL worker through
localhost forwarding and validated PLIP export identity.  Real mouse movement, callback-owned CGO, Qt
timeout/cancel, licensed PyRosetta, and PyMOL 2.x compatibility remain separate
gates.  See [implementation status](docs/implementation_status.md), the
[Host record](docs/validation/windows_host_smoke_2026-07-25.md), and the
[Windows Host acceptance checklist](docs/windows_host_acceptance.md).

The following older modules are not canonical entry points:

- `workflows/dataset_navigation/file_management.py` and
  `analysis/alignment_selection/align_by_ligand.py` are compatibility facades.
- legacy style scripts delegate to the style registry.
- `workflows/dataset_navigation/screen_boost.py` and
  `file_management.py` are compatibility adapters over the same canonical
  `NavigationSession`; neither owns a second cursor or binds keys at import.
- `analysis/interactions/plip_interactions.py` keeps old command/UI names but
  its public run/clear commands delegate to the canonical WSL-backed pipeline
  and exact manifests.  New code should use `pp_interact_*` directly.
- `plugins/structure_annotator.py` retains its annotation domain and UI while
  reusing the canonical navigation cursor and explicit key leases.

The detailed classification is in [the script catalog](docs/script_catalog.md).
Canonical implementation now lives under the feature-oriented
`pymolplugins/` package. Historical `analysis/`, `workflows/`, `visualization/`,
`utilities/`, and `services/` paths are import/run compatibility shims only;
optional standalone tools live under `extras/`.

## Load in Windows PyMOL

Use a disposable session for the first test.

1. Make the repository visible to Windows, either from a Windows checkout/copy
   or through `\\wsl.localhost\<distro>\home\admin\gitrepo\PymolPlugins`.
2. In PyMOL Plugin Manager, install or load `plugins/pymol_plugins.py`.
   Loading a script through UNC is supported, but Windows automation must start
   from a normal Windows working directory rather than an UNC current directory.
3. In the PyMOL console, run:

   ```pymol
   pp_load
   pp_doctor
   pp_help
   ```

`pp_load` imports only the canonical modules.  `pp_doctor` reports the Host
Python/PyMOL API and optionally probes the WSL worker; `pp_help` lists all
canonical commands by category.  A worker failure is not
fatal for local navigation, alignment, styles, geometry, or local interactions.

## Canonical quick start

### Navigate files, objects, or root groups

```pymol
pp_nav_start source=groups, entries="design_1,design_2", window_size=1, bind_keys=0
pp_nav_next
pp_nav_pin
pp_nav_status
pp_nav_stop
```

For files, provide `path` and optionally `pattern`; for existing molecule
objects use `source=objects`.  A group is one atomic navigation entry.  Keyboard
bindings are opt-in with `bind_keys=1` and are restored on stop when the Host
exposes restorable key mappings.

### Resolve and iterate objects or groups

PyMOL's built-in `iterate` evaluates an expression once per atom. For
object/group-level work, use the separate target resolver:

```pymol
# Preview all matched objects; no scene mutation.
pp_target_resolve "design_*", match=glob, kinds=object

# Preview one command per recursive group leaf, including CGO/map/measurement.
pp_foreach assembly, "disable {target}", group_mode=leaves

# Execute only after reviewing the preview.
pp_foreach assembly, "disable {target}", group_mode=leaves, commit=1

# Approximate-name matching is explicit and can be limited.
pp_target_resolve desgn_alpha, match=fuzzy, fuzzy_cutoff=0.75, limit=1
```

Matching modes are `exact`, `glob`, `contains`, `regex`, `fuzzy`, and `auto`.
Groups can remain `atomic`, expand to all recursive `leaves`, include `both`,
or filter to `molecules`. `pp_foreach` previews by default because arbitrary
PyMOL commands cannot be generically rolled back. Committed templates must
render as one physical parser line (semicolon-separated commands are allowed).
Each dispatch carries a temporary same-line completion marker, so parser and
`CmdException` failures are reported in `errors` and obey
`on_error=stop|continue`; the marker is removed immediately. PyMOL's console
contains the detailed parser error because its public `cmd.do` API does not
return it.

Canonical commands also normalize PyMOL STRICT-parser quoting at registration:
one outer single-, double-, or triple-single-quote syntax layer is removed from
explicit console arguments without decoding backslashes. Quoted selections,
regexes, command templates, and Windows/UNC paths therefore reach the Python
adapter unchanged internally.

### Resume Structure Annotator work and search command history

Load the standalone annotation entry from Plugin Manager, or run
`plugins/structure_annotator.py`, then open **Structure Annotator**. After a
dataset is loaded, the annotator atomically writes:

```text
<dataset>/.pymolplugins/structure_annotator.json
```

This dataset-local file restores the regex, label/values, `On step` command,
per-bucket commands, preload options, and last file/cursor. Runtime-only PyMOL
objects, caches, navigation objects, and undo entries are intentionally not
serialized. A new PyMOL session reopens the most recently finalized dataset by
default; clear **Restore last dataset when this panel opens** to disable that
behavior.

Finalized configurations are also stored in the Host user's global history:

```text
~/.pymolplugins/structure_annotator_history.json
```

While typing, Directory, Pattern, Label name, Values, On step, and bucket
command inputs automatically show fuzzy-ranked global candidates. The history
is de-duplicated, bounded, versioned, and records use count plus recency. It is
updated when a dataset successfully loads, a configuration is explicitly
saved or used for annotation, or the panel closes—not for every intermediate
keystroke.

Useful console diagnostics are:

```pymol
sa_save_state
sa_state_status
sa_history_status
sa_history_search step_cmd, cartn
sa_history_search bucket_cmd, evrythng
```

For isolated automation, set `PYMOLPLUGINS_ANNOTATOR_HISTORY` to an alternate
history file. Dataset state is executable configuration: `On step` runs after
an object loads and a bucket command runs after annotation. Only reopen state
files from datasets you trust.

The reproducible visible Host-Qt check is
`tests/host/annotator_qt_smoke.py`; run it with the Python executable bundled
with Windows PyMOL, not WSL Python. The latest exact command and result are in
[the Host record](docs/validation/windows_host_smoke_2026-07-25.md).

### Import a score CSV and annotate rows

Choose **CSV + scores** in the Structure Annotator **Source** tab. Select the
CSV, give the exact structure-path header, optionally give a unique ID header
and semicolon-separated score columns, then use **Preview mapping** before
**Load CSV**. Preview parses and checks paths (including WSL staging `stat`)
without changing the active PyMOL scene, state, history, annotations, or path
mapping store.

Each CSV row remains an independent entry, even when two rows reference the
same structure. The **Scores** tab displays source metadata, the active label's
annotation, path/provider/cache diagnostics, substring filtering, and typed
numeric sorting. The source CSV is read-only. Annotations are append-only JSONL
events beside it:

```text
<csv-parent>/.pymolplugins/structure_annotator.<csv-id>.annotations.jsonl
```

`sa_export_csv` writes a separate, re-importable document and uses the
collision-safe column `pymol_annotation_<label>`; it refuses to overwrite the
source CSV. Per-CSV parser, mapping, staging, filter, sort, label, commands, and
cursor settings are restored from that CSV's own version-2 state file.

The default `x_k` path profile maps the literal prefixes
`/xcfhome/zpzeng/mount_local`, `/xcfhome/zpzeng`, and `~/` to `K:\`. Mapped,
UNC, configured remote-drive, and `.gz` inputs are copied once into a
Windows-local persistent cache; direct `K:\...` is treated as remote by
default. Other drives can be listed in `PYMOLPLUGINS_REMOTE_DRIVES`.
For structures available only inside WSL, start the worker with an explicit
`--stage-root ALIAS=PATH`, then enter the same PATH and ALIAS in the Source tab.
The Host performs `stat -> cache lookup -> download on miss`, and the worker
never exposes arbitrary absolute-path, listing, write, delete, or shell APIs.

Useful diagnostics are:

```pymol
sa_mapping_list
sa_cache_status 1
sa_state_status
sa_history_status
```

See the complete [CSV/path/cache/WSL walkthrough](docs/structure_annotator_csv_walkthrough.md)
before using a production score table.

### Move a group by following one manually moved object

```pymol
pp_follow_arm driver, group=my_group
# Move "driver" with the PyMOL object-movement mouse mode.
pp_follow_apply
pp_follow_undo
pp_follow_stop
```

The follower delta is derived from object TTT matrices; PyMOL groups themselves
are not scene-graph transform parents.  Static CGO objects can follow directly.
Callback-owned overlays are skipped by default; `axes_overlay.py` registers
itself automatically.  A registered rebuild hook or explicit `cgo_policy=static`
can opt into other behavior.  Undo refuses post-apply external edits unless the
user explicitly passes `force=1`.

### Sequence selection and rigid alignment

```pymol
pp_select_sequence objects="model*", queries="GGH", occurrence=all
pp_align_motif mobile, target, "GGH", move="mobile,mobile_ligand", commit=0
pp_align_ligand mobile, target, mobile_ligand=organic, target_ligand=organic, commit=0
pp_align_commit
pp_align_undo
```

Alignment anchors and moved payloads are separate.  Preview is the default;
inspect the matrix/RMSD before committing.  Preview commit fingerprints both
anchors and move-object baselines, while undo refuses to overwrite newer edits
unless explicitly forced.

### Axes, orientation, and membrane slabs

```pymol
pp_axis_pca selection=model, axis=long, name=model_axis
pp_axis_pca selection=model, atoms=heavy, weighting=mass, name=mass_axis
pp_axis_vector vector="0,0,1", center="0,0,0", name=normal_axis
pp_axis_two_points points="0,0,-10,0,0,10", name=two_point_axis
pp_orient_pca selection=model, move=model, target_axis=z, secondary_selection=ligand, commit=0
pp_axis_cyclic selection=oligomer, order=auto, name=symmetry_axis
pp_membrane_slab selection=oligomer, axis_source=cyclic, thickness=30
pp_membrane_slab selection=monomer, axis_source=vector, axis="0,0,1", thickness=30
```

PCA and cyclic-axis results include confidence/diagnostics.  Cn support focuses
on cyclic oligomers; higher-order dihedral/polyhedral symmetry is out of scope.
`pp_membrane_from_anvil` can parse an existing DUM-plane PDB or invoke an
explicit user-provided executable without a shell.  It validates protein
identity, maps rigidly reoriented planes back to the input frame, and rejects
non-rigid coordinate changes; no ANVIL binary is bundled.  Direct invocation
runs on the caller's OS: Windows PyMOL needs a Windows-native executable.  For
a WSL-only ANVIL build, run it in WSL and pass the output PDB back through a
Windows-visible/UNC path for parsing rather than pointing Windows at `/home/...`.

### Declarative styles

```pymol
pp_style_list
pp_style_apply publication_light, selection=all, snapshot=1, render=0
pp_style_revert
pp_style_render width=1600, height=1200, filename=figure.png
```

Applying a style does not ray trace by default.  `pp_style_render` is the only
canonical render entry point.

### Interaction presets

```pymol
pp_interact_list
pp_interact_run ligand_quick, source=complex, partner1="polymer.protein", partner2="organic"
pp_interact_run polar_qc, source=complex, partner1="chain A", partner2="chain B"
pp_interact_export generated_group, interactions.json
pp_interact_clear generated_group
```

Canonical presets produce one versioned `InteractionResult` and delete only
names recorded in their manifest.  `polar_qc` classifies good/weak polar pairs
and donor/acceptor clashes without the fixed temporary names or global setting
mutations in the historical `show_contacts.py`.

With the WSL worker running and PLIP/OpenBabel available, PLIP presets are
asynchronous by default:

```pymol
pp_interact_run plip_overview, source=complex, partner2="resn LIG and chain X and resi 501"
pp_worker_jobs
pp_worker_cancel job_id
```

The Host exports PDB text and a checked atom-identity map before starting the
thread.  HTTP is the only off-thread work; schema validation, exact-ligand
filtering, and rendering return to the Qt main thread.  A cancel request
suppresses rendering but cannot forcibly interrupt an in-progress backend.
Use `async_mode=0` only for deliberate headless/batch execution without a Qt
event loop.

The recorded validation environment completed a real RCSB 1A4W calculation
with PLIP 3.0.0, including Windows PyMOL export, WSL HTTP analysis,
exact-ligand filtering, and Host rendering.  See
[the Host evidence record](docs/validation/windows_host_smoke_2026-07-25.md);
PLIP remains optional.

## Optional WSL worker

The core worker uses only the Python standard library and can start even when
PLIP and PyRosetta are unavailable.  Optional backends must be installed in an
isolated WSL environment using their own supported and licensed distribution
channels.

```bash
python3 scripts/probe_worker_env.py --pretty
export PYMOLPLUGINS_WORKER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
scripts/start_worker.sh
```

Set the same `PYMOLPLUGINS_WORKER_TOKEN` and, if needed,
`PYMOLPLUGINS_WORKER_URL` in the Windows environment inherited by PyMOL.  Then:

```pymol
pp_worker_health
pp_worker_capabilities
pp_rosetta_metrics selection=complex, target_chains=A, binder_chains=B
pp_rosetta_metrics_async selection=complex, target_chains=A, binder_chains=B
```

Do not put the token on a command line or commit it to the repository.  The
startup script requires a token by default and refuses non-loopback binding
without an additional explicit opt-in.  Full setup, networking, license, and
troubleshooting guidance is in [docs/wsl_worker.md](docs/wsl_worker.md).

## Development checks in WSL

The test suite does not require PyMOL:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile path/to/changed_module.py
git diff --check
```

Use the Host checklist for functionality that depends on Windows PyMOL.  Never
claim Host integration from WSL fake-command tests alone.

## Repository layout

- `pymolplugins/`: canonical feature packages and the only implementation
  namespace used by `pp_load`.
- `pymolplugins/core/`: PyMOL-independent parsing, sequence, group metadata,
  and 3D math.
- `pymolplugins/host/`: command manifest, plugin bootstrap, doctor, and help.
- `pymolplugins/worker/`: WSL HTTP application and optional backends.
- `plugins/`: thin PyMOL Plugin Manager entry files.
- `extras/`: optional standalone tools outside the canonical lifecycle.
- `analysis/`, `workflows/`, `visualization/`, `utilities/`, `services/`:
  compatibility paths retained for existing `run` and imports.
- `tests/`: feature-mirrored WSL tests plus the separate Windows Host harness.
- `docs/`: specification, status, deployment, and acceptance documentation.
- `archive/`: historical/experimental scripts, not loaded by `pp_load`.

## Licensing and third-party tools

PyRosetta, ANVIL, MSMS, PLIP, and OpenBabel are separate projects with their own
licenses and supported installation methods.  This repository does not download
or redistribute PyRosetta, ANVIL, MSMS, or credentials.  An adapter in this
repository is not permission to vendor third-party code.  The repository itself
still needs an explicit top-level license before redistribution assumptions can
be made.
