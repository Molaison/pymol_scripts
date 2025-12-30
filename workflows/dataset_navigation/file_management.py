from pymol import cmd
import os
import re

# Global state variables
_current_index = 0
_current_path = "."
_bucket_index = 0
_bucket_size = 10
_pattern = r"^.+\.pdb$|^.+\.cif$"
_pinned_objects = set()
_auto_bucket_mode = True
_switch_commands = []


def load_structures(path=".", pattern=None, bucket_size=10):
    """
    Load structure files from directory with smart batching.
    
    Args:
        path (str): Directory containing structure files
        pattern (str): Regex pattern for file matching
        bucket_size (int): Number of files per batch
    """
    global _current_path, _bucket_size, _pattern
    
    _current_path = path
    _bucket_size = bucket_size
    if pattern:
        _pattern = pattern
    
    files = _get_structure_files(path)
    if not files:
        print(f"No structure files found in {path}")
        return
    
    print(f"Found {len(files)} structure files")
    print(f"Loading first batch of {min(bucket_size, len(files))} files...")
    
    _load_batch(0, files)


def _get_structure_files(path):
    """Get list of structure files matching pattern."""
    try:
        files = os.listdir(path)
        pattern = re.compile(_pattern)
        return sorted([f for f in files if pattern.match(f)])
    except OSError as e:
        print(f"Error reading directory {path}: {e}")
        return []


def _load_batch(batch_index, files):
    """Load a specific batch of files."""
    global _bucket_index, _current_index
    
    _bucket_index = batch_index
    start_idx = batch_index * _bucket_size
    end_idx = min(start_idx + _bucket_size, len(files))
    batch_files = files[start_idx:end_idx]
    
    # Clear non-pinned objects
    _clear_non_pinned()
    
    # Load new batch
    for file in batch_files:
        obj_name = _get_object_name(file)
        if obj_name not in cmd.get_object_list():
            try:
                cmd.load(os.path.join(_current_path, file), obj_name)
            except Exception as e:
                print(f"Error loading {file}: {e}")
    
    # Show first structure
    _current_index = 0
    _show_current_structure()


def _get_object_name(filename):
    """Get PyMOL object name from filename."""
    return filename.replace(".pdb", "").replace(".cif", "").replace(".sdf", "")


def _clear_non_pinned():
    """Remove all non-pinned objects."""
    for obj in cmd.get_object_list():
        if obj not in _pinned_objects:
            cmd.delete(obj)


def _show_current_structure():
    """Display current structure and execute switch commands."""
    structures = _get_current_structures()
    if not structures:
        print("No structures available")
        return
    
    if _current_index >= len(structures):
        _current_index = 0
    elif _current_index < 0:
        _current_index = len(structures) - 1
    
    current = structures[_current_index]
    
    # Disable all non-pinned, enable current + pinned
    for obj in structures:
        cmd.disable(obj)
    
    cmd.enable(current)
    for pinned in _pinned_objects:
        if pinned in cmd.get_object_list():
            cmd.enable(pinned)
    
    # Execute registered commands
    _execute_switch_commands(current)
    
    cmd.zoom()
    print(f"Showing: {current} ({_current_index + 1}/{len(structures)})")


def _get_current_structures():
    """Get list of current non-pinned structures."""
    return [obj for obj in cmd.get_object_list() if obj not in _pinned_objects]


def _execute_switch_commands(object_name):
    """Execute all registered switch commands."""
    for command in _switch_commands:
        try:
            formatted_cmd = command.format(object=object_name)
            cmd.do(formatted_cmd)
        except Exception as e:
            print(f"Error executing command '{command}': {e}")


# Navigation commands
@cmd.set_key("right")
def next_structure():
    """Move to next structure in current batch."""
    global _current_index
    
    structures = _get_current_structures()
    if not structures:
        return
    
    _current_index += 1
    
    # Auto-advance to next batch if at end and auto mode enabled
    if _current_index >= len(structures) and _auto_bucket_mode:
        next_batch()
        return
    
    _show_current_structure()


@cmd.set_key("left") 
def prev_structure():
    """Move to previous structure in current batch."""
    global _current_index
    
    structures = _get_current_structures()
    if not structures:
        return
    
    _current_index -= 1
    
    # Auto-retreat to previous batch if at beginning and auto mode enabled
    if _current_index < 0 and _auto_bucket_mode:
        prev_batch()
        return
    
    _show_current_structure()


@cmd.set_key("CTRL-right")
def next_batch():
    """Load next batch of structures."""
    files = _get_structure_files(_current_path)
    if not files:
        return
    
    total_batches = (len(files) + _bucket_size - 1) // _bucket_size
    next_batch_idx = (_bucket_index + 1) % total_batches
    
    print(f"Loading batch {next_batch_idx + 1}/{total_batches}")
    _load_batch(next_batch_idx, files)


