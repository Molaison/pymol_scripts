# Windows Host / CSV / cache smoke record: PyMOL 3.1.3.1 (2026-07-25)

This is the reviewed evidence summary for the CSV Structure Annotator change.
It combines the automated Windows PyMOL gate, a real PyQt5 widget smoke, a real
Windows `K:` FUSE/cache run, and an authenticated real WSL staging run. It does
not convert PyMOL 2.x, licensed PyRosetta, user-supplied ANVIL, real mouse group
follow, or production-scale CSV performance into passes.

## Environment and source state

- Final Host run interval: 2026-07-24 19:33:49.003–19:33:49.598 UTC
  (2026-07-25 03:33:49.003–03:33:49.598 Asia/Shanghai).
- Base commit: `55492a3`; the record corresponds to the dirty CSV/cache/staging
  implementation worktree that is finalized by the containing commit.
- Windows 10 build 19045; PyMOL 3.1.3.1 build
  `698d66e4c6e73b53e79c3954053b380006455f15`.
- Host Python 3.10.16, conda-forge build; Qt smoke binding PyQt5.
- Repository:
  `\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins`.
- Host harness schema/version: `pymolplugins.host-smoke/1.0`, `1.3.0`.
- Final WSL full suite: `Ran 333 tests in 10.390s`, `OK`.
- Python compile, `sh -n scripts/start_worker.sh`, and `git diff --check` passed.

## Automated Windows PyMOL result

Reproduction command from WSL:

```bash
'/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe' \
  -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass \
  -File '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\scripts\windows\run_host_smoke.ps1' \
  -PymolExe 'D:\Softwares\Pymol3.1\Scripts\pymol.exe' \
  -RepositoryPath '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins'
```

`automatic_status=pass`; `overall_status=automatic_pass_manual_pending`.
Cleanup passed with zero errors. All 13 required checks passed:

| Check | Result | Duration (ms) | Covered behavior |
| --- | --- | ---: | --- |
| `HOST-LOAD-01` | PASS | 209.479 | UNC load, doctor, 10/10 modules, 63 canonical commands. |
| `HOST-FIXTURE-01` | PASS | 4.582 | Nested/multitype and scientific fixtures. |
| `HOST-NAV-01` | PASS | 10.382 | Atomic nested-group navigation. |
| `HOST-TARGETS-01` | PASS | 10.283 | Object/group fuzzy resolution and foreach. |
| `HOST-ALIGNMENT-01` | PASS | 9.942 | Sequence and anchor/motif/ligand alignment. |
| `HOST-ORIENTATION-01` | PASS | 4.063 | PCA/cyclic orientation commit and undo. |
| `HOST-GEOMETRY-01` | PASS | 4.283 | Cyclic/PCA/vector axes and membrane slab. |
| `HOST-FOLLOW-01` | PASS | 20.352 | Follow apply/undo under matrix modes 0/1/2. |
| `HOST-STYLE-01` | PASS | 8.240 | Style apply/revert snapshot. |
| `HOST-INTERACTION-01` | PASS | 10.508 | Local interactions and exact manifest clear. |
| `HOST-PLIP-EXPORT-01` | PASS | 1.672 | PDB atom identity and setting restoration. |
| `HOST-ANNOTATION-STATE-01` | PASS | 129.417 | Directory state and global fuzzy history. |
| `HOST-ANNOTATION-CSV-01` | PASS | 170.281 | CSV metadata, row annotations, export, and restore. |

The JSON record is outside the repository at
`C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-host-smoke-20260725_033348.json`.
Its reviewed SHA-256 is
`03be7b9cf61906858291b3c2b00e3cc441efa183b806ad293962ffbf05a86948`.

## CSV Structure Annotator evidence

`HOST-ANNOTATION-CSV-01` used real `cmd.do` and Windows-local paths containing
spaces. Two rows deliberately referenced the same PDB but received different
stable entry IDs and independent annotations. The check verified:

- state schema `pymolplugins.structure-annotator-state/2.0`;
- sidecar schema `pymolplugins.structure-annotator-annotations/1.0` with two
  row-specific events;
- source columns `structure_path,case,iptm,note` plus exactly one
  `pymol_annotation_host_csv_review` export column;
- restored second-row entry ID/cursor after reloading the CSV;
- source CSV SHA-256
  `a283cdcfd98c967bed9725ed939da369237371be3a470cbeb4f58612c922b915`;
- source PDB SHA-256
  `9c7e0b8369dee8a8e281a1b36d87198e433264cff430fa82ad2a3a92d788efe6`;
- both source hashes remained unchanged after annotation, export, and restore.

The state and annotation files were under the CSV's hidden `.pymolplugins`
directory. The merged output used another path and was removed with the
disposable fixture.

## Real Host PyQt5 record

Reproduction command:

```bash
'/mnt/d/Softwares/Pymol3.1/python.exe' \
  '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\tests\host\annotator_qt_smoke.py' \
  --repository '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins' \
  --output 'C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-annotator-qt-20260725.json'
```

