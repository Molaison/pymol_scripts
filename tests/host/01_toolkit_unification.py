"""Exercise the native console, style undo, and group facade in isolated PyMOL.

Run with the bundled PyMOL python.exe; output directory is the sole argument.
"""

import contextlib
import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUTPUT = Path(sys.argv[1]).resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)

import pymol
from pymol import cmd

pymol.finish_launching(["pymol", "-cqk"])


def main():
    from pymolplugins.host.plugin import pp_load
    from pymolplugins.host.manifest import COMMAND_TARGETS
    from pymolplugins.host.completion import install_completions
    from pymolplugins.styles.registry import pp_style_apply, pp_style_revert, _REGISTRY
    from pymolplugins.styles.presets import PRESETS
    from pymolplugins.grouping.commands import pp_group, pp_group_by_name

    loaded = pp_load()
    assert loaded["status"] == "ok", loaded
    callbacks = {name: getattr(sys.modules[module], function)
                 for name, (module, function) in COMMAND_TARGETS.items()}
    coverage = install_completions(cmd, callbacks)
    cmd.load(str(ROOT / "data" / "helical_bundle.pdb"), "bundle_1")
    cmd.create("bundle_2", "bundle_1")
    cmd.select("user_selection", "bundle_1 and name CA")
    cmd.group("root", "bundle_1 bundle_2")
    # This is the parser used for actual console Tab completion.
    parser = cmd._parser
    queries = {
        "pp_style_apply clean_": "pp_style_apply clean_outline",
        "pp_style_apply preset=ligand_out": "pp_style_apply preset=ligand_outline",
        "pp_contacts bundle_1, preset=ligand_q": "pp_contacts bundle_1, preset=ligand_quick",
        "pp_group en": "pp_group enable",
        "pp_group info, ro": "pp_group info, root",
        "pp_show cartoon, user_sel": "pp_show cartoon, user_selection",
        "pp_interact_run ligand_quick, surface=": "pp_interact_run ligand_quick, surface=",
        "pp_interact_run ligand_quick, sur": "pp_interact_run ligand_quick, surface=",
    }
    completed = {}
    for text, expected in queries.items():
        actual = parser.complete(text)
        if text.endswith("surface="):
            assert actual in (None, expected), (text, actual)
        else:
            assert actual == expected, (text, actual, expected)
        completed[text] = actual
    cmd.group("new_group", "bundle_2")
    assert parser.complete("pp_group info, new_g") == "pp_group info, new_group"
    cmd.group("new_group", action="empty")
    cmd.delete("new_group")
    assert parser.complete("pp_group info, new_g") is None
    for name, count in coverage.items():
        assert all(name in cmd.auto_arg[index] for index in range(count)), name
        for index in range(count):
            completion = cmd.auto_arg[index][name][0]
            completion.interpret("")
            for parameter in completion.parameters:
                completion.interpret(parameter.name + "=")
    # A path argument and an out-of-position keyword must complete through Tab.
    path = str(OUTPUT / "completion_fixture.pdb").replace("\\", "/")
    Path(path).write_text("END\n", encoding="ascii")
    typed = "pp_style_render filename=" + path[:-4]
    assert parser.complete(typed) == "pp_style_render filename=" + path
    Path(path).unlink()

    pp_group_by_name("bundle_1 or bundle_2", prefix="named_")
    pp_group_by_name("bundle_1 or bundle_2", prefix="named_", replace=1)
    assert cmd.count_atoms("bundle_1") and cmd.count_atoms("bundle_2")
    cmd.distance("contact_line", "bundle_1 and index 1", "bundle_1 and index 2")
    cmd.group("named_bundle", "contact_line")
    cmd.group("outer", "named_bundle")
    info = pp_group("info", "named_bundle")["named_bundle"]
    assert "contact_line" in info["leaves"], info
    cmd.disable("outer")
    pp_group("enable", "named_bundle")
    assert "outer" in cmd.get_names("group_objects", enabled_only=1)
    pp_group("disable", "named_bundle")
    assert "named_bundle" not in cmd.get_names("group_objects", enabled_only=1)
    pp_group("enable", "named_bundle")
    cmd.disable("bundle_2")
    cmd.hide("everything", "all")
    cmd.show("cartoon", "bundle_1")
    cmd.color("cyan", "bundle_1")
    cmd.bg_color("white")
    cmd.orient("bundle_1")
    initial_view = list(cmd.get_view())
    initial_selections = cmd.get_names("selections")
    styles = {}
    names = ("clean_outline", "ao_cartoon", "soft_glossy", "monochrome_bw",
             "goodsell_flat", "ligand_outline")
    for name in names:
        definition = next(item for item in PRESETS if item.name == name)
        before = {key: cmd.get_setting_text(key) for key in definition.global_settings}
        snapshot = pp_style_apply(name, "bundle_1")
        assert snapshot is not None and int(snapshot) > 0, (name, snapshot)
        # The final view field is projection, intentionally set by orthoscopic.
        assert list(cmd.get_view())[:17] == initial_view[:17]
        assert cmd.get_names("selections") == initial_selections
        cmd.png(str(OUTPUT / (name + ".png")), width=480, height=360, ray=1, quiet=1)
        pp_style_revert(int(snapshot))
        after = {key: cmd.get_setting_text(key) for key in definition.global_settings}
        assert before == after, (name, before, after)
        assert list(cmd.get_view()) == initial_view
        styles[name] = {"snapshot": int(snapshot), "global_settings_restored": True,
                        "orientation_preserved": True, "view_restored": True,
                        "selection_names_preserved": True,
                        "image": name + ".png"}
    assert not _REGISTRY.snapshots
    from pymolplugins.annotation import plugin as annotation
    assert parser.complete("sa_load_groups par") == "sa_load_groups parent="
    assert parser.complete("sa_annotate Goo") == "sa_annotate Good"
    assert parser.complete("sa_group_candidate en") == "sa_group_candidate enabled="
    annotation_callbacks = {name: entry[0] for name, entry in cmd.keyword.items()
                            if name.startswith("sa_")}
    annotation_coverage = install_completions(cmd, annotation_callbacks)
    for name, count in annotation_coverage.items():
        for index in range(count):
            cmd.auto_arg[index][name][0].interpret("")
    return {"status": "passed", "version": cmd.get_version()[0],
            "canonical_and_compat_commands": len(coverage),
            "parameter_slots": sum(coverage.values()), "completion": completed,
            "annotator_commands": len(annotation_coverage),
            "annotator_parameter_slots": sum(annotation_coverage.values()),
            "group": info, "styles": styles}


if __name__ == "__main__":
    report = {"status": "failed"}
    try:
        with (OUTPUT / "native.log").open("w", encoding="utf-8") as log:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                report = main()
    except Exception:
        report["error"] = traceback.format_exc()
    (OUTPUT / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "styles"}), flush=True)
    cmd.quit(0 if report["status"] == "passed" else 1)