@cmd.set_key("CTRL-left")
def prev_batch():
    """Load previous batch of structures."""
    files = _get_structure_files(_current_path)
    if not files:
        return
    
    total_batches = (len(files) + _bucket_size - 1) // _bucket_size
    prev_batch_idx = (_bucket_index - 1) % total_batches
    
    print(f"Loading batch {prev_batch_idx + 1}/{total_batches}")
    _load_batch(prev_batch_idx, files)


# Object pinning
def pin_current():
    """Pin currently enabled structure so it persists across batches."""
    enabled = cmd.get_names("objects", enabled_only=True)
    non_pinned = [obj for obj in enabled if obj not in _pinned_objects]
    
    if non_pinned:
        obj = non_pinned[0]
        _pinned_objects.add(obj)
        print(f"Pinned: {obj}")
        print(f"Total pinned: {list(_pinned_objects)}")
    else:
        print("No unpinned objects to pin")


def unpin_current():
    """Unpin currently enabled structure."""
    enabled = cmd.get_names("objects", enabled_only=True)
    pinned = [obj for obj in enabled if obj in _pinned_objects]
    
    if pinned:
        obj = pinned[0]
        _pinned_objects.discard(obj)
        print(f"Unpinned: {obj}")
        print(f"Remaining pinned: {list(_pinned_objects)}")
    else:
        print("No pinned objects currently enabled")


def clear_pins():
    """Remove all pins."""
    count = len(_pinned_objects)
    _pinned_objects.clear()
    print(f"Cleared {count} pinned objects")


# Selection management
@cmd.set_key("CTSH-S")
def save_selected():
    """Save current structure to 'selected' subdirectory."""
    current = _get_current_structure()
    if not current:
        print("No current structure to save")
        return
    
    selected_dir = os.path.join(_current_path, "selected")
    os.makedirs(selected_dir, exist_ok=True)
    
    save_path = os.path.join(selected_dir, f"{current}.pdb")
    cmd.save(save_path, current)
    print(f"Saved {current} to selected/")


def _get_current_structure():
    """Get currently displayed structure."""
    enabled = cmd.get_names("objects", enabled_only=True)
    non_pinned = [obj for obj in enabled if obj not in _pinned_objects]
    return non_pinned[0] if non_pinned else None


# Switch command registration
def register_switch_command(command):
    """Register command to execute when switching structures.
    
    Args:
        command (str): PyMOL command with {object} placeholder
    """
    if command not in _switch_commands:
        _switch_commands.append(command)
        print(f"Registered: {command}")


def clear_switch_commands():
    """Clear all registered switch commands."""
    count = len(_switch_commands)
    _switch_commands.clear()
    print(f"Cleared {count} switch commands")


# Convenience functions
def auto_show_sticks():
    """Auto-show sticks representation for all structures."""
    register_switch_command("show sticks, {object}")


def auto_show_cartoon():
    """Auto-show cartoon representation for all structures."""
    register_switch_command("show cartoon, {object}")


# Settings
def set_auto_batch(enabled=True):
    """Enable/disable automatic batch advancement."""
    global _auto_bucket_mode
    _auto_bucket_mode = enabled
    status = "enabled" if enabled else "disabled"
    print(f"Auto batch mode {status}")


def set_batch_size(size):
    """Set number of structures per batch."""
    global _bucket_size
    _bucket_size = max(1, int(size))
    print(f"Batch size set to {_bucket_size}")


# Register PyMOL commands
cmd.extend("load_structures", load_structures)
cmd.extend("next_structure", next_structure)
cmd.extend("prev_structure", prev_structure)
cmd.extend("next_batch", next_batch)
cmd.extend("prev_batch", prev_batch)
cmd.extend("pin_current", pin_current)
cmd.extend("unpin_current", unpin_current)
cmd.extend("clear_pins", clear_pins)
cmd.extend("save_selected", save_selected)
cmd.extend("register_switch_cmd", register_switch_command)
cmd.extend("clear_switch_cmds", clear_switch_commands)
cmd.extend("auto_show_sticks", auto_show_sticks)
cmd.extend("auto_show_cartoon", auto_show_cartoon)
cmd.extend("set_auto_batch", set_auto_batch)
cmd.extend("set_batch_size", set_batch_size)

# Aliases for convenience
cmd.extend("load_files", load_structures)
cmd.extend("next", next_structure)
cmd.extend("prev", prev_structure)
cmd.extend("pin", pin_current)
cmd.extend("unpin", unpin_current)

print("File Management module loaded successfully!")
print("Usage: load_structures '/path/to/structures'")
print("Navigation: next/prev structure, next_batch/prev_batch")
print("Pinning: pin/unpin current structure")