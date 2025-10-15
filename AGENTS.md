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