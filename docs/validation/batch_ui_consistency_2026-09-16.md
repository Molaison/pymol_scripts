# Batch UI consistency - 0.6.1

Status: passed on Windows PyMOL 3.1.3.1 / PyQt5, including full regression and
final ZIP installation. PyMOL 2.x and other Qt bindings remain unverified. The
user asked whether the new Batch Processing dialog matches the rest of the toolkit.

## Confirmed gap

Version 0.6.0 passed functional and bounds checks but did not apply the
Structure Annotator stylesheet. Its independent top-level dialog inherited
PyMOL's monospace font and native gray controls. That was not visual consistency
with the annotation workspace, even though the controls fit their windows.

## Change and acceptance contract

- Reuse `annotation/panel_style.py` without changing the application-wide font
  or stylesheet. Ordinary controls use the same font, colors, spacing, table
  rows, focus/selection treatment and restrained progress line as the annotator.
- Keep the command editor monospace, use the existing primary-action role for
  Run batch, and keep native icon tools within fixed-size bounds. Reuse the
  existing File locations collapsible control and shared error feedback style.
- Preserve all pairing, preparation, execution, cancellation, output and
  annotation behavior. Normal and compact windows must keep every input
  accessible through their existing scroll areas without clipping button text.
- Validate actual Qt fonts, style identity, rendered primary-button color,
  row/icon geometry and both standalone/menu and annotation entry points.
  Rerun real Windows batch and annotation workflows, then test the exact 0.6.1
  ZIP through the real Plugin Manager. PyMOL 2.x and other Qt bindings remain
  separate, unverified targets.

## Execution record

- The read-only acceptance scout failed at startup with upstream HTTP 502 and
  made no edits. The main agent owns the source inspection and real Host checks;
  no alternate model or backend was substituted.
- Source inspection confirmed `BatchDialog` had no style application. The
  shared `apply_style` helper now accepts a workspace name; existing annotation
  callers keep their default. Error styling is shared by role rather than
  duplicated. Only presentation and package version are changed in production.
- Syntax validation passed for both presentation modules, the shared layout,
  and the two modified Host harnesses. Full regression session 14516 exited 0:
  757 tests in 22.094 s. Log: `output/batch-ui-0.6.1-unit.log`.
- Windows session 62753 exited 1 at the layout visible-region assertion after
  real reference processing and style/font/color checks. The screenshot already
  shows the shared theme, but is not accepted as a complete layout pass. The
  harness now reports the exact widget, full rectangle and visible rectangle to
  distinguish clipping from a styled-widget geometry assumption. Evidence:
  `output/batch-ui-0.6.1-host.json`, `.log` and `-failed.png`.
- Diagnostic session 13448 exited 1 and identified the table: its rectangle was
  `(0, 0, 960, 234)` and parent-only visible region `(0, 48, 960, 186)`.
  The excluded 48-pixel area is its opaque styled header, which is visibly
  present in the screenshot. Qt excludes opaque child painting from the
  parent's visible region. The test now checks full visibility of both actual
  paint viewports (header and data), plus the full table's window bounds;
  button-content and input checks remain strict. No production geometry was
  changed for this correction. Evidence: `output/batch-ui-0.6.1-host-r2.*`.
- Session 74108 passed the normal/compact font, button-content, table and input
  bounds checks, then failed because the harness switched inputs while leaving
  the form scrolled down from the previous configuration. Each source/mode
  selection now scrolls its real control into view before interacting, as do
  the existing path controls. Screenshot review also prompted a larger initial
  input area with a stable preferred table height, exposing the ordinary
  reference-mode saving options at normal window size. Evidence:
  `output/batch-ui-0.6.1-host-r3.*`.
- Session 52695 passed the previous gates and failed while the harness typed
  into the newly shown regex inputs before Qt processed their dynamic layout.
  Its screenshot shows the regex mode already selected but the new expression
  rows not yet laid out. Combo/tab test actions now yield through the Qt event
  loop for 30 ms before subsequent geometry-dependent input. Strict visible
  bounds are retained, and production callbacks do not force event processing.
  Evidence: `output/batch-ui-0.6.1-host-r4.*`.
- Session 94127 passed template, wildcard, regex, both CSV processing modes and
  prior geometry gates, then hit the same pending-layout condition in the new
  File locations expansion check. That check now yields for 80 ms after
  expansion/resizing, matching the established layout test, before scrolling
  each actual control fully into view. No visibility assertion or production
  behavior was relaxed. Evidence: `output/batch-ui-0.6.1-host-r5.*`.
- Session 95717 still failed in File locations after explicit Qt event-loop
  waits, disproving the timing explanation for this particular check. The
  harness now captures the exact control, visible/parent rectangles and scroll
  range before any further correction. Evidence:
  `output/batch-ui-0.6.1-host-r6.*`.
- Diagnostic session 3667 isolated the Base folder line edit: height 39, visible
  height 31, scroll value 141 with maximum 351. Its cursor was already within
  the viewport while the bottom padding was not. Qt's `ensureWidgetVisible`
  can follow a line edit's input cursor instead of its full padded bounds. Test
  scrolling now uses `ensureVisible` with the complete mapped widget rectangle
  and margins; full-control visibility is still required. No runtime scrolling
  or error handling was changed. Evidence: `output/batch-ui-0.6.1-host-r7.*`.