The schema `pymolplugins.annotator-qt-smoke/1.0` returned `status=pass`.
The real panel was visible, installed nine fixed and two dynamic bucket
bindings, opened both fuzzy popups, accepted the complete On-step candidate by
mouse and bucket candidate by keyboard, restored the saved opt-out/config, and
recorded `preview_click_navigation_calls=[]` after clicking a CSV preview row.

The record SHA-256 is
`567b4ecb5a3dee84ea597763e11701b0f3b6c5f006150e8bb3b606fde75fec6a`.

## Real `K:` FUSE mapping and Windows-local cache

The source was `K:\gitrepo\BindCraft\example\PDL1.pdb`; Windows reported the
drive filesystem as FUSE. Source size was 75,609 bytes and SHA-256 was
`fa5e19c75e690a37759d11e217d8a9b937e3e9c8e63728fc4af272e56e4a8163`.

The Windows-bundled PyMOL Python called the annotator's materialization layer
directly. All three default aliases resolved to that exact Host path:

```text
/xcfhome/zpzeng/mount_local/gitrepo/BindCraft/example/PDL1.pdb
/xcfhome/zpzeng/gitrepo/BindCraft/example/PDL1.pdb
~/gitrepo/BindCraft/example/PDL1.pdb
```

They shared cache key
`7207e8d7aa6e35aeed1b5401d60c2361f0dec2892ef3023940e726a376c874ec`:
the first call was cold at 8.846 ms, followed by warm hits at 3.558 and
3.060 ms. Two direct materialization calls for `K:\...` independently proved
that the configured remote drive uses a persistent cache: cold 8.382 ms, warm
3.119 ms, stable key
`be77719308ee2d38edf8a3cfb2791d753371b2ae044924baa78e8ef350f624cc`.

Every materialized path was under a Windows-local `C:` temporary cache. The
source SHA-256, size, and mtime were identical afterward, and the disposable
cache was removed.

## Real authenticated WSL staging

The foreground worker used `.venv-worker/bin/python`, URL
`http://127.0.0.1:18765`, root alias `structures`, and an isolated root below
`/tmp`. Bearer authentication and staging were both enabled. The 62,712-byte
source SHA-256 was
`fedf9fe11bc67f7f0022d41412a46012fa5fee71c2f1da3c7b2b612ee4c466fc`.

The real request log was exactly:

```text
POST /v1/stage/stat 200
POST /v1/stage/file 200
POST /v1/stage/stat 200
```

The cold lookup/materialization took 23.168 ms and returned `cache_hit=false`;
the second stat/lookup took 4.110 ms and returned a warm hit. A newly reopened
`PersistentStructureCache` also hit the same key
`4f0e75495e7a66efa3e1550bf1ed4766140fb7ccdc62ccf03bea5db38308117f`.
The identity preserved:

```text
provider:         wsl-stage:structures
canonical_source: structures/nested/sample.pdb
version_token:    v1-bfae561d7b1fc6c1b40586d40fc8fc48322af167327925a8316839b320f92ddd
```

The opaque token is the worker's SHA-256 digest of
device/inode/size/mtime/mode metadata, not a file-content hash. The independent
source SHA-256 comparison above verified content for this run; stat-based cache
freshness cannot detect an in-place rewrite that preserves every identity
field.

The cached/source hashes matched. The worker exited normally on `Ctrl-C`, port
18765 had no listener afterward, and the root/download/cache temporary
directories were removed. Unit tests separately cover wrong Bearer, missing
files, traversal/symlink escape, non-regular files, size/deadline bounds, bad
response identity, and atomic partial-download cleanup.

An attempted direct import of `pymolplugins.annotation.plugin` under ordinary
WSL Python correctly failed with `ModuleNotFoundError: pymol`; that controller
belongs to Windows PyMOL. The successful real WSL E2E therefore exercised the
PyMOL-independent staging/client/cache/materializer chain, while Windows Host
and fake-PyMOL tests cover controller integration. A controller regression test
also verifies that CSV load reuses the fresh `stage/stat` returned by path
resolution for immediate preflight rather than issuing a redundant second
stat. A Preview request has its own read-only stat, and revisiting the same
entry in one session is memoized rather than performing another cache lookup.

## Independent review and remaining gates

The final independent review reran 108 annotation tests plus targeted cache
identity/direct-`K:`, export/re-import, tri-state source configuration, future
finalization, staging missing, and scene rollback checks. It reproduced no
previous issue and found no new blocker.

Still open:

- PyMOL 2.x compatibility;
- human inspection and responsiveness measurement for production-sized CSVs;
- fresh-process GUI restore opt-out;
- real mouse group follow and callback-owned overlays;
- real ANVIL and legally installed PyRosetta numerical validation.

The one-by-one procedure is in
[structure_annotator_csv_walkthrough.md](../structure_annotator_csv_walkthrough.md),
and the broader manual gate remains
[windows_host_acceptance.md](../windows_host_acceptance.md).
