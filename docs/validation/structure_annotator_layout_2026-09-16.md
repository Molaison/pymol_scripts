# Structure Annotator 0.5.0 validation

Status: completed for Windows PyMOL 3.1.3.1 / PyQt5 on 2026-09-16.
Resumes session `01a0a7ff-45e5-7481-9343-a153243d7f9f`. All final gates below
passed; earlier failed attempts are retained as the diagnostic record.

## Acceptance contract

- One review workspace with a candidate list, current structure, selected scores,
  annotation fields and one primary save action. Import and settings are separate
  pages; filters, details, field setup and path profiles remain available on demand.
- Mouse choices remain drafts until Save. Required values gate Save. Drafts
  survive navigation and opening settings/import, but do not cross datasets.
- The save action remains outside the scrolling score/field content. Verify the
  real 1000 x 720 and 760 x 560 windows with six scores and a long structure name.
- Use visible controls for import, save/advance, batch save, menu undo and filters.
  Preserve profile mapping, command validation, cancellation and warm navigation.
- Package and installer checks must use the final source tree. Windows PyMOL
  3.1.3.1 evidence is separate from PyMOL 2.x and other Qt bindings.

## Execution record

- Initial annotation regression: 199 tests, one error because the table-refresh
  test stub lacked the new Filter button. The stub now includes that control;
  the no-resort assertion is unchanged. Session 86456 exited 1. Log:
  `output/annotation-0.5.0-core.log`.
- First Windows review check: passed, 200 rows; required/multiple fields,
  Shift/Ctrl selection, batch save/undo, filtered scores and export. Batch save
  made zero structure loads. Warm Next median/max: 2.69/4.21 ms. Session 7371
  exited 0; JSON and screenshot were inspected. Evidence:
  `output/annotation-review-0.5.0.json`, `.png`, `.log`. Subsequent edits refine
  navigation icons, table headers and shortcut scope, so this is intermediate
  evidence until the final checks complete.
- Visible-controls/layout acceptance: terminal session 53243, command
  `python.exe -I tests/host/annotator_layout_smoke.py --output <layout.json>`.
  Log: `output/annotation-layout-0.5.0.log`; expected JSON:
  `output/annotation-layout-0.5.0.json`. Screenshots share that stem.
- Full regression: terminal session 22748, command
  `python3 -m unittest discover -s tests -v`. Log:
  `output/annotation-0.5.0-full-suite.log`. Passed: 714 tests in 25.953 s.
- Layout session 53243 exited 1 after the visible workflow and both layout
  checks passed. The viewport pixel check caught use of QWidget `grab()` on
  this installation's `PyQt5.QtOpenGL.QGLWidget`; that API omits its GL buffer.
  Runtime class inspection confirmed `grabFrameBuffer()` is the correct API.
  The harness now saves that image before its nonblank assertion. Normal,
  small and empty-state panel screenshots were inspected; saving stays visible.
- Supervision uses the terminal session's blocking wait. Each run is accepted
  only after terminal exit and inspection of its expected JSON/log/artifacts.

Completed follow-up gates:

- Session 61877: the complete visible-controls/layout run passed after using
  the QGLWidget framebuffer. Both exact window sizes passed; the 170 x 43 save
  button stayed inside the window, with no clipped button text. The OpenGL
  sample contained 83 colors, and the rendered structure was visually checked.
- Session 24019: full Windows workflow passed (200 rows x 128 columns), including
  native completion, panel/viewport/docked/floating console shortcuts, failed
  imports, cancellation and close/late-result isolation. Import: 0.100 s;
  warm Next median/p95/max: 1.25/1.80/1.82 ms, with zero load/stat calls.
  Evidence: `output/annotation-workflow-0.5.0.json`, `.png`, `.log`.
- Session 88422: Windows profile editing and command recovery passed. Create,
  sample-test, save, duplicate, delete, invalid-regex preservation, preview
  isolation, format restoration, incomplete/unapplied commands, missing-selection
  warm/cold failures and camera restoration are all covered. Evidence:
  `output/annotation-mapping-0.5.0.json`, `.png`, `.log`.
- Session 69028 passed explicit history restore and directory import, including
  a 341-character directory and 421-character structure path. Sessions 51495
  and 22222 then passed the 50,000-row review and final 714-test suite (21.983 s).
- A pre-install screenshot review caught clipped combo text and a nearly hidden
  root-mapping row in the profile editor. The first 0.5.0 package from session
  59960 is superseded. Native combo rendering, table row sizing, separate rule
  tabs and a scrollable profile form with a fixed Save action address that issue;
  targeted visual regression and rebuilt-package checks are required below.
- Resumed final sizing checks: all four changed UI modules and both updated
  host harnesses compile. The read-only scout returned upstream HTTP 502 and
  made no changes. Windows mapping session 21152 exited 1: its new assertion
  found that the rule table could not display two complete rows. The harness
  now saves the current profile screenshot and reports actual table/header/row
  dimensions before that assertion; the failed gate is not treated as passed.
- Diagnostic session 55891 exited 1 with a 53 px rule viewport, 36 px rows,
  48 px header and 41 px tab bar. Its normal-window screenshot confirmed the
  second row was clipped. The rule area is now 300 px instead of 230 px;
  the profile form still scrolls and its Save action remains outside the scroll.
