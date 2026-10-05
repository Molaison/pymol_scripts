# Structure Annotator save controls, 2026-09-22

The annotation save button and Saved/Not saved status now stay below the form
fields instead of consuming the full height of a maximized PyMOL window. The
editor remains scrollable in compact windows. Expanding or collapsing Notes
updates its preferred height immediately, without requiring a window resize.

## Observed cause

On the Windows host, taskbar auto-hide is enabled (`ABM_GETSTATE = 1`) while the
taskbar remains visible. Both Windows `rcWork` and Qt `availableGeometry()`
include the entire screen. Bare PyMOL, without this plugin, also maximizes to
that area. No system taskbar preference or PyMOL window geometry was changed.

The original annotation footer placed Save and next at `[446, 1379, 170, 43]`
in screen coordinates. The visible taskbar starts at y=1390, covering the
button text and status. The ordinary Qt visible-region checks passed despite
this overlap. A regression run loading the original `review_widgets.py` copy
reproduced the taskbar intersection assertion; the worktree was not reverted.

## Verification

Real Windows PyMOL 3.1.3.1 / Qt 5.15.8, 3440 x 1440 desktop, native taskbar
rectangle `[0, 1390, 3440, 50]`. The original review was on a 2560 x 1440 desktop;
the screen size changed before the fix verification.

`tests/host/workspace_maximized_smoke.py` passed:

- Normal, maximized, restored and maximized again: save/status are visible,
  inside the screen work area and outside the actual taskbar rectangle.
- Clicking Good and Save and next persists the value and advances to row 2.
- Restoring/maximizing preserves the next candidate's unsaved draft.
- Notes expand and collapse without a host resize; the editor and save row
  adjust together, and the expanded field is visible without scrolling.
- Floating 1000 x 720 and 760 x 560 layouts retain visible save/status and
  usable Notes; the smaller layout scrolls as needed for the score cards.
- Saving the second candidate persists both Good and the entered note.

Artifacts under `output/`:

- `annotation-save-placement-before-final-20260922.json` and `.log`: original
  source fails specifically on taskbar overlap.
- `annotation-save-placement-accepted-20260922.json` and `.log`: final source
  passes, with per-state screenshots sharing the same filename prefix.

The first size-policy-only implementation retained Qt's cached content height
after expanding Notes. The `annotation-save-placement-20260922` and
`annotation-save-placement-sizing-20260922` records preserve that failure;
`_AnnotationScrollArea` now supplies the current content height and updates its
layout when the content requests a new layout. The first compact test assumed
zero scrolling even when the score cards plus form exceeded the available
height; the final check permits necessary compact scrolling while requiring
visible controls and no scrolling in the roomy layout.

The existing `annotator_layout_smoke.py` was also attempted. It fails before
reaching its layout checks because its initial import-page assertion still
accesses the removed `directory_mode_button` attribute. That suite is not
claimed as passed; evidence is `annotation-save-placement-layout-20260922.json`
and `.log`.

## Reproduce

From a Windows-local working directory with the bundled Python:

```powershell
D:\Softwares\Pymol3.1\python.exe -I \\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\tests\host\workspace_maximized_smoke.py --output C:\Temp\annotation-save-placement.json
```

The script uses disposable sources, isolated plugin preferences and targeted
Qt input. It writes final JSON before quitting PyMOL. It does not change the
user's installed plugin or taskbar settings. This check covers the default
Quality/Notes form on the tested Windows host, not other PyMOL/Qt platforms or
multi-monitor taskbar configurations.
