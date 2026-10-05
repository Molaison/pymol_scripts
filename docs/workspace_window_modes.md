# PyMOL workspace window modes - 0.7.0

Status: implemented, tested and packaged on Windows PyMOL 3.1.3.1 / PyQt5,
2026-09-17. The user requested an embedded UI instead of having to reposition
a separate window, while retaining a choice
between embedded and independent windows. "Python" is interpreted as the PyMOL
main window. Existing 0.6.1 artifacts and dirty working-tree changes are retained.

## Contract

- Structure Annotator and Batch Processing share a native `QDockWidget` host.
  Both default to the left side of the actual PyMOL `QMainWindow`, obtained with
  `pymol.gui.get_qtwindow()`. An unavailable main window is an explicit error,
  not a silent switch to an independent dialog.
- A shared window-mode icon menu offers Embedded in PyMOL and Floating window.
  Native docking/floating controls remain usable and update the same choice.
  Both workspaces retain their existing local stylesheet and controls.
- Switching changes only the host's floating state. Keep the same content,
  drafts, candidate selection, batch plan, timers and active work. Do not call
  close handlers or reload data while changing mode.
- If both tools are embedded on the same side, tabify only these plugin docks.
  Do not replace the OpenGL widget, restore the main window's whole layout,
  hide native PyMOL tools or reset the user's other docks.
- Remember each tool's mode, dock side/width and independent floating geometry
  with Qt settings. Tests use an explicit `PYMOLPLUGINS_UI_SETTINGS` INI path;
  they must not modify the user's settings or plugin installation.
- Native host close and programmatic content close share the existing cleanup.
  Saving annotations still prevents close. Closing a running batch requests
  stop and remains open until it stops. Opening an already-visible or tabbed
  tool activates its existing instance, without duplicate windows.
- Embedded geometry may use a lower minimum height than the existing floating
  workspace, whose 760 x 560 annotation layout remains supported. Forms scroll;
  primary actions and PyMOL's actual viewport must remain usable.
  Embedded tools use a 640 x 440 minimum; while any is docked, reserve at least
  320 x 180 for the central viewport. Restore its original minimum once no
  plugin workspace remains docked, without resetting the native dock layout.
  The occupied side extends through its bottom corner so the log dock does not
  squeeze the tools vertically. Restore the original corner allocation after
  leaving that side; do not hide the log or force a different log height.

## Acceptance

1. Validate preference isolation and persistence, default docking, native and
   menu-driven mode changes, direct/host close, vetoed close and reopen.
2. In real Windows PyMOL, preserve annotation drafts and cursor across switches,
   exercise labels/shortcuts and asynchronous close protection, and preserve
   batch preparation, preview, running work and both A/B outputs across switches.
3. Check the actual OpenGL widget and viewport remain present and nonblank.
   Inspect desktop and compact screenshots, visible action/input rectangles,
   tabbed tools and unchanged application stylesheet/native dock ownership.
4. Run the existing regression suite and relevant real Host workflows, then
   audit the exact release ZIP (CRC, runtime bytes and version) and install it
   through the real Plugin Manager in an isolated temporary directory.

PyMOL 2.x and Qt bindings other than the tested Windows PyMOL 3.1.3.1 / PyQt5
remain unverified. Host execution is strictly serial from `/mnt/c`, using the
bundled Python, isolated fixtures and blocking process supervision. Final JSON,
screenshots and logs belong in the existing `output/` directory.

## Execution Record

- Read-only delegation returned upstream HTTP 502 without edits. Current
  source inspection confirms both tools are independent `QDialog` instances,
  and that batch/annotation close handlers own cancellation and cleanup.
- The installed PyMOL exposes its real `QMainWindow` through
  `pymol.gui.get_qtwindow()`. The implementation will use native docking rather
  than treating `QApplication.activeWindow()` as the main window.
- The initial specification did not claim a new release or Host validation;
  the completed checks are recorded below.
- The bounded implementation worker also failed with upstream HTTP 502 and
  made no edits. Main-thread implementation now hosts the existing widgets
  without rebuilding them when the native floating state changes.
