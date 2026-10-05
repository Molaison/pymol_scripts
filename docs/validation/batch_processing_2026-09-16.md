# Batch processing 0.6.0 validation

Status: passed for the complete WSL regression and Windows PyMOL 3.1.3.1 / PyQt5,
including final ZIP installation. PyMOL 2.x and other Qt bindings are unverified.
Input scope confirmed by the user: structure files in folders or CSV tables,
not already-loaded PyMOL objects.

## Contract and investigation

Fixed-reference mode pairs each A file/CSV row with one reference. Folder ID
mode matches A/B templates, wildcard captures or filename regexes; paired CSV
mode uses chosen ID, A-path and B-path columns. Recipes run in order for each
pair and are not limited to alignment. Outputs use a separate directory and
retain source formats.
The user explicitly requested both outputs: saving processed A and B is the
default in the dialog and command (save_b=1).

Implementation reuses canonical CSV parsing, read-only mapping profiles,
PathResolver, DirectMaterializer and the checked pp_foreach parser dispatcher.
It adds no annotation/navigation cursor, transform store or persistent cache.
All cmd calls stay on the main thread; only input preparation uses a background
worker. Original files and unrelated scene objects are outside automatic writes
and cleanup. Arbitrary user recipes are trusted executable PyMOL configuration.

The scout and bounded planner worker both returned upstream HTTP 502 without
edits. The main thread implements and validates this change.

## Validation gates

- Pure planning and real-file tests, source/output preflight, exact step order,
  stop/continue/cancel, reporting and owned-only cleanup.
- Real Windows PyMOL visible GUI run for folder/reference, folder/pairs and
  paired CSV, including a multi-step A/B recipe and verified output coordinates.
- Main/compact screenshots, parser failure, missing ID, source hash preservation,
  annotation entry point, final regressions and real final-ZIP installation.

Tests use the existing output directory for logs/JSON/screenshots and isolated
temporary structure fixtures. Windows processes run serially from /mnt/c with
the bundled PyMOL Python; terminal sessions are supervised by blocking waits.
PyMOL 2.x and other Qt bindings are not established by the Windows PyQt5 check.

## Execution record

- Core session 42521 exited 1: 38 tests, three CSV loader errors and two
  default-help-count failures. Log: `output/batch-0.6.0-core.log`. The loader
  must pass cancellation to `CsvDatasetSource.load`, not its constructor;
  batch help must stay within the existing twelve-task default surface.
  Both causes were corrected before retrying.
- The bounded runner review worker also failed at startup with upstream
  HTTP 502, without edits. Main-thread review and validation cover its scope;
  no alternate agent model or backend was substituted.
- Core retry 91860 exited 0: 38 tests in 3.022 s. Expanded boundary session
  72420 exited 0: 51 tests in 1.371 s. Logs: `output/batch-0.6.0-core-rerun.log`
  and `output/batch-0.6.0-boundaries.log`.
- Windows session 64524 exited 1 before processing: the new harness supplied
  an unsupported `quiet` argument to `pp_load`. The harness now uses the
  existing no-argument command. Evidence: `output/batch-host-0.6.0.log` and JSON.
- Windows session 99516 exited 0 but produced no final JSON, so it is NOT a
  passing gate. Its log/screenshots reach successful reference, paired-file,
  paired-CSV processing and deliberate error checks. The harness called
  `PyMOLQtGUI.close()` in its finally block, exiting PyMOL before the final
  result or traceback could be written. Remove that premature close; only
  `main()` may quit after persisting the result. Screenshot review also found
  stale table/progress after a new preview failed; preview now clears both.
- Windows session 27550 exited 1 with a preserved JSON traceback: the harness
  clicked the Data button's main action instead of its menu-arrow subcontrol.
  It now clicks the native Qt menu subcontrol before selecting Batch processing.
  No production annotation menu behavior was changed. A CSV/reference run was
  also added alongside the existing CSV/paired run.
