"""Real PyMOL checks; run with bundled python, passing an output JSON path.

Uses an isolated pymol2 instance. No molecular/scene APIs are mocked.
"""

import json
import os
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pymol2
from pymolplugins.interactions import controller as interactions
from pymolplugins.tasks import commands as tasks

with patch.dict(os.environ, {"PP_HOST_SMOKE_AUTORUN": "0"}):
    from tests.host.host_smoke import _ligand_complex_pdb


def source_snapshot(cmd):
    rows = []
    for state in (1, 2):
        cmd.iterate_state(
            state, "complex or left_side or right_side",
            "rows.append((model, index, name, resn, resi, chain, segi, "
            "elem, formal_charge, x, y, z, color, reps))",
            space={"rows": rows},
        )
    return rows


def check_scenes(cmd):
    cmd.read_pdbstr(_ligand_complex_pdb(), "complex")
    cmd.create("complex", "complex", 1, 2)
    cmd.translate([50, 0, 0], "complex and organic", state=2)
    cmd.create("left_side", "complex and chain A", 1, 1)
    cmd.create("right_side", "complex and chain B", 1, 1)
    cmd.alter("right_side", "chain='A'")
    cmd.select("user_keep", "complex and resn LIG")
    cmd.select("taken", "complex and chain A")
    cmd.disable("user_keep")
    baseline_names = set(cmd.get_names("all"))
    baseline_selections = {
        name: tuple(cmd.index(name)) for name in cmd.get_names("selections")
    }
    baseline_atoms = source_snapshot(cmd)
    baseline_enabled = set(cmd.get_names("all", enabled_only=1))
    controller = interactions.get_controller(cmd)
    checks = []

    def assert_boundary():
        assert {
            name: tuple(cmd.index(name)) for name in cmd.get_names("selections")
        } == baseline_selections, "named selections changed"
        assert source_snapshot(cmd) == baseline_atoms, "source atoms/style changed"
        enabled = set(cmd.get_names("all", enabled_only=1)) & baseline_names
        assert enabled == baseline_enabled, (baseline_enabled, enabled)

    def inspect_group(group):
        assert_boundary()
        record = controller.get(group)
        assert all(entry.kind != "selection" for entry in record.manifest.created)
        assert set(record.manifest.created_names) == set(cmd.get_names("all")) - baseline_names
        return record

    def clear_group(group):
        interactions.pp_interact_clear(group)
        assert set(cmd.get_names("all")) == baseline_names
        assert_boundary()

    for preset in ("interchain_basic", "polar_qc"):
        group = interactions.pp_interact_run(preset, source="complex", group="result")
        record = inspect_group(group)
        assert record.result.interactions, preset
        assert all(
            participant.atom.object == "complex"
            for item in record.result.interactions for participant in item.participants
        )
        checks.append({"case": preset, "counts": dict(record.result.summary["counts_by_type"])})
        clear_group(group)

    group = interactions.pp_interact_run(
        "interchain_basic", source="left_side or right_side",
        partner1="left_side", partner2="right_side", group="result",
    )
    record = inspect_group(group)
    assert record.result.interactions
    assert all(
        {part.atom.object for part in item.participants} == {"left_side", "right_side"}
        for item in record.result.interactions
    ), "same-chain objects lost their object boundary"
    checks.append({"case": "same_chain_different_objects", "counts": dict(record.result.summary["counts_by_type"])})
    clear_group(group)

    for preset in ("ligand_contacts", "ligand_quick", "ligand_site"):
        group = interactions.pp_interact_run(
            preset, source="complex", receptor="polymer", ligand="resn LIG", group="result",
        )
        record = inspect_group(group)
        if preset != "ligand_quick":
            assert record.result.interactions, preset
        assert all(
            any(part.residue.resn == "LIG" for part in item.participants)
            for item in record.result.interactions
        )
        checks.append({"case": preset, "counts": dict(record.result.summary["counts_by_type"])})
        clear_group(group)

    for label, options in (
        ("state_2_far_ligand", {"state": 2, "ligand": "resn LIG"}),
        ("disjoint_far_ligand", {"ligand": "resn DRG"}),
    ):
        group = interactions.pp_interact_run(source="complex", group="result", **options)
        record = inspect_group(group)
        assert not record.result.interactions, label
        assert record.result.run.source.state == options.get("state", 1)
        checks.append({"case": label, "counts": {}})
        clear_group(group)

    for label, options in (
        ("empty_partner", {"preset": "interchain_basic", "partner1": "none", "partner2": "chain B"}),
        ("outside_source", {"preset": "interchain_basic", "partner1": "left_side", "partner2": "chain B"}),
        ("overlapping_partners", {"preset": "interchain_basic", "partner1": "chain A", "partner2": "chain A"}),
        ("existing_user_selection_output", {"group": "taken", "ligand": "resn LIG"}),
    ):
        try:
            interactions.pp_interact_run(source="complex", **options)
        except ValueError as exc:
            checks.append({"case": label, "rejected": str(exc)})
        else:
            raise AssertionError("expected rejection: " + label)
        assert set(cmd.get_names("all")) == baseline_names
        assert_boundary()

    first = tasks.pp_contacts("complex", focus="resn LIG", group="result")
    assert first["status"] == "changed", first
    old_record = inspect_group(first["result"])
    second = tasks.pp_contacts("complex", focus="resn LIG", group="result")
    assert second["status"] == "changed", second
    record = inspect_group(second["result"])
    assert old_record.result.run.id != record.result.run.id
    assert not (set(old_record.manifest.created_names) - {"result"}) & set(cmd.get_names("all"))
    try:
        controller.clear(old_record.owner_reference)
    except interactions.StaleInteractionOwnerError:
        pass
    else:
        raise AssertionError("stale owner cleared the replacement")
    inspect_group("result")
    undone = tasks.pp_undo(second["operation_id"])
    assert undone["status"] == "restored", undone
    assert set(cmd.get_names("all")) == baseline_names
    assert_boundary()
    checks.append({"case": "pp_contacts_replace_stale_owner_and_undo", "status": "passed"})
    return {"checks": checks, "selection_names": sorted(baseline_selections),
            "source_atoms_and_style_preserved": True, "exact_clear_and_undo": True}


if __name__ == "__main__":
    output = Path(sys.argv[1])
    report = {"status": "failed"}
    try:
        with pymol2.PyMOL() as host:
            with patch.object(interactions, "_PYMOL_CMD", host.cmd), patch.object(tasks, "cmd", host.cmd):
                report.update(check_scenes(host.cmd))
                report["pymol_version"] = host.cmd.get_version()[0]
                report["status"] = "passed"
    except Exception:
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
