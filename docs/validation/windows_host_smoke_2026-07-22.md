# Windows Host smoke record: PyMOL 3.1.3.1

This is an evidence summary for the automated, headless Host smoke harness. It
does not convert the listed interactive checks into passes and it does not prove
compatibility with every PyMOL 2.x build.

## Environment

- Latest run interval: 2026-07-22 03:08:40.373–03:08:40.787 UTC
  (2026-07-22 11:08:40.373–11:08:40.787 Asia/Shanghai)
- Base commit: `2dd4d087e1cb1a886e3f11c23de73ad3fc5389c6`
- Worktree: dirty implementation worktree; the record corresponds to the files
  present at run time, not only to the base commit
- Windows: Windows 10 build 19045
- PyMOL: 3.1.3.1, build hash
  `698d66e4c6e73b53e79c3954053b380006455f15`
- Host Python: 3.10.16, conda-forge build
- Repository path:
  `\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins`
- Launcher: `scripts/windows/run_host_smoke.ps1`, starting PyMOL from a
  Windows-local temporary current directory
- Harness schema/version: `pymolplugins.host-smoke/1.0`, `1.1.0`
- Worker URL during the run: `http://127.0.0.1:18765`
- Windows PyMOL reached the WSL worker directly through the shared bounded
  client; worker Python was 3.12.13, worker `status` was `ok`, and PLIP 3.0.0
  reported `available=true` while PyRosetta remained unavailable

The full JSON record was written outside the repository at
`C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-host-smoke-3.1-final.json`
and was visible from WSL at
`/mnt/c/Users/ADMIN/AppData/Local/Temp/PymolPlugins-host-smoke-3.1-final.json`.
Its SHA-256 at review time was
`62b5f34908e781bd639f01c6865b94fe37fcae133607ebf00b5bad12f035657f`.

## Automated result

`automatic_status=pass`; `overall_status=automatic_pass_manual_pending`.

| Check | Result | Covered behavior |
| --- | --- | --- |
| `HOST-LOAD-01` | PASS | UNC thin-entry load, idempotent loading of all 10 `pymolplugins.*` canonical modules, inferred `Debian` WSL boundary, required APIs, all 63 canonical commands, and Windows-PyMOL-to-WSL PLIP-capable worker health request. |
| `HOST-FIXTURE-01` | PASS | Nested groups; molecule, static CGO, map, measurement, multistate, ligand/water/metal, and C2/C3/C6 fixtures. |
| `HOST-NAV-01` | PASS | Atomic nested-group navigation and preservation of an unmanaged overlay. |
| `HOST-TARGETS-01` | PASS | Fuzzy single-object resolution plus preview/commit iteration over recursive nested-group leaves, including molecule, CGO, map, measurement, and multistate objects; unrelated overlay preserved. Same-line completion markers detected a swallowed unknown command under `stop` and six swallowed invalid-color `CmdException` failures under `continue`, with zero false executed targets or marker residue. |
| `HOST-ALIGNMENT-01` | PASS | Exact chain-specific sequence selection plus anchor, motif, and ligand preview/commit/undo on real object TTTs. All fits reached zero RMSD; a detached ligand key stayed fixed while its separate payload moved. |
| `HOST-ORIENTATION-01` | PASS | Non-mutating preview and real commit/undo for elongated-object PCA orientation and C3 cyclic orientation. |
| `HOST-GEOMETRY-01` | PASS | C2 paired-rotation, C3/C6 cyclic axes, mass-weighted PCA, explicit vector/two-point axes, provenance-complete JSON fields, and a real 30 A vector-normal membrane-slab CGO with measured plane separation. |
| `HOST-FOLLOW-01` | PASS | Programmatic driver delta, static followers, and undo under `matrix_mode=0/1/2`. |
| `HOST-STYLE-01` | PASS | Scoped style apply and snapshot revert. |
| `HOST-INTERACTION-01` | PASS | Local interaction ligand boundary, manifest output, exact clear, and neighboring-name preservation. |
| `HOST-PLIP-EXPORT-01` | PASS | Real `get_pdbstr` TER-aware serial mapping, 15 unique source identities, explicit/blank-chain ligand identity, no scene names changed, and all export settings restored. |

Each matrix mode recorded `status=pass`,
`movement_source=programmatic set_object_ttt (not mouse)`, and
`static_cgo_verified=true`. Cleanup passed with no errors and removed only the
unique fixture prefix.

## Real PLIP numerical and Host render evidence

The optional PLIP environment was also exercised outside the synthetic smoke
fixture with RCSB 1A4W (2439 exported atoms; 42 atoms in `QWE:H:373`):

- direct `PlipBackend.analyze` and loopback
  `POST /v1/interactions/plip` returned identical canonical results;
- the unfiltered backend result contained 3 sites and 18 interactions;
- a real Windows PyMOL 3.1.3.1 process exported the structure, posted it to the
  WSL worker, validated the returned source identity, retained only the exact
  QWE ligand, and rendered 12 manifest-owned output names;
- the retained `QWE:H:373` site contained 14 interactions: 6 hydrogen bonds,
  4 hydrophobic contacts, 1 pi stack, 1 salt bridge, and 2 water bridges.

The reproducible Host script is `tests/host/plip_e2e.py`. The RCSB 1A4W input
used for the latest run had SHA-256
`11779ea2ad6a87636dd4c86b56660e157a799aef3354e24bbfa991348d473921`.
External evidence files for that run were written to:

- `C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-plip-host-e2e-final-evidence.json`
  (SHA-256 `9b1f986d0c62157b74c56992a97b3b8ee1d87f54add0284004ee623c2dcec9c3`);
- `C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-plip-host-e2e-final-result.json`
  (SHA-256 `d93715477cfd4dc8bc5471a7fc07bfd8b9d30a3360ee7e28d784301b9e14938b`).

This synchronous headless success record does not substitute for the manual Qt
responsiveness/timeout/cancel check.

## Explicitly not passed

- `HOST-MANUAL-MOUSE-01`: real mouse object rotation/translation followed by
  `pp_follow_apply` and undo.
- `HOST-MANUAL-CALLBACK-CGO-01`: a live callback-owned overlay under view
  changes and skip/rebuild policy.
- `HOST-MANUAL-QT-01`: successful remote request, timeout, and cancel while
  observing GUI responsiveness and zero partial output.
- A licensed PyRosetta calculation was not available in this environment.
- A real user-supplied ANVIL executable was not available; only its safe runner
  contract, coordinate validation, and DUM parser are WSL-tested.
- PyMOL 2.x remains a separate compatibility gate; this record is for 3.1.3.1.