- Windows session 67631 exited 1 at a test assumption: Windows hides Qt's
  embedded float button once a dock uses the native floating title bar. The
  test now clicks that native Qt button while embedded and uses the visible
  mode menu to dock again. Source screenshot review also identified QWidget's
  missing styled-background attribute; shared styling now enables it explicitly.
  Evidence: `output/window-modes-0.7.0-host.json`, `.log` and screenshots.
- Session 76434 passed native/menu switching, restored floating geometry and
  real asynchronous save close protection, then failed at a fixture lookup.
  CSV annotation keys are canonical `csv:` hashes, not display IDs such as
  `row-0`; the check now captures `STATE.current_entry_id()` before saving.
  Evidence: `output/window-modes-0.7.0-host-r2.json` and `.log`.
- Session 96521 passed the annotation gates, including the viewport shortcut,
  then exposed a real compact-layout problem: the 1280 x 720 desktop left less
  than 240 px of viewport width. Docked content now has its own 680 x 360
  minimum and reserves 320 x 180 centrally while docked. The original central
  minimum is restored when the last workspace is floated or closed. The
  geometry test retains a strict width check and tests minimum restoration.
  Evidence: `output/window-modes-0.7.0-host-r3.json`, `.log` and screenshots.
- Session 75124 measured the real combined minimum as 1285 px, so 680 px for
  the plugin was still too wide for the 1280 px target. Embedded minimum width
  is now 640 px; floating sizes and the 320 px viewport requirement are retained.
  Evidence: `output/window-modes-0.7.0-host-r4.json` and `.log`.
- Session 13004 passed the compact main-window and viewport-width checks, but
  exposed a zero-height batch table viewport beneath its header. Native dock
  corner allocation now gives the tool its side's full height while preserving
  log height. Docked content requires 640 x 440 and the results table retains
  at least one data row. Original corner allocations are checked after close.
  Evidence: `output/window-modes-0.7.0-host-r5.json`, `.log` and screenshot.
- Session 98297 passed both compact tool layouts and the nonblank framebuffer
  check, but did not observe changed sampled pixels after camera rotation.
  The next check retains separate before/after framebuffer images and records
  camera matrices, enabled objects and the native draw timer before attributing
  this to the plugin or Qt capture. Evidence: `output/window-modes-0.7.0-host-r6.*`.
- Session 87966 confirms that the camera matrix changes and the native draw
  timer remains active, while automatic captures have 0 changed samples from
  295 colors. A diagnostic-only launch without any plugin docks now compares
  automatic capture with an explicit native widget repaint. Neither result
  is treated as full workspace acceptance. Evidence:
  `output/window-modes-0.7.0-host-r7.json` and its framebuffer PNGs.
- Diagnostic session 7330 exited 0 without any plugin dock: automatic capture
  also had 0 changed samples, while an explicit native repaint produced 1043.
  This is a capture/standalone-harness boundary, not evidence of a docking
  regression. Image acceptance now explicitly repaints the real OpenGL widget
  before comparing its framebuffers; production rendering is unchanged. Full
  window screenshots use native screen capture to include the OpenGL surface.
  Evidence: `output/window-modes-0.7.0-viewport-probe.json` and `.log`.
- Session 6502 passed both layouts, changed native framebuffer samples, batch
  preparation/running switches, all six A/B output checks and stop-on-close.
  Reopening the floating batch tool failed the strict size/position equality
  check. The test now records expected and actual geometry before a correction.
  Evidence: `output/window-modes-0.7.0-host-r8.json`, `.log` and screenshots.
- Session 13191 isolated an 8 px horizontal reopen offset, with the requested
  960 x 700 size unchanged. Native `QDockWidget` frame recreation changes Qt's
  position accounting on Windows. Floating position is now saved separately
  and reapplied after `restoreGeometry`; the strict position assertion remains.
  Evidence: `output/window-modes-0.7.0-host-r9.json` and `.log`.
