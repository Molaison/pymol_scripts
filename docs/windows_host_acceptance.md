# Windows Host PyMOL Acceptance Checklist

## 2026-09-29 当前交付

0.10.0最终ZIP已通过真实临时Plugin Manager安装。原14组命令检查及新增语法检查
已取得分项通过证据；修复项在真实PyMOLQtGUI主线程验收。两离散状态30原子显示/撤销、
引号正则、分号与失败完成标记通过；共享批处理56对/112输出，坐标误差0。
完整矩阵、原始失败、最终ZIP校验值和未验证项见
[本轮执行记录](validation/01_sampling_reference_2026-09-29.md#最终交付与真实证据)。

使用 `just test-host-commands` 直接调用既有Windows Python/PyMOL，
不需要PowerShell脚本或永久安全策略变更。它默认打开真实Qt窗口，
可指定检查ID；原来的PowerShell入口及下文历史记录保留各自的历史边界。
安装复现：`just test-package`。600行实际RNA审阅已有独立证据，不随命令补验重复运行。

## 历史检查边界

This checklist is the required integration gate for behavior that WSL pure/fake
tests cannot prove.  `tests/host/host_smoke.py` and
`scripts/windows/run_host_smoke.ps1` now automate the headless subset with
generated fixtures.  Mouse object movement, callback-owned CGO, interactive Qt,
real ANVIL, and licensed PyRosetta remain manual except for the explicitly
scripted Structure Annotator PyQt5 widget checks. Do not extend an automated
result beyond what its versioned JSON record actually tested.

The latest reviewed record is
[the PyMOL 3.1.3.1 smoke summary](validation/windows_host_smoke_2026-07-25.md).
PyMOL 2.x remains a separate compatibility gate.

Version 0.7.0 has a separate [workspace window-mode record](workspace_window_modes.md).
`tests/host/workspace_window_smoke.py --output <modes.json>` exercises native
docking, floating, tabification, state-preserving switches during real work,
close guards, settings errors and remembered geometry in the actual desktop.
Use isolated `PYMOLPLUGINS_UI_SETTINGS`; floating content must be resized through
its host. The record includes explicit native framebuffer repaints, compact
viewport bounds, exact ZIP checks and isolated Plugin Manager installation.
It does not claim PyMOL 2.x compatibility or a fix for native shutdown callbacks.

Structure Annotator 0.5.0 has a separate
[current GUI record](validation/structure_annotator_layout_2026-09-16.md).
Run `tests/host/annotator_layout_smoke.py --output <layout.json>` with the
bundled Windows Python from a Windows-local working directory. It exercises
visible controls, staged choices, explicit save/advance, batch/menu undo,
required fields, filter reset, and 1000 x 720 / 760 x 560 geometry with long
names and six scores. It also captures and checks the actual OpenGL framebuffer.
For the bundled PyQt5 `QGLWidget`, use `grabFrameBuffer()`; QWidget `grab()` does
not capture that GL buffer. Write JSON before `cmd.quit` and verify both the
terminal exit and the expected artifacts. This GUI gate does not extend the
canonical-command or version-compatibility evidence below.

That record covers the former 63-command surface. Harness 1.5.0 adds the eight
task facades, Unicode/task help, and a state-1 atom-representation undo round
trip under `HOST-TASKS-01`; it does not yet cover the newer all-state snapshot,
hidden object-identity marker, or asynchronous backend-failure history gates.
It must be extended and rerun before those additions are called Host-verified;
WSL/fake-command success is supporting evidence only.

The 2026-07-27 current-revision launcher attempt was blocked before PowerShell
or PyMOL started: WSL reported `UtilConnectUnix:514 connect failed 1` followed
by `UtilBindVsockAnyPort:287 socket failed 1`. This is a launcher-environment
blocker, not a Host pass and not evidence about the harness assertions.

## Runtime boundary

- PyMOL and Qt run on the Windows Host.
- Repository scripts may be read from a Windows path or
  `\\wsl.localhost\<distro>\...`.
- Any PowerShell/cmd automation must start with a normal Windows working
  directory; an UNC script path is not permission to use an UNC current working
  directory.
- WSL test results are supporting evidence only.
- Remote PLIP/Rosetta tests use the HTTP worker and inline structure text.  The
  Host must never execute a `/home/...` interpreter path.

## Test record header

Create one record per PyMOL build/environment:

```text
Record ID:
Date/time/timezone:
Tester:
Git commit and dirty-worktree note:
Windows edition/build:
PyMOL version/build/channel:
Host Python and Qt versions:
GPU/driver:
WSL distro/version/kernel:
Repository path as seen by Windows:
Worker URL/auth enabled:
PLIP/PyRosetta versions (or unavailable):
```

Use a disposable PyMOL session.  Save screenshots, console logs, exported JSON,
and timings outside the source tree or under a clearly identified test-artifact
directory.  Never include the worker token or licensed binaries.

Status values are `PASS`, `FAIL`, `BLOCKED`, and `NOT RUN`.  `BLOCKED` requires a
specific missing dependency/capability, not merely an untested case.

## 1. Bootstrap and capability baseline

1. Start PyMOL from a normal Windows working directory.
2. Load `plugins/pymol_plugins.py` through Plugin Manager.
3. Run:

   ```pymol
   pp_load
   pp_doctor
   pp_help
   ```

4. Record all load errors, missing PyMOL APIs, repository path handling, and
   worker status.
5. Confirm `missing_commands=[]`, inspect the categorized `pp_help` inventory,
   and verify that loading the plugin twice does not
   change their signatures or produce duplicate menu entries/tracebacks.

Expected: local canonical modules load even when the optional worker/backends
are unavailable.

## 2. Required fixtures

The record must identify how each fixture was produced and preserve a small
reproducible copy where licensing permits:

- molecule object with non-identity object TTT;
- multi-state molecule;
- nested root/child group;
- static CGO, measurement, and map objects inside a group;
- callback-owned axes overlay outside or inside a test group;
- complex containing protein and exact ligand selection;
- blank chain, insertion code, multiple ligands, water, and metal;
- synthetic C2, C3, and C6 oligomers;
- objects with sufficiently long names to exercise output-name limits.

The automated harness generates these fixtures in a unique namespace and
cleans that exact namespace afterward.  Re-run with
`PP_HOST_SMOKE_KEEP_FIXTURES=1` (or launcher `-KeepFixtures`) when a manual check
needs to inspect them.

Example launcher from WSL:

```bash
'/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe' \
  -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass \
  -File '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\scripts\windows\run_host_smoke.ps1' \
  -PymolExe 'D:\Softwares\Pymol3.1\Scripts\pymol.exe' \
  -RepositoryPath '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins' \
  -WorkerUrl 'http://127.0.0.1:8765'
```

Omit `-WorkerUrl` when no worker is running.  When supplied, the gate requires
Windows Host PyMOL itself—not only PowerShell or WSL curl—to receive a valid
worker health schema.

## 3. Navigation (`NAV-*`)

Test file, object, and group sources separately.

| Check | Required action and expected result | Status/evidence |
| --- | --- | --- |
| NAV-H01 | Start root-group navigation with molecule/CGO/map members. The entire root group is one entry. | |
| NAV-H02 | Step forward/backward across a short final batch with wrap on and off; window/stride remain symmetric. | |
| NAV-H03 | Pin a group; every leaf remains visible while unrelated overlays retain their prior enabled state. | |
| NAV-H04 | In file mode, induce one load failure. Cursor/managed state rolls back and user objects are not deleted. | |
| NAV-H05 | Start with `bind_keys=1`, record prior arrow bindings, stop, and verify exact restoration. If a third party changes a binding during the session, the newer binding is preserved. | |
| NAV-H06 | Load compatibility `file_management.py`; confirm it delegates to the same session and does not install generic `next`/`prev`/`pin`. Do not load `screen_boost.py` in this canonical test. | |

Run `pp_nav_stop` and confirm pre-session enabled states are restored for
existing-object/group sessions.

For manually loaded navigation compatibility adapters, use a separate fresh
session: load only one of `screen_boost.py` or `file_management.py`, confirm its
callbacks carry the shared console adapter, and pass a quoted path plus strict
`0|1` booleans. An invalid boolean must produce one actionable line without a
traceback; an injected internal failure must retain diagnostics. With sibling
`same.pdb` and `same.cif`, save both selections and verify distinct stable
`same__pdb.pdb` / `same__cif.pdb` files and correct `exclude_selected` behavior.

### Object/group target iteration (`TARGET-*`)

1. Run `pp_target_resolve` with exact, glob, contains, regex, and an intentional
   misspelling under `match=fuzzy`; record actual names, methods, and scores.
2. Preview `pp_foreach test_group, "disable {target}", group_mode=leaves, commit=0` and
   confirm no object changed.
3. Repeat without `commit=0`; molecule, static CGO, map, measurement, and multistate
   leaves must all receive exactly one command.
4. Confirm an unrelated overlay remains enabled, then restore the fixtures.
5. Test `group_mode=atomic` for a command that accepts a group name and verify
   it executes once rather than once per atom/member.
6. Run an unknown command with `on_error=stop` and a command that raises
   `CmdException` with `on_error=continue`; verify neither is listed under
   `executed_targets`, error counts follow the policy, and no `ppfe_ack_*`
   selection remains.

The current `HOST-TARGETS-01` rerun must cover steps 1–4 and 6; earlier Host
records validated the prior explicit-commit contract only. `pp_foreach` now
executes by default. Arbitrary command templates have no generic undo, so risky
operations should first be run with explicit `commit=0`.

## Tier-1 task facades (`TASK-*`)

Harness 1.5.0 registers `HOST-TASKS-01`. In a disposable session it must:

1. run `pp_show surface, TEST_GROUP`, then `pp_undo OPERATION_ID`, and compare
   every molecule atom's `reps` bitmask in every state before/after. Confirm CGO,
   map, and measurement leaves were not representation targets. On a Host with
   object-property APIs, confirm commit stored the hidden identity marker and
   undo refuses a deleted/recreated same-name object;
2. run group navigation through `pp_nav groups, "A,B", wrap=0`, confirm a non-wrapping
   `next-page` boundary, then confirm `pp_nav next, window=2` is rejected without
   changing the scene before `pp_nav stop`;
3. run `pp_align SOURCE, SOURCE, preview=1`, `pp_orient SOURCE, preview=1`, and
   `pp_contacts SOURCE, focus=EXACT_LIGAND, preview=1`; prove no transform or
   interaction-manifest mutation;
4. run default `pp_metrics SOURCE, target=A, binder=B` and verify it submits
   without a synchronous capabilities request on the GUI thread. Inspect it with
   `pp_jobs`; an unavailable or failed backend must become a recent terminal
   history entry with actionable diagnostics. Test `wait=1` separately as the
   deliberate blocking/preflight path;
5. run `pp_jobs` and `pp_undo list` with bounded human output; verify
   `pp_jobs cancel, JOB_ID` is described as result suppression, not backend
   termination;
6. send quoted commands through `cmd.do`, and verify all eight callbacks carry
   the shared console adapter;
7. query `pp_help "ligand contacts"`, `pp_help "group batch"`, `pp_help 导航`,
   `pp_help 表面`, `pp_help 对齐`, and `pp_help 配体接触`; exact/full-token intent
   must rank first. Then query the English typo `pp_help aligment` and Chinese
   typo `pp_help 结构对其`; both must return the `align` task with
   `matched_by=fuzzy`. Natural queries `pp_help "browse groups"`,
   `pp_help "sequence selection"`, `pp_help "axis align"`, and
   `pp_help "ligand contact"` must route to navigation, sequence-motif,
   orientation, and interaction tasks respectively. `pp_help "!!!"` and
   `pp_help foobar` must return `no_match`, not the default guide or an unrelated
   low-confidence command.

Failure or stale undo must retain a retryable operation. Each `pp_undo` result
must name the exact canonical delegate/reference stored in the journal. A
`force=1` test may overwrite only a representation/transform changed after
commit; it must still refuse a replaced/missing source object which cannot be
recreated.

## 4. Structure Annotator persistence (`ANNO-*`)

Use a disposable directory containing at least two structures and a space in
its path.

1. Load `plugins/structure_annotator.py`, open the panel, and load the dataset.
2. Set a non-default label/values, `On step` command, two bucket commands,
   preload options, and move to the second file. Run `sa_save_state`.
3. Verify `<dataset>/.pymolplugins/structure_annotator.json`; on Windows the
   directory must also carry the hidden attribute. `sa_state_status` must show
   the same config and relative filename/index.
4. Close and reopen PyMOL, then open the panel. The same dataset, config, and
   file must restore automatically. Confirm no prior undo entry is restored.
5. Type misspellings such as `cartn` into On step and `evrythng` into a bucket
   command. The popup must offer the previously finalized full commands; use
   keyboard and mouse acceptance once each.
6. Run `sa_history_status` and `sa_history_search step_cmd, cartn`. Confirm the
   global path is under the Windows Host user's home, not WSL, unless
   `PYMOLPLUGINS_ANNOTATOR_HISTORY` was deliberately overridden.
7. Clear **Restore last dataset when this panel opens**, save/close, and verify
   the next panel open restores config/path without automatically loading it.

The automated `HOST-ANNOTATION-STATE-01` covers quoted-path loading, both JSON
schemas, Windows hidden state, cursor/config/command restoration, and fuzzy
ranking with a temporary global history. The separate visible
`tests/host/annotator_qt_smoke.py` covers step 5 with real Host PyQt5 widgets,
including automatic popup visibility plus mouse/keyboard acceptance. Steps 4
and 7 remain fresh-session gates. Only use dataset state from trusted directories: restored
On-step and bucket templates are executable PyMOL commands.

### CSV, score table, path mapping, and cache (`ANNO-CSV-*`)

Use a disposable CSV with a unique ID column, several numeric/text score
columns, two rows that deliberately reference the same structure, one missing
path, and spaces in both the CSV and structure paths. Record the source CSV and
PDB SHA-256 before testing.

1. In **Open data**, choose **CSV table**, select the file, enter the exact
   structure and row-ID headers, then open **File locations > Check file paths**.
   Confirm valid/missing statuses and metadata. Click a preview row and confirm
   PyMOL does not navigate or replace the previously active dataset.
2. Snapshot state/history/annotation/mapping files immediately before Preview.
   Confirm Preview created no state/history/annotation data, left an existing
   mapping file byte-identical, and changed no managed object. Panel creation
   also uses read-only mapping lookup. Then click **Open CSV** and inspect the
   review workspace. **Scores…**, **Filter**, and **More actions > Structure
   details…** expose numeric scores/sort, filtering, annotation, raw/resolved
   paths, provider, materialized path and cache-hit information.
3. Choose a value, confirm that no annotation is written before Save, and save
   the two duplicate-path rows with different values. Undo one, and
   annotate it again. Confirm the source CSV remains byte-identical and the
   hidden JSONL sidecar contains row-ID-specific events.
4. Export to a new CSV. Confirm the header contains exactly one
   `pymol_annotation_<label>` column, source rows/scores are preserved, and the
   exported file can be loaded as a new CSV source. Export to the source path
   must be refused.
5. Set a filter/sort, navigate away from row 1, save state, switch to a different
   CSV with different parser settings, then return. Each CSV must restore only
   its own parser/mapping/staging/view/cursor state. Explicit empty ID/base/stage
   values must remain empty; empty display-columns means auto, while JSON `[]`
   explicitly clears every display column.
6. With real `K:` FUSE/SSHFS available, test all default `x_k` spellings plus
   two independently identified duplicate direct `K:\...` rows. Enable
   persistent cache. The first mapped alias should copy to a Windows-local
   cache and the other aliases should hit the same key; the first direct row is
   cold under its provider and the second direct row is warm. Revisiting one
   identical entry only uses session memoization and is not a cache re-check.
   Source bytes/size/mtime must not change.
7. For a WSL-only file, start the authenticated worker with
   `--stage-root structures=<absolute-root>`. Under **File locations**, select
   worker file access, put that root in **Server root** and `structures` in
   **Worker root alias**. First use at least two unique staged paths plus a
   duplicate row: Preview/Load should issue bounded `stat-batch` requests,
   preserve per-row missing/error status, and count the duplicate path once.
   Then use two IDs with the same staged path. Preview produces its own single
   `stat`; record it separately. From a clean observation
   baseline, first Load should produce `stat -> file`, and navigating to the
   duplicate row (or reloading the CSV) should add the warm `stat`. The
   controller reuses load's fresh resolve identity for preflight, so there is no
   duplicate preflight stat. Revisiting the same entry is memoized and produces
   no request. A wrong token/missing row must be rejected without replacing the
   active scene.
8. Close/reopen the panel in the same process and confirm live data and cursor
   remain available. A fresh PyMOL process opens Open data without restoring
   history. Use Recent configurations to restore or restore-and-load explicitly;
   confirm the chosen configuration takes precedence over newer sidecar settings.

`HOST-ANNOTATION-CSV-01` automates the Windows-local subset of steps 1-5 and 8
through real `cmd.do`; `annotator_qt_smoke.py` automates preview-source
isolation plus background prepare heartbeat, cancel/late-result suppression,
panel-close registry cleanup, and main-thread Preview/Load completion in real
PyQt5. The reviewed 2026-07-25 record separately captures a real `K:` FUSE
cold/warm run and a real WSL core staging request sequence.
The [2026-07-26 optimization record](validation/structure_annotator_optimization_2026-07-26.md)
adds a real mixed-result batch worker run and a 50,000 x 8 Windows-Qt RSS/
cursor benchmark. Windows-PyMOL-to-WSL annotator staging, human visual review
with a production dataset, and PyMOL 2.x remain open gates.

## 5. Object matrices and group follow (`FOLLOW-*`)

Repeat the core test under each supported `matrix_mode` value 0, 1, and 2.  If a
mode is unsupported by the installed PyMOL, record the exact behavior rather
than coercing it silently.

1. Put driver, molecule follower, static CGO, measurement, and map in one group.
2. Give driver and followers different non-identity baselines.
3. Run `pp_follow_arm driver, group=test_group`.
4. Enter PyMOL object-movement mouse mode and apply both rotation and
   translation to the driver.  This must be actual mouse/object manipulation,
   not only a scripted `set_object_ttt` substitute.
5. Run `pp_follow_apply`.
6. Verify the driver was not transformed twice and all static followers retained
   their relative placement.
7. Apply again, rebase, then undo.  Verify baseline matrices numerically.
8. Modify one follower externally after arm; apply must reject the stale state
   unless explicitly allowed.
9. Include the callback overlay with skip policy and confirm a deterministic
   skip/warning rather than an overwritten viewport transform.

Record before/after TTT matrices and a screenshot.  This is the gate for
`FOLLOW-01` through `FOLLOW-06` and static-CGO `MEM-02` behavior.

## 6. Sequence selection and rigid alignment (`SEQ-*`, `ALIGN-*`)

| Check | Expected result | Status/evidence |
| --- | --- | --- |
| SEQ-H01 | Exact motif selections identify atom indices correctly for blank/multi-character chain IDs, `42A`, negative resi, altloc, missing CA, and modified residues. | |
| SEQ-H02 | Overlap, multi-object, unique ambiguity, and bounded-gap searches return deterministic structured results. | |
| ALIGN-H01 | Anchor alignment preview changes nothing; commit moves only the explicit payload; undo restores all payload baselines. | |
| ALIGN-H02 | Motif residues define the fit while the requested whole complex/group moves rigidly. | |
| ALIGN-H03 | A detached ligand/key object can be the anchor without becoming the implicit move payload. | |
| ALIGN-H04 | Duplicate ligand atom names/symmetric ambiguity, collinearity, or reflection are rejected or explicitly reported. | |

Automated `HOST-ALIGNMENT-01` covers a chain-specific exact sequence selection,
anchor and motif preview/commit/undo, and a detached ligand-key fit which moves
a separate payload while leaving the key object fixed. The ambiguity and
degenerate failure cases remain deterministic WSL tests.

Record exported alignment JSON, RMSD before/after, moved object names, and
pairwise-distance preservation.

## 7. Geometry and membrane (`GEO-*`, `MEM-*`)

1. Compute PCA on an elongated structure and orient its long axis to X/Y/Z,
   including an antiparallel target case.
2. Confirm low-confidence/degenerate PCA requires explicit force.
3. Compute axes for synthetic C2, C3, and C6 fixtures; confirm C2 uses its
   distinct paired-rotation path.
4. Check a nonuniform/helical arrangement is not falsely accepted as a clean Cn
   ring.
5. Draw a membrane slab from PCA, cyclic axis, explicit vector, and stored axis.
6. Measure that plane separation equals thickness and extents cover projected
   coordinates.
7. Parse a reviewed ANVIL output with two DUM layers; verify center/normal and a
   deterministic failure for a missing layer.
8. Include the slab CGO in the group-follow matrix test.

Automated `HOST-ORIENTATION-01` covers non-mutating preview plus real object-TTT
commit/undo for an elongated PCA fixture and a cyclic C3 fixture. The automated
geometry check separately covers C2/C3/C6 axes and a numeric 30 A slab.

Save axis/membrane JSON and screenshots with the axis object visible.

## 8. Styles (`STYLE-*`)

Build a scene with multiple representations, atom colors, scoped settings, and
a non-default background.

```pymol
pp_style_apply publication_light, selection=test_object, snapshot=1, render=0
pp_style_revert
```

Verify:

- apply does not invoke ray, PNG export, or zoom;
- only the requested selection is restyled where the preset is scoped;
- revert restores global/scoped settings, background, atom colors, and
  representations;
- nested snapshots enforce LIFO behavior;
- `pp_style_render` is the explicit ray/PNG path;
- loading legacy style wrappers in different orders leaves identical command
  signatures.

Record before/apply/revert screenshots and the render output metadata.

## 9. Interactions and worker (`INT-*`, `ROS-*`)

### Local canonical presets

Run `ligand_quick`, `ligand_site`, and `interchain_basic` on reviewed fixtures.
Confirm the exact ligand is one endpoint of ligand interactions, source objects
are never put inside result groups, JSON/summary/visual counts agree, and clear
deletes only manifest names.

Also exercise blank chain, insertion code, multiple ligands, multi-state input,
water/metal, long names, zero interactions, and an induced renderer failure.

### WSL worker

1. With optional backends absent, `pp_worker_health` must return a healthy worker
   and `available=false` per backend.
2. Verify missing/wrong token, invalid schema, service unreachable, and timeout
   produce typed errors without partial PyMOL output.
3. Inspect the request boundary: only PDB text, partners, metrics, and options;
   no path conversion or shell/XML field.
4. With PLIP installed, run one reviewed protein-ligand complex through the
   complete Windows PyMOL export -> WSL POST -> exact-ligand filter -> Host
   render path, and record canonical type/site counts plus manifest names.
5. In a licensed PyRosetta environment, record initialization count, backend
   versions/flags, input SHA256, selectors/effective cutoffs, and repeatability.
6. Keep CMS, bound heavy-atom buried unsats, and interface-delta unsats as
   separate fields and definitions.

Canonical PLIP-over-WSL and Rosetta GUI calls now use a Qt worker thread for
HTTP only.  Export occurs before submission; canonical validation and rendering
return to the main thread.  `INT-07` is still a manual Host gate until a tester
records GUI responsiveness, success, timeout, cancel-result suppression, and
zero partial output.  Cancellation is not hard backend interruption.

## 10. Performance and lifecycle

Measure `pp_follow_apply` with 100 and 1000 static followers after one warm-up.
Record elapsed time, PyMOL responsiveness, object types, and hardware.  No fixed
threshold is specified yet; preserve the baseline for regression comparison.

Then verify:

- a second `pp_load` is idempotent;
- stop/undo/clear commands leave no toolkit-created names outside their exact
  records;
- user objects, selections, key bindings, settings, and camera survive cleanup;
- closing/reopening PyMOL does not require stale WSL/Windows paths.

## 11. Result summary

Complete this table in the test record:

| Area | Acceptance IDs | Status | Evidence / issue link |
| --- | --- | --- | --- |
| Bootstrap/doctor | experience §12.1 | | |
| Navigation | NAV-01..06 | | |
| Structure Annotator state/history | ANNO-01..04 | | |
| Structure Annotator CSV/path/cache | ANNO-CSV-01..09 | | |
| Group follow | FOLLOW-01..06 | | |
| Sequence | SEQ-01..02 | | |
| Alignment | ALIGN-01..04 | | |
| Geometry | GEO-01..03 | | |
| Membrane | MEM-01..03 | | |
| Styles | STYLE-01..05 | | |
| Interactions | INT-01..07 | | |
| Rosetta service | ROS-01..05 | | |

Any `FAIL`, `BLOCKED`, or `NOT RUN` keeps the corresponding phase open.  Syntax
checks and WSL tests cannot change a Host-only item to `PASS`.