- Windows session 27602 exited 1 while rereading a CSV/reference output:
  `cmd.get_coords` returned None after `cmd.load`. The run marked both pairs
  completed, so completion alone is not accepted as output proof. The harness
  now records the exact output contents, object/atom/state counts on that
  assertion before deciding whether the defect is in saving or inspection.
- Diagnostic sessions 42526 and 37475 exited 1. The former attempted an atom
  count before the read-back object existed. The latter preserves the valid
  six-atom PDB, `cmd.load` return value 1, and the object appearing in the
  following name query. Output inspection outside the runner's Qt callback
  must allow the PyMOL event loop to complete the load. The harness now waits
  for actual read-back coordinates and deletion, then asserts every coordinate;
  no production load or save behavior was relaxed.
- Sessions 4468, 11204 and 11799 did not pass. Event-loop waiting did NOT fix
  the output selection lookup, disproving the timing hypothesis above. Session
  11799 preserved exact output PDBs: an independent Windows process loads the
  first one with six atoms, one state and correct coordinates. Forty repeated
  same-name load/delete cycles also pass (`batch-output-readback.log` and
  `batch-output-lifecycle.log`). In the GUI scene the name query reports a
  molecule and one state, but the selection query rejects that name. The output
  verifier now reads the exact object's coordinate set instead of a selection;
  this remains a real load and full coordinate comparison, not a file-size gate.
  A 36-pair continuous GUI run was added to check runtime name reuse separately.
- User refinements: A and B both save by default. File ID pairing now supports
  literal templates, wildcard captures and named/single-group filename regexes.
  Core matching gate passes 39 tests (`output/batch-0.6.0-matching.log`).
- Session 78926 failed in the 36-pair run at Save A for pair 4, after its
  alignment and translation had completed. The same selector failure therefore
  affects real execution, not just inspection. Object names were reused after
  deletion. Preparation now assigns unique A/B/reference names per pair, while
  an unchanged fixed reference remains loaded once. Verification objects also
  use unique names and assert both object-coordinate and selection-coordinate
  access. Native undo settings are not disabled or altered. Separate headless
  150-command marker and 40-cycle undo-enabled tests pass; they do not reproduce
  the GUI failure, so no unproven native-undo root cause is claimed.
- Session 87967 exited 0: 52 complete pairs, 104 A/B outputs, maximum coordinate
  error 0.0, 11 unchanged original source files and 288 viewport sample colors.
  Template/glob/regex, both CSV modes, cancellation, errors and the annotation
  menu entry pass. Full regression session 57869 passes 756 tests in 25.998 s.
- Session 6797 reaches all format/output/hash gates but exits 1 at the added
  native undo-mode equality assertion. The implementation never sets that mode;
  the harness now records the mode before processing, after batches and after
  opening the annotation panel to distinguish the source of the transition.
- Session 79898 records native undo mode 0 before the workflow and 1 after
  both the batch and annotation stages: it was enabled, not disabled. The
  fixture now explicitly enables undo before its baseline and verifies that
  the enabled state survives all workflows; production code does not set it.
- Session 47056 exited 0. Final runtime gate: 55 completed pairs, 110 verified
  A/B output structures, maximum coordinate error 0.0 and all 50 input hashes
  unchanged. PDB/mmCIF/gzip output, 36-pair continuity, all match modes, both CSV
  modes, explicit stop/continue, cancellation after one completed pair, real
  annotation menu entry and normal/compact controls pass. Undo stays enabled;
  the viewport sample has 246 colors. Evidence: `output/batch-host-0.6.0-final2.*`.
- Annotation regression session 62477 exited 1 at the multiple-choice Alt+digit
  assertion after a modal field editor. Earlier click-save, failure/retry,
  column/detail and navigation checks completed. The harness now explicitly
  activates the panel, waits for modal/popup closure and counts real shortcut
  callbacks; production annotation behavior is unchanged.
