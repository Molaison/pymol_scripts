"""Scoped visibility updates for the PyMOL 3.1 Qt contents panel.

PyMOL 2.x and unknown panel layouts keep their native update path. No installed
PyMOL files, global settings, or group-membership snapshots are changed.
"""

import traceback


def cancel_visibility_update():
    """Restore a pending panel callback, if this host has one."""
    panel = _contents_panel()
    restore = getattr(panel, "_pp_restore_visibility", None)
    if restore:
        restore()


def _contents_panel():
    try:
        from pymol import gui
        return getattr(gui.get_qtwindow(), "contentsPanelFrame", None)
    except (ImportError, AttributeError, RuntimeError):
        return None


def arm_visibility_update(changes):
    """Reuse tiles during a known sequence of visibility-only changes.

    Args:
        changes: Object names mapped to their requested enabled states.
    """
    panel = _contents_panel()
    if panel is None:
        return
    cancel_visibility_update()
    if not changes or getattr(panel, "_needsUpdate", True):
        return
    rows = [list(row) for row in getattr(panel, "raw_rec_list", ())]
    if not rows or any(len(row) != 5 for row in rows):
        return
    if not set(changes).issubset({row[0] for row in rows}):
        return
    box = getattr(panel, "content_sa_gb", None)
    if not getattr(box, "object_tile_list", None):
        return
    expected = [
        row[:4] + [int(changes[row[0]])] if row[0] in changes else row
        for row in rows
    ]
    original = panel.invalidate

    def restore():
        if getattr(panel, "_pp_restore_visibility", None) is restore:
            panel.invalidate = original
            del panel._pp_restore_visibility

    def invalidate(new_rows, group_rows):
        incoming = [list(row) for row in new_rows]
        group_states = {
            row[0]: row[5] for row in group_rows if len(row) > 5 and row[2] == 12
        }
        compatible = (
            not panel._needsUpdate
            and len(rows) == len(incoming)
            and all(old[:4] == new[:4] and (
                old[0] in changes or old[4] == new[4]
            ) for old, new in zip(rows, incoming))
            and all(panel.group_toggle_info.get(name) == value
                    for name, value in group_states.items())
        )
        if compatible:
            try:
                _update_tiles(panel, new_rows)
                if incoming == expected:
                    restore()
                return
            except (AttributeError, KeyError, RuntimeError, TypeError):
                traceback.print_exc()
        restore()
        original(new_rows, group_rows)

    panel._pp_restore_visibility = restore
    panel.invalidate = invalidate
    return restore


def _update_tiles(panel, rows):
    box = panel.content_sa_gb
    visible = {row[0]: row[4] for row in rows}
    changed = {rec.name for rec in panel.rec_list
               if rec.visible != visible[rec.name]}
    for rec in panel.rec_list:
        rec.visible = visible[rec.name]
    for name, group in box.grouped_recs.items():
        group.active = visible[name]
    parent_activity = {
        name: box._check_group_stack(name)[1] for name in box.grouped_recs
    }
    for tile in box.object_tile_list:
        rec = tile.rec
        active = (parent_activity[rec.group_name] if rec.group_name else
                  visible[rec.name] if rec.obj_type == 12 else None)
        if rec.name in changed:
            tile.radioButton.setChecked(bool(rec.visible))
            tile._update_activity_style()
        if rec.group_name and (
            tile.group_active != active or rec.name in changed
        ):
            tile._update_properties(
                "active_group_inactive", bool(rec.visible and not active)
            )
        tile.group_active = active
    panel.raw_rec_list = rows
    panel.update()
