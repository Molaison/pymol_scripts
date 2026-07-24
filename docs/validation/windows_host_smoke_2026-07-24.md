# Windows Host smoke record: PyMOL 3.1.3.1 (2026-07-24)

This is the reviewed evidence summary for the automated headless Host gate. It
does not convert group-follow mouse, callback-owned CGO, remote-request Qt,
licensed PyRosetta, or PyMOL 2.x checks into passes. Structure Annotator fuzzy
completion has a separate visible Host-Qt record below.

## Environment and record

- Run interval: 2026-07-24 12:54:31.077–12:54:31.538 UTC
  (20:54:31.077–20:54:31.538 Asia/Shanghai).
- Base commit: `851f24a`; the record corresponds to the dirty implementation
  worktree present at run time.
- Windows 10 build 19045; PyMOL 3.1.3.1 build
  `698d66e4c6e73b53e79c3954053b380006455f15`.
- Host Python 3.10.16, conda-forge build.
- Repository:
  `\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins`.
- Harness schema/version: `pymolplugins.host-smoke/1.0`, `1.2.0`.
- Worker checking was omitted for this local-state regression run.

The JSON record is outside the repository at
`C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-host-annotator-20260724.json`
(`/mnt/c/Users/ADMIN/AppData/Local/Temp/PymolPlugins-host-annotator-20260724.json`
from WSL). Its reviewed SHA-256 is
`65cf26a1ff01e2b0eab6bb961f55242d2f115b160c7a827abe92e25d4a287405`.

## Automated result

`automatic_status=pass`; `overall_status=automatic_pass_manual_pending`.
Cleanup passed with zero errors. All 12 required checks passed:

| Check | Result | Covered behavior |
| --- | --- | --- |
| `HOST-LOAD-01` | PASS | UNC load, doctor, 10/10 modules, 63/63 canonical commands and STRICT-parser adapters. |
| `HOST-FIXTURE-01` | PASS | Nested groups and molecule/CGO/map/measurement/multistate/scientific fixtures. |
| `HOST-NAV-01` | PASS | Atomic root-group navigation and unmanaged-overlay preservation. |
| `HOST-TARGETS-01` | PASS | Quoted templates/targets, recursive leaf iteration, and parser-error detection. |
| `HOST-ALIGNMENT-01` | PASS | Sequence plus anchor/motif/ligand preview, commit, and undo. |
| `HOST-ORIENTATION-01` | PASS | PCA/cyclic orientation commit and undo. |
| `HOST-GEOMETRY-01` | PASS | C2/C3/C6 and PCA/vector axes plus 30 A membrane slab. |
| `HOST-FOLLOW-01` | PASS | Static follower apply/undo for `matrix_mode=0/1/2`. |
| `HOST-STYLE-01` | PASS | Scoped style snapshot and revert. |
| `HOST-INTERACTION-01` | PASS | Local interaction manifest and exact clear. |
| `HOST-PLIP-EXPORT-01` | PASS | TER-aware atom identity and setting restoration. |
| `HOST-ANNOTATION-STATE-01` | PASS | Dataset-local state, global fuzzy history, hidden directory, and config/cursor restoration. |

## Structure Annotator evidence

The new check loaded a quoted Windows path containing spaces through real
`cmd.do` and used a disposable history override, so it did not modify the Host
user's actual history. It recorded and verified:

- dataset schema `pymolplugins.structure-annotator-state/1.0` at
  `<dataset>\.pymolplugins\structure_annotator.json`;
- global schema `pymolplugins.structure-annotator-history/1.0`;
- Windows hidden attribute on the dataset `.pymolplugins` directory;
- position `{"file": "b.pdb", "index": 1}` and restored file `b.pdb`;
- label `host_review`, values `Keep,Reject`, all preload/boolean options,
  `show cartoon, {object}` On-step, and both bucket commands;
- fuzzy query `cartn` returning `show cartoon, {object}` first.

An initial run exposed `cmd.sync() timed out (wait_queue)` because the annotator
called `cmd.sync` from inside the `sa_load_dir` parser callback that already
owned that command queue. `cmd.load` is synchronous; the redundant self-wait
was removed. This final reviewed rerun completed the annotation check in
132.294 ms with no `wait_queue` timeout.

## Visible Host Qt completion record

The `-cq` PyMOL gate intentionally has no `QApplication`. A separate visible
Qt test therefore ran `tests/host/annotator_qt_smoke.py` with the Python 3.10
and PyQt5 bundled in the same Windows PyMOL installation. It created a native
QApplication and the complete Structure Annotator panel, then used QtTest to
type into the real widgets rather than calling the fuzzy function directly.

The JSON record is at
`C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-annotator-qt-20260724.json`
(`/mnt/c/Users/ADMIN/AppData/Local/Temp/PymolPlugins-annotator-qt-20260724.json`
from WSL). Its SHA-256 is
`7c3f4dea43616f6092a5028f221a425211bf7698baa0a6b9762be17079fcb4f6`.
The schema is `pymolplugins.annotator-qt-smoke/1.0` and `status=pass`:

- the visible panel installed all 5 fixed-field completers and 2 bucket
  completers;
- the latest global snapshot restored its dataset-path hint, label `review`,
  command configuration, and `restore_last_dataset=false` opt-out;
- typing `cartn` automatically opened the multiline popup, whose first result
  was `show cartoon, {object}`; a QtTest mouse click replaced the full field;
- typing `evrythng` automatically opened the bucket popup, whose first result
  was `hide everything, {object}`; Down/Return replaced the full line edit.

Reproduce from WSL with the Host executable (run from a Windows-local cwd):

```bash
'/mnt/d/Softwares/Pymol3.1/python.exe' \
  '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\tests\host\annotator_qt_smoke.py' \
  --repository '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins' \
  --output 'C:\Users\ADMIN\AppData\Local\Temp\PymolPlugins-annotator-qt-20260724.json'
```

## Remaining interactive gates

- Close/reopen of a visible panel across a fresh GUI session, including the
  **Restore last dataset** opt-out.
- Real mouse group-follow movement, callback-owned overlay behavior, and Qt
  remote-request responsiveness/cancel.
- Licensed PyRosetta, user-supplied ANVIL, and PyMOL 2.x compatibility.

The exact manual Structure Annotator procedure is in
[windows_host_acceptance.md](../windows_host_acceptance.md).