- Mapping session 1024 confirmed the taller table, then exited 1 on the new
  combo text-height check. The later generic QTableView item padding overrode
  the compact QTableWidget rule, reducing embedded editor height. The compact
  override now follows the generic rule. Text-boundary assertions remain active.
- Mapping session 15752 passed the complete profile/preview/command-recovery
  run at 980 x 740 and 760 x 560. Text height is 23 px inside a 24 px table
  editor; the Save button remains 139 x 43 and within both windows. Screenshots
  confirm legible native combos and usable root/rewrite rows.
- Layout session 79354 passed the main page and field editor checks, then
  exited 1 on the score direction label (141 px text inside 133 px). The score
  direction and threshold columns are wider, final columns have explicit
  widths, and the default score dialog is 920 x 420. Both config dialogs now
  also assert complete header text widths, including at compact sizes.
- Layout session 14869 passed the visible workflow, 1000 x 720 / 760 x 560
  review layout, 680 x 390 / 560 x 360 field editor and 920 x 420 / 640 x 360
  score editor. All combo options and headers fit their cells; fixed save
  buttons remain in bounds. The OpenGL sample contains 83 colors. Final main,
  compact, open-data and configuration screenshots have been visually checked.
- Final workflow session 19390 passed: 200 x 128 CSV import in 0.097 s, warm
  Next median/p95/max 1.34/2.19/2.27 ms, no load/stat calls, and complete
  shortcut/completion/cancellation/failed-import checks. Review session 92913
  passed 50,000 rows, selection/required/multiple fields, batch save/undo,
  scores and export; batch operations made no structure loads. Import session
  72737 passed explicit history restore, 341-character directories, 421-character
  structure paths and preserved live sessions. Regression session 16838 exited
  0 with 714 tests passing in 22.700 s.
- Before packaging, the mapping harness also checks that Test path feedback is
  visible immediately after the click, not just present in the label's data.
- Session 9647 exited 1 and its screenshot confirmed Test path results were
  below the scroll viewport after successful resolution. The action now scrolls
  its result into view on the next Qt event turn. The harness checks the result
  and captures it at both profile sizes after actual Test path clicks.
- Session 27569 passed the complete final mapping run and both result-visibility
  checks. The 760 x 560 screenshot shows the raw path, rewritten path and wrapped
  computer path above the fixed Save button. Session 49992 then passed all
  714 regression tests in 20.699 s against the final runtime source.

Windows commands run from `/mnt/c` through
`/mnt/d/Softwares/Pymol3.1/python.exe -I`, using absolute repository UNC paths.
Fixtures, history, mapping files and structure caches are isolated in a
Windows-local temporary directory. Source data and user settings are not edited.

## Final evidence

Every terminal session listed here exited 0. JSON status and expected artifacts
were inspected after exit; screenshots were visually reviewed.

| Gate | Session | Evidence in `output/` |
| --- | --- | --- |
| Visible workflow, main layout, fields and score dialogs | 14869 | `annotation-layout-0.5.0.json`, `.log` and named PNGs |
| Profile layout, visible path results, mapping and command recovery | 27569 | `annotation-mapping-0.5.0.json`, `.log` and named PNGs |
| Full Windows workflow, 200 rows x 128 columns | 19390 | `annotation-workflow-0.5.0.json`, `.log`, `.png` |
| 50,000-row review, required/multiple fields, batch save/undo and export | 92913 | `annotation-review-0.5.0.json`, `.log`, `.png` |
| History, directory import, long paths and live-session preservation | 72737 | `annotation-import-0.5.0.json`, `.log` |
| Final regression: 714 tests, 20.699 s | 49992 | `annotation-0.5.0-full-suite.log` |
| Real Windows Plugin Manager installer and command/menu loading | 8819 | `plugin-package-0.5.0.json`, `.log` |

The complete workflow command includes `--full-gui`; review includes
`--rows 50000`. All host scripts accept `--output <absolute-UNC-JSON-path>`.
The package check also receives the absolute UNC ZIP path as its positional
argument. The regression command is `python3 -m unittest discover -s tests -v`.

## Final package

- Command: `just package`, exit 0; log: `output/package-0.5.0.log`.
  Existing Bash startup warnings about missing environment files and `zoxide`
  did not prevent the build; they are retained in the log.
- Artifact: `dist/PymolPlugins-0.5.0.zip`, 398498 bytes, 92 entries.
- SHA256: `6d4a822c480e10e5ab4f2489eb3b0046ab7e1d0e7936a02bd3dadc1bf6dcc07e`.
- Audit: `output/package-0.5.0-audit.json`; ZIP CRC, versioned bootstrap, complete
  Python-source byte equivalence and absence of compiled artifacts pass. This
  reused `PluginPackageTests.test_single_plugin_package_contains_complete_runtime`
  against the delivered archive rather than building a different test archive.
- Windows installer result: version 0.5.0, 11 canonical modules, 71 canonical
  commands, 8 compatibility commands, 5 menus, Structure Annotator import and
  installer success confirmation all pass.
- Installation used the real Plugin Manager API with an isolated temporary
  plugin directory and Qt menu container. It did not modify the user's existing
  plugin installation. The GUI workflow checks used the byte-matched source tree.

No implementation or release blocker remains for this tested target. PyMOL 2.x,
other Qt bindings and optional external scientific backends are not validated
by these gates. Existing working-tree changes remain uncommitted and preserved.
