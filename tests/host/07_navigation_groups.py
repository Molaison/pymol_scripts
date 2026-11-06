"""Verify group visibility in an isolated native PyMOL process.

Run: D:/Softwares/Pymol3.1/python.exe tests/host/07_navigation_groups.py
Writes output/07_navigation_groups.json; never opens an existing session.
"""

import importlib
import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pymol
from pymol import cmd

pymol.finish_launching(["pymol", "-cqk"])
nav = importlib.import_module("pymolplugins.navigation.commands")
report = {"version": cmd.get_version()[0], "checks": {}}


def snapshot():
    records = cmd.get_session("", partial=1, quiet=1)["names"]
    states = {record[0]: bool(record[2]) for record in records if record}
    parents = {record[0]: record[6] for record in records if record}
    visible = set()
    for name, enabled in states.items():
        parent = parents[name]
        while enabled and parent:
            enabled = states[parent]
            parent = parents[parent]
        if enabled:
            visible.add(name)
    return states, visible


try:
    for index, name in enumerate(("nav_live", "nav_hidden", "nav_deep", "nav_sibling", "nav_other", "nav_overlay")):
        cmd.pseudoatom(name, pos=[index * 3, 0, 0])
    cmd.group("nav_deep_group", "nav_deep")
    cmd.group("nav_inner", "nav_live nav_hidden nav_deep_group")
    cmd.group("nav_outer", "nav_inner nav_sibling")
    cmd.group("nav_other_group", "nav_other")
    cmd.disable("nav_hidden")
    cmd.disable("nav_deep_group")
    cmd.disable("nav_outer")
    initial, _ = snapshot()

    started = nav.pp_nav_start(source="groups", entries="nav_outer,nav_other_group", zoom_policy="none")
    assert started.get("status") != "error", started
    states, visible = snapshot()
    assert "nav_live" in visible and "nav_hidden" not in visible and "nav_deep" not in visible
    assert not states["nav_hidden"] and not states["nav_deep_group"]
    nav.pp_nav_next()
    states, visible = snapshot()
    assert "nav_other" in visible and "nav_live" not in visible and states["nav_live"]
    assert "nav_overlay" in visible
    nav.pp_nav_pin()
    nav.pp_nav_prev()
    assert {"nav_live", "nav_other"}.issubset(snapshot()[1])
    nav.pp_nav_stop()
    assert snapshot()[0] == initial
    report["checks"]["containers_children_pins_stop"] = "passed"

    nav.pp_nav_start(source="groups", entries="nav_inner,nav_other_group", zoom_policy="none")
    states, visible = snapshot()
    assert states["nav_outer"] and "nav_live" in visible
    assert "nav_sibling" not in visible and "nav_hidden" not in visible and "nav_deep" not in visible
    assert "nav_overlay" in visible
    active = states
    nav.pp_nav_register_hook("pp_missing_navigation_command {object}")
    failed = nav.pp_nav_next()
    assert failed["rollback"] == "restored", failed
    assert snapshot()[0] == active
    assert nav.pp_nav_context(quiet=1)["objects"] == ["nav_live", "nav_hidden", "nav_deep"]
    nav.pp_nav_stop()
    assert snapshot()[0] == initial
    report["checks"]["nested_ancestors_external_siblings_native_hook_rollback"] = "passed"

    nav.pp_nav_start(source="objects", entries="nav_deep,nav_other", zoom_policy="none")
    states, visible = snapshot()
    assert {"nav_outer", "nav_inner", "nav_deep_group", "nav_deep"}.issubset(visible)
    assert not {"nav_live", "nav_hidden", "nav_sibling"}.intersection(visible)
    replaced = nav.pp_nav_start(source="groups", entries="nav_inner,nav_other_group", replace=1, zoom_policy="none")
    assert replaced.get("status") != "error", replaced
    states, visible = snapshot()
    assert "nav_live" in visible and not states["nav_deep_group"]
    nav.pp_nav_next()
    cmd.disable("nav_other_group")
    cmd.enable("nav_outer")
    cmd.enable("nav_inner")
    synced = nav.pp_nav_sync_from_scene()
    assert synced["current_entry_ids"] == ["group:nav_inner"], synced
    nav.pp_nav_stop()
    assert snapshot()[0] == initial
    report["checks"]["objects_ancestors_replacement_sync_restore"] = "passed"
    report["status"] = "passed"
except Exception:
    report["status"] = "failed"
    report["error"] = traceback.format_exc()
    raise
finally:
    output = ROOT / "output" / "07_navigation_groups.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