- Session 54739 completed all processing, format, long-path, cancellation,
  error-feedback and File locations layout checks. It failed in the final
  annotation-entry icon assertion. That assertion now reports the specific
  icon, size and constraints, and entry screenshots are captured before it.
  Evidence: `output/batch-ui-0.6.1-host-r8.*`.
- Session 96243 identified a real fixed-size discrepancy: Read CSV columns was
  34 x 30, with minimum height 0 despite its requested 34 x 34 size. The shared
  icon-role stylesheet's `min-height: 0` overrode the code-set constraint during
  Qt polishing. The shared role now explicitly fixes both dimensions at 34 px,
  matching all seven existing icon-role controls across batch and annotation.
  The Host check also asserts all three annotation navigation icon sizes.
  Evidence: `output/batch-ui-0.6.1-host-r9.*`.
- Session 64966 exited 0 with a complete result JSON. Standalone and annotation
  entry styles are identical; controls use Segoe UI, command editors Consolas,
  table rows 36 px and all icon tools 34 x 34. Rendered primary colors are
  `#356ae6` enabled and `#dce5f7` disabled. Application font/stylesheet stay
  unchanged. Both window sizes and expanded File locations pass. All 56 pairs,
  112 outputs, 51 unchanged input hashes, 431-character paths, parser errors
  and cancellation pass; maximum coordinate error is 0.0. Evidence:
  `output/batch-ui-0.6.1-host-r10.json`, `.log` and screenshots.
- Annotation layout session 69071 exited 0, covering click-save/advance,
  failure/retry, multiple-label shortcuts, preferences, and 1000 x 720 /
  760 x 560 layouts. Evidence: `output/annotation-layout-0.6.1.json` and `.log`.
- Full GUI workflow session 74485 failed at filter Tab completion. The harness
  issued keys immediately after showing the modal filter dialog, without waiting
  for native activation following the previous popup. It now requires popup
  closure, an active filter dialog and a visible completion list before Tab;
  the actual completion assertion remains and has focus diagnostics. No
  production completion code changed. Evidence:
  `output/annotation-workflow-0.6.1.json` and `.log`.
- Full GUI retry session 82163 exited 0 for 200 rows and 128 columns, including
  visible filter completion, native command completion, panel/viewport/docked/
  floating-console shortcuts, failure preservation and cancellation. Warm Next
  median/p95 was 1.404/1.994 ms with no load/stat calls. Evidence:
  `output/annotation-workflow-0.6.1-r2.json` and `.log`.
- Final screenshot review confirms shared appearance in the ordinary input
  page, compact regex page, compact File locations and annotation workspace.
  Reference saving options are visible at normal size; compact forms scroll
  while progress and primary actions stay visible. Source screenshots use the
  `output/batch-ui-0.6.1-host-r10-` prefix.
- Final syntax checks passed. Full regression session 99321 exited 0:
  757 tests in 24.629 s, `OK`. Command:
  `python3 -m unittest discover -s tests -v`. Log:
  `output/batch-ui-0.6.1-unit-final.log`.
- `just package` exited 0 and generated the 0.6.1 ZIP. Pre-existing shell
  initialization warnings for missing `/home/admin/.cargo/env`,
  `/home/admin/.local/bin/env` and unavailable `zoxide` did not prevent the
  build or subsequent checks. Log: `output/batch-ui-0.6.1-package-build.log`.
- The exact delivery ZIP passed
  `PluginPackageTests.test_single_plugin_package_contains_complete_runtime`
  with its `archive` set to `dist/PymolPlugins-0.6.1.zip`. CRC, bootstrap
  version, every runtime/entry-source byte and cache exclusion pass. Evidence:
  `output/batch-ui-0.6.1-package-audit.json`.
- Real Windows installation session 36140 exited 0. Plugin Manager installed
  version 0.6.1 in a temporary isolated directory: 11 modules, 72 canonical +
  8 compatibility commands, 6 menus. Its actual Batch Processing menu opens
  the styled dialog with both outputs enabled and all matching modes. The
  installed theme, actual Segoe UI/Consolas fonts and primary role pass without
  repository runtime imports. Evidence: `output/plugin-package-0.6.1.json`
  and `.log`. All terminal sessions were supervised with blocking waits.
- `git diff --check` passed. No commit, reset, user-plugin installation change
  or overwrite of the 0.6.0 release artifact was performed.

## Delivered artifact

- File: `dist/PymolPlugins-0.6.1.zip`
- Size: 416386 bytes; 96 entries.
- SHA-256: `890b7cd7ba70ebc997ecaf060bbb52e1064c252b8434ef528953284ac559fa61`
- Install through Plugin Manager and restart PyMOL when upgrading.

The Windows checks use the following commands from `/mnt/c`. Substitute a
fresh output name to retain this release's records:

```bash
repo='\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins'
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\batch_processing_smoke.py" --output "$repo\output\batch-ui-0.6.1-host-r10.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\annotator_layout_smoke.py" --output "$repo\output\annotation-layout-0.6.1.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\annotator_workflow_smoke.py" --full-gui --output "$repo\output\annotation-workflow-0.6.1-r2.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\plugin_package_smoke.py" "$repo\dist\PymolPlugins-0.6.1.zip" --output "$repo\output\plugin-package-0.6.1.json"
```

Logs, JSON and screenshots use the existing `output/` directory. Windows
processes run serially from `/mnt/c` with bundled PyMOL Python, temporary
fixtures and blocking terminal supervision. No user installation is changed.