- Annotation session 4032 exited 0, including the actual two shortcut callbacks,
  click-save/advance, required fields, failure/retry, optional columns, complete
  annotation details and navigation. Full workflow session 26010 also exited 0.
- Final path review found that the output directory can be short while the
  completed output/report path exceeds Windows limits. Long-path conversion
  now applies after filenames are joined, including manual report export.
  Session 65734 reaches the processing gates but exits 1 in TemporaryDirectory
  cleanup because its unprefixed recursive delete cannot remove long outputs.
  The test cleanup now uses the same extended-path helper on its owned fixture
  root; no application processing behavior changed for that retry.
- Final Windows session 25355 exited 0 with a complete result JSON. All 56 pairs
  completed and all 112 A/B outputs passed coordinate read-back with maximum
  error 0.0; all 51 original source hashes stayed unchanged. A 431-character
  output path passed saving, report writing and coordinate read-back. Native
  undo remained enabled and the viewport sample contained 236 colors. Evidence:
  `output/batch-host-0.6.0-release2.json` and `.log`.
- Final regression session 43321 exited 0: 757 tests in 23.406 s, `OK`.
  Command: `python3 -m unittest discover -s tests -v`; log:
  `output/batch-0.6.0-full-suite-final.log`. Blocking terminal supervision waited
  for the process exit; the final unittest summary was checked separately.
- The 0.6.0 annotation regression evidence is
  `output/annotation-layout-0.6.0-r2.json` and
  `output/annotation-workflow-0.6.0.json`, both `passed`. The latter exercises
  the full GUI with 200 rows and 128 columns, including viewport/docked/floating
  console shortcuts. Warm Next median/p95 was 1.468/2.002 ms with zero file-load
  or stat calls.
- `just package` exited 0 and produced the final ZIP. Build log:
  `output/batch-0.6.0-package-build.log`. Shell startup printed pre-existing
  warnings for missing `/home/admin/.cargo/env`, `/home/admin/.local/bin/env`
  and unavailable `zoxide`; the package build and following artifact checks
  completed successfully.
- The exact delivered ZIP, not a separately rebuilt test archive, passed
  `PluginPackageTests.test_single_plugin_package_contains_complete_runtime`
  with `archive` set to `dist/PymolPlugins-0.6.0.zip`. CRC, bootstrap version,
  every runtime/entry-source byte and compiled-cache exclusion passed. Evidence:
  `output/batch-0.6.0-package-audit.json`.
- Windows installation session 17638 exited 0 under blocking terminal
  supervision. The real Plugin Manager installed the final ZIP into an isolated
  temporary plugin directory and emitted its success dialog. Version 0.6.0,
  11 modules, 72 canonical + 8 compatibility commands and 6 menus passed;
  Structure Annotator imports from the installed bundle. Triggering the actual
  Batch Processing menu opens its dialog with Save B checked and all three
  matching modes. No repository runtime imports or user installation changes
  were required. Evidence: `output/plugin-package-0.6.0.json` and `.log`.
- Final screenshot inspection covered the 1000 x 720 command editor, 760 x 560
  regex inputs/results and the annotation save/advance settings. Text and
  controls remain within their layouts; compact tables use horizontal scrolling.
- `git diff --check` passed. No commit, reset or change to the user's installed
  plugin was performed.

## Delivered artifact

- File: `dist/PymolPlugins-0.6.0.zip`
- Size: 416154 bytes; 96 entries.
- SHA-256: `b4d1f022a095599c958e7c246e87e484cdb684374c2e754c1f84897c6e468455`
- Install through Plugin Manager, then restart PyMOL after replacing an older
  version. Batch Processing is available from the plugin menu, `pp_batch` and
  Structure Annotator's Data menu.

Final installation command, run from `/mnt/c`:

```bash
/mnt/d/Softwares/Pymol3.1/python.exe -I \
  '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\tests\host\plugin_package_smoke.py' \
  '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\dist\PymolPlugins-0.6.0.zip' \
  --output '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\output\plugin-package-0.6.0.json'
```
