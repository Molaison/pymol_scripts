# Structure Annotator import and review configuration

Status: completed and packaged for Windows PyMOL 3.1.3.1, 2026-09-17.

## Required behavior

- Load `review.json` and then `review.override.json` beside a CSV/TSV table.
  The four supported review options are `path_column`, ordered
  `display_columns`, optional `label_column`, and `step_cmd`. Explicit import
  options take precedence. User changes write `review.override.json`; generated
  `review.json`, score tables and structures remain unchanged.
- A source label describes the candidate; it neither changes row identity nor
  counts as a manual annotation. Empty labels retain the ordinary row title.
- Delimited-table import exposes CSV/TSV, detects tab-separated headers even
  with ragged data, and retains exact header/field spelling.
- Root mapping accepts `K:` as the root `K:\`, including `/` to `K:\`.
- Both directory and table imports prepare filesystem work off the Qt thread.
  Slow directory scans, legacy annotation reads and state writes must not freeze
  the interface. Cancellation suppresses late results and retains the old scene.
- Import uses the currently entered path. History suggestions, source-header
  completion, settings saves and older asynchronous results cannot replace it.

## Validation plan

Use focused standard-library tests for parsing, configuration precedence,
read-only preview, atomic override writes, source labels and mapping. Use the
real Windows PyMOL/PyQt5 harness for typed/pasted paths, CSV/TSV switching,
review settings, save/reopen, failed import, cancellation and slow remote-style
I/O. Record desktop evidence separately from WSL and injected-latency evidence.
Build and verify an updated Plugin Manager ZIP after validation.

## Evidence

- Initial existing parsing/mapping tests: 34 passed. Existing annotation
  controller tests: 69 passed before the asynchronous folder integration.
- New review tests exposed one worker-side access to live `STATE` through
  legacy annotation path selection. Folder preparation now reads the exact
  discovered path; legacy annotation read errors propagate visibly.
- Desktop command (Windows-local cwd `/mnt/c`):

  ```sh
  /mnt/d/Softwares/Pymol3.1/python.exe -I '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\tests\host\annotator_source_review_smoke.py' --output '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\output\annotation-source-review-0.8.0.json'
  ```

  Log: `output/annotation-source-review-0.8.0.log`. Supervision uses the live
  exec session and blocking `write_stdin`; read final JSON after process exit.
  Session `66325` exited 0; JSON says passed on Windows PyMOL 3.1.3.1. All
  review/override, label, CSV/TSV pasted-path, bad-config, mapping and source-hash
  assertions passed. The 203-file folder test recorded 2.53 ms import click,
  0.53 ms cancel, 19 Qt heartbeats and 204 filesystem operations off the main
  thread. Slow-I/O blocking was injected; it is not real remote timing.
- New review tests and existing controller tests: 79 passed in 0.713 s.
- Full-suite command: `python3 -m unittest discover -s tests -q`, session
  `6573`, log `output/annotation-regression-0.8.0.log`; 767 passed in 26.578 s.
- Existing Windows `annotator_workflow_smoke.py --full-gui`, session `82766`,
  output `output/annotation-workflow-0.8.0.json` and `.log`; passed. The
  200-row/128-column table imports in 0.139 s. Warm Next median/p95 is
  1.743/2.071 ms, with zero load/stat calls in the navigation click handler.
- Existing Windows `annotator_import_smoke.py`, session `81061`, passes explicit
  configuration-history restoration and precedence, directory/CSV switching,
  421-character structure paths, failed-import preservation and reopen. Output:
  `output/annotation-import-0.8.0.json` and `.log`.
- Actual remote attempt, session `61310`, failed with `FileNotFoundError` for
  the previously inspected `summary2.csv`. A separate Windows probe confirmed
  that the dataset changed during this task: the parent now contains
  `01_scores.tsv`, 600 rows, and 601 structure-directory entries. This is a
  source-availability failure, not a passed import. The failed JSON/log remain
  at `output/annotation-source-review-remote-0.8.0.*`.
- Actual remote rerun uses `annotator_source_review_smoke.py --remote-table
  K:\xcfhome\zpzeng\project_run_log\hallucination_paper_preparation\010_rna_aptamer_target_protein\outputs\rna_evaluator_comparison\22_bivalent_40nt_review\01_scores.tsv
  --path-column relative_path`. All source state writes are suppressed and
  history/cache remain in the isolated temporary Windows test directory.
- Session `35676` passed the actual remote rerun. Output:
  `output/annotation-source-review-remote-tsv-0.8.0.json` and `.log`.

  | Source | Rows | Load time | Click time | Qt heartbeat ticks | Largest gap |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | `01_scores.tsv`, `relative_path` column | 600 | 0.281 s | 1.73 ms | 19 | 46.99 ms |
  | Real K: `structures` folder | 600 | 16.151 s | 2.92 ms | 1603 | 58.41 ms |

  Table load timing begins after header discovery. The first displayed
  structure contains 2399 atoms in both cases. There are no main-thread K:
  stat/list/scandir calls during import. Source table and existing state-file
  hashes match; absent state files remain absent. The test suppresses source
  settings writes and uses an isolated local history/cache. It does not
  measure cold network throughput or promise hard cancellation of OS reads.

  The same run repeats the generated review/override, source-label, pasted
  CSV/TSV switching and failure checks. All 12 synthetic source-file hashes
  match. Injected blocked folder scanning records 3.19 ms click and 0.92 ms
  cancel with 18 heartbeats; all 204 scan/annotation calls are off the Qt thread.
- Final command: `python3 -m unittest tests.annotation.test_review_import
  tests.annotation.test_structure_annotator_history
  tests.architecture.test_plugin_package -q`. All 20 passed in 0.379 s, including
  duplicate source labels, label configuration history, failed atomic writes
  and ZIP source-byte/CRC/bootstrap checks. Log:
  `output/annotation-final-checks-0.8.0.log`.
- `python3 scripts/package_plugin.py` generated `dist/PymolPlugins-0.8.0.zip`
  (423914 bytes; SHA-256
  `c01194187f364c47f434049cef0397934b3966a68c72b84a13785e90205ef614`).
  Real Windows `plugin_package_smoke.py` installation passed in session `41917`:
  version 0.8.0, 11 modules, 72 canonical commands, 8 compatibility commands,
  6 menus, annotation/batch dialogs, shared theme and native workspace modes.
  Output: `output/plugin-package-0.8.0.json` and `.log`. No user plugin
  installation was changed by the temporary harness. `git diff --check` passes.

The package is ready for Plugin Manager installation followed by restarting
PyMOL. PyMOL 2.x and other Qt bindings were not exercised by this change.