- Session 45554 exited 0 with complete JSON: annotation draft/shortcut/save
  protection, native/menu switches, preparation/running switches, cancellation,
  3 pairs and 6 verified outputs (maximum coordinate error 0.0), both layouts,
  reopen geometry, dock side, singleton ownership and restored native layout
  pass. At 1280 x 720 the viewport is 320 x 406; 1261 native framebuffer sample
  pixels change on rotation. The 12 source hashes are unchanged. Evidence:
  `output/window-modes-0.7.0-host-r10.json`, `.log` and screenshots.
- Regression session 83331 exited 0: 757 tests in 22.818 s, `OK`.
  Command: `python3 -m unittest discover -s tests -v`.
  Log: `output/window-modes-0.7.0-unit.log`.
- The final Host check also requires remembered dock width and visible
  settings failures/no silent standalone fallback. The specimen is explicitly
  framed for compact screenshots without changing production camera behavior.
- Full batch session 33779 exited 0: all 56 pairs, 112 outputs, 51 unchanged
  source hashes, 431-character output paths, shared styles, compact/floating
  layouts, all matching formats and error/cancellation paths pass. Maximum
  coordinate error is 0.0. Evidence: `output/batch-workspace-0.7.0-host.json`
  and `.log`.
- Annotation layout session 50064 reached the multiple-label shortcut check,
  but its old activation wait compared the active top-level window to the
  content widget. That check now compares `panel.window()`, which is the real
  floating host. The shortcut and annotation assertions are unchanged.
  Evidence: `output/annotation-workspace-0.7.0-layout.json` and `.log`.
- Annotation layout session 73173 exited 0 for both floating sizes and all
  click-save/manual/advance, required-field, batch, failure-retry, shortcut and
  preference-reopen checks. Evidence:
  `output/annotation-workspace-0.7.0-layout-r2.json` and `.log`.
- Full annotation workflow session 59652 exited 0 for 200 rows / 128 columns,
  native completion, panel/viewport/console shortcuts, failed-import scene
  preservation, cancellation and close/late-result isolation. Warm navigation
  median/p95 was 1.550/1.936 ms with no load/stat calls. Evidence:
  `output/annotation-workspace-0.7.0-workflow.json` and `.log`.
- Final mode session 63178 exited 0 including remembered dock width, explicit
  missing-main-window failure, preservation of invalid settings and visible
  settings write failure. Compact viewport remains 320 x 406; the framed
  specimen produces 96 colors and 133 changed samples after native repaint.
  Evidence: `output/window-modes-0.7.0-host-final.json`, `.log` and screenshots.
- Final screenshot review found vertical stretching of the batch reference
  input in the taller embedded form. Its stacked input now uses a fixed
  vertical size policy, keeping the label and input aligned. Version 0.7.0 and
  installation/user documentation now describe both window modes.
- Window settings also flush on Qt application exit; closed hosts ignore late
  callbacks. The release check uses the native dock Close button as well as
  direct content/host close. No production renderer behavior was changed.
- Log review separates intentional error-path tracebacks from shutdown output.
  The standalone registered-main-window harness also prints native PyMOL
  `ContentsPanelFrame has been deleted` callbacks after final JSON and process
  shutdown; the no-plugin-dock diagnostic reproduces them. They are retained
  in logs, not suppressed, and are not claimed as a plugin-owned shutdown fix.
- Release session 60820 exited 0 with all window-mode gates, real native Close,
  draft/work preservation, geometry and settings checks passing on the final
  runtime. The 3 pairs produce 6 outputs with coordinate error 0.0; 12 input
  hashes remain unchanged. Native full-window screenshots confirm aligned
  reference controls and a framed molecular view. Evidence:
  `output/window-modes-0.7.0-release.json`, `.log` and screenshots.
- Final compilation passed for `pymolplugins`, `tests/host` and the packager.
  Regression session 12352 exited 0: 757 tests in 20.325 s, `OK`.
  Log: `output/window-modes-0.7.0-unit-final.log`.
- `just package` exited 0 and built the final ZIP. Existing shell initialization
  warnings about missing cargo/local env files and zoxide remain unrelated to
  the successful package build. Log: `output/window-modes-0.7.0-package-build.log`.
- The exact delivery ZIP passed the existing complete-runtime test with its
  archive set to the delivery path: CRC, version, every runtime/entry source
  byte and cache exclusion pass. Result:
  `output/window-modes-0.7.0-package-audit.json`.
