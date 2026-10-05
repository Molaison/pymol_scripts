# PyMOL Plugins Development Guide

## Project Structure
- Python scripts for PyMOL visualization and analysis
- Mix of standalone scripts and Qt-based plugins
- Primary target: PyMOL 2.x with Qt interface

## Build/Test Commands
```bash
# No formal build system - scripts run directly in PyMOL
# Test individual script: `run script_name.py` in PyMOL console
# Test plugin: load via Plugin Manager or `run plugin_name.py`
# Validate syntax: `python -m py_compile script_name.py`
```

## Code Style Guidelines

### Imports
```python
from pymol import cmd          # Core PyMOL API first
from pymol.Qt import QtWidgets # PyMOL Qt components
import standard_library        # Standard lib third
import traceback              # Exception handling
```

### Function Style
- Use `snake_case` for functions and variables
- Docstrings with Args/Returns for public functions
- Keep functions under 50 lines, classes under 200
- Single responsibility per function

### Error Handling
```python
try:
    # PyMOL operations
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()  # Always include traceback
```

### PyMOL Integration
- Use `cmd.extend()` to register new commands
- Implement `__init_plugin__()` for menu integration
- Store plugin state in global variables if needed
- Use `cmd.get_setting_text()` to preserve user settings

### File Organization
- One plugin per file
- Keep related visualization scripts in `visualization/`
- Command-line tools as standalone scripts in root

## Windows Host Validation Notes
- Maximized-window checks must compare primary actions with the actual Windows
  taskbar rectangle. With auto-hide enabled, both native `rcWork` and Qt
  `availableGeometry()` can include a visible taskbar; `visibleRegion()` alone
  misses this overlap. Also expand/collapse annotation fields without resizing
  the host to exercise scroll-area size-hint updates.
- Batch processing must use unique temporary names per pair. In Windows PyMOL
  3.1.3.1 GUI tests, recycling deleted names eventually left molecule objects
  present but their named selections invalid. Cover long sequences, not just
  one pair; keep fixed references read-only and copy them into each pair.
- A Host process exit code is not sufficient evidence. Persist final JSON
  before `cmd.quit()`; closing `PyMOLQtGUI` early can exit before assertions or
  tracebacks are recorded. Verify output coordinates and source hashes.
- Standalone Qt harnesses must retain the QApplication Python object until
  final JSON is written. Its destruction when a helper returns can terminate
  Windows PyMOL with code 9 after assertions but before the result is recorded.
- In Qt GUI tests, click the menu-arrow subcontrol of `MenuButtonPopup` tools;
  their main button executes a different action. Start undo-state checks from
  an explicitly enabled test baseline instead of the unfinished startup state.
- Styled Qt layout checks must inspect table header/data viewports separately:
  opaque child painting is excluded from the parent's `visibleRegion`. Scroll
  padded inputs with `ensureVisible` and their full mapped bounds;
  `ensureWidgetVisible` can expose only the input cursor. Wait for modal window
  activation and the actual completion popup before sending acceptance keys.
- Qt stylesheet minimum sizes can override `setFixedSize` during polishing.
  Keep icon-role min/max dimensions explicit in the shared stylesheet and test
  both standalone and annotation-menu entry paths.
- Workspace content is a QWidget inside a native QDockWidget. Qt acceptance
  must register its real main-window fixture with `pymol.gui.get_qtwindow()`,
  isolate `PYMOLPLUGINS_UI_SETTINGS`, resize the floating host, and compare
  activation to `content.window()`. Never use activeWindow as the desktop owner.
- Check mode switches during real asynchronous saves/preparation/runs. Native
  float controls are visible on embedded docks; a floating Windows title bar
  uses OS controls instead. Test both menu/native transitions, vetoed close,
  reopen position/width, and restoration of only the plugin-owned constraints.
- In the standalone Windows Qt harness, QTest waits alone may leave a stale
  OpenGL framebuffer even without plugin docks. Explicitly repaint before
  pixel comparisons, and use native screen capture for full-window images;
  QWidget.grab may omit a QGLWidget surface. Do not label this as proof of
  unattended redraw timing or silently replace a failed renderer assertion.
- QLineEdit's native completer can insert the highlighted first history item
  before acceptance. Source-path history must own insertion through activation
  and explicit Tab/Enter; test paste followed immediately by an import click.
- Completion acceptance must handle key events on both the input and the
  popup. QCompleter can hide the popup then forward Enter to `returnPressed`,
  prematurely submitting a filter. Test actual popup Return/keypad Enter,
  then submission with the popup closed; input-only Tab tests miss this case.
- Directory scans and legacy inline annotation reads belong in import workers.
  CSV state-path identity also scans the parent directory: prepare/cache that
  path before Qt commit, and never calculate it when queuing a background save.
  Reuse prepared structure paths without probing remote files again on commit.
- Source import acceptance is `tests/host/annotator_source_review_smoke.py`.
  Wait for both asynchronous headers and the dependent import. Record injected
  slow-I/O responsiveness separately from real remote-source timing.
- SSH acceptance must include a default-config URI on the bundled Windows
  Python, the configured login program, and the real source. Explicit `-F`
  fixtures miss Python 3.10's empty-query parsing failure. Keep one authenticated
  read connection so per-operation OTP leases do not stall an import; never
  reset or bypass the login program's code-allocation policy.
