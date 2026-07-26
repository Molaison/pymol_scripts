# Windows Host PyMOL Acceptance Checklist

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

### Object/group target iteration (`TARGET-*`)

1. Run `pp_target_resolve` with exact, glob, contains, regex, and an intentional
   misspelling under `match=fuzzy`; record actual names, methods, and scores.
2. Preview `pp_foreach test_group, "disable {target}", group_mode=leaves` and
   confirm no object changed.
3. Repeat with `commit=1`; molecule, static CGO, map, measurement, and multistate
   leaves must all receive exactly one command.
4. Confirm an unrelated overlay remains enabled, then restore the fixtures.
5. Test `group_mode=atomic` for a command that accepts a group name and verify
   it executes once rather than once per atom/member.
6. Run an unknown command with `on_error=stop` and a command that raises
   `CmdException` with `on_error=continue`; verify neither is listed under
   `executed_targets`, error counts follow the policy, and no `ppfe_ack_*`
   selection remains.

The automated `HOST-TARGETS-01` covers steps 1–4 and 6. Arbitrary command templates
have no generic undo, so preview-before-commit is part of the public contract.

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

1. In **Source**, choose **CSV + scores**, select the file, enter the exact path
   and ID headers, select display columns, then click **Preview mapping**.
   Confirm valid/missing statuses and metadata. Click a preview row and confirm
   PyMOL does not navigate or replace the previously active dataset.
2. Snapshot state/history/annotation/mapping files immediately before Preview.
   Confirm Preview created no state/history/annotation data, left an existing
   mapping file byte-identical, and changed no managed object. Panel creation
   itself may initialize/repair the mapping store before this baseline. Then
   click **Load CSV** and inspect the **Scores** tab: numeric sort, substring
   filter, active annotation column, raw/resolved path, provider, materialized
   path, and cache hit must be visible/correct.
3. Annotate the two duplicate-path rows with different values, undo one, and
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
   `--stage-root structures=<absolute-root>`. Put that root in **WSL root** and
   `structures` in **alias**. First use at least two unique staged paths plus a
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
8. Close/reopen the panel and then a fresh PyMOL process. Confirm source mode,
   CSV path, scores view, annotation values and cursor restore. Disable
   **Restore last dataset** and confirm the next fresh open does not auto-load.

`HOST-ANNOTATION-CSV-01` automates the Windows-local subset of steps 1-5 and 8
through real `cmd.do`; `annotator_qt_smoke.py` automates preview-source
isolation plus background prepare heartbeat, cancel/late-result suppression,
and main-thread Preview/Load completion in real PyQt5. The reviewed 2026-07-25 record separately captures a
real `K:` FUSE cold/warm run and a real WSL core staging request sequence.
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