- Real Plugin Manager installation session 13065 exited 0: installed version
  0.7.0, 11 modules, 72 canonical + 8 compatibility commands and 6 menus. The
  installed tools use the shared theme and both modes; same-side tabification
  passes. This installation check uses a QMainWindow fixture, with actual
  PyMOLQtGUI geometry covered by the separate release check. No repository
  runtime imports or user-plugin installation changes were used. Evidence:
  `output/plugin-package-0.7.0.json` and `.log`.
- Final batch session 23592 exited 0 after the reference-row size correction:
  56 pairs, 112 outputs, 51 unchanged sources, error/cancellation, all input
  matching modes, compact forms and shared styling pass. Coordinate error is
  0.0. Evidence: `output/batch-workspace-0.7.0-release.json` and `.log`.
- Final source review identified that the separate Windows position correction
  could undo Qt's off-screen recovery after a display-layout change. Reapply
  that saved position only when its full frame fits a current screen; otherwise
  retain Qt's restored geometry. The Host check now closes a window saved far
  off-screen and requires its reopened frame to fit an available screen. This
  source change requires a refreshed ZIP audit and installation gate below.
- Session 70466 confirmed the off-screen case needs a native-frame correction:
  Qt restored the client area but left the frame at `(-1, -18, 962, 739)`.
  The restore now clamps the entire native frame to the selected screen's
  available area when the saved position is unavailable, retaining exact
  positions that still fit. The full-frame visibility assertion is unchanged.
  Evidence: `output/window-modes-0.7.0-release-r2.json` and `.log`.
- Final mode session 58352 exited 0, including full-frame off-screen recovery,
  exact floating geometry, remembered dock side/width, native/menu controls,
  save/preparation/run switches, close protection and visible settings errors.
  Both layouts pass; the compact viewport remains 320 x 406. All 3 pairs and
  6 outputs pass with coordinate error 0.0 and 12 unchanged source hashes.
  Evidence: `output/window-modes-0.7.0-release-r3.json` and `.log`.
- Final compilation passed. Regression session 46414 exited 0: 757 tests in
  27.348 s, `OK`. Log: `output/window-modes-0.7.0-unit-release.log`.
- Refreshed ZIP audit session 37318 exited 0: CRC, version and exact runtime
  source bytes pass for the delivered 420200-byte archive with 97 entries.
  Evidence: `output/window-modes-0.7.0-package-audit-r2.json`.
- Refreshed real Plugin Manager installation session 61620 exited 0: version
  0.7.0, 11 modules, 72 canonical + 8 compatibility commands and 6 menus.
  Both installed tools, their shared theme, mode switches and tabification pass
  from an isolated temporary installation without changing the user's plugins.
  Evidence: `output/plugin-package-0.7.0-r2.json` and `.log`.

## Delivered Artifact

- File: `dist/PymolPlugins-0.7.0.zip`
- Size: 420200 bytes; 97 entries.
- SHA-256: `bd26d4b5a59ddeabc055c53a7e0ce1b056d20244f3a574ade52e5a12dceefb0d`
- Install the original ZIP through Plugin Manager and restart PyMOL on upgrade.
  The existing 0.6.1 artifact and unrelated working-tree changes are preserved.

## Reproduce

Windows checks use the bundled Python from `/mnt/c`. Choose fresh output names
to retain the release evidence:

```bash
repo='\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins'
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\workspace_window_smoke.py" --output "$repo\output\window-modes-check.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\batch_processing_smoke.py" --output "$repo\output\batch-window-check.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\annotator_layout_smoke.py" --output "$repo\output\annotation-window-layout.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\annotator_workflow_smoke.py" --full-gui --output "$repo\output\annotation-window-workflow.json"
/mnt/d/Softwares/Pymol3.1/python.exe -I "$repo\tests\host\plugin_package_smoke.py" "$repo\dist\PymolPlugins-0.7.0.zip" --output "$repo\output\plugin-window-install.json"
```

Run these serially and supervise the actual terminal to completion. Inspect
the final JSON, logs and PNGs rather than treating exit 0 as sufficient.
