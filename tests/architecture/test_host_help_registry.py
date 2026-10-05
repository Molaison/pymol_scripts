import json
from pathlib import Path
import subprocess
import sys
import unittest

from pymolplugins.host.help_catalog import (
    command_help,
    default_tasks,
    exact_help,
    help_topics,
    search_help,
)
from pymolplugins.host.manifest import (
    AUTOLOADED_COMPAT_COMMANDS,
    CANONICAL_COMMANDS,
    COMMAND_TARGETS,
    declared_command_names,
)
from pymolplugins.interactions.presets import PRESETS as INTERACTION_PRESETS
from pymolplugins.styles.presets import CANONICAL_PRESETS as STYLE_PRESETS


ROOT = Path(__file__).resolve().parents[2]


def _run_host_probe(body: str, before_import: str = ""):
    source = r'''
import json
import sys
import types

sys.path.insert(0, {root!r})

class FakeCmd:
    def __init__(self):
        self.keyword = {{}}
        self.auto_arg = {{0: {{}}}}

    def extend(self, name, function):
        self.keyword[name] = [function, 0, 0, ",", 11]

    def Shortcut(self, values):
        return tuple(values)

cmd = FakeCmd()
pymol_module = types.ModuleType("pymol")
pymol_module.__path__ = []
pymol_module.cmd = cmd
qt_module = types.ModuleType("pymol.Qt")
qt_module.QtCore = None
pymol_module.Qt = qt_module
sys.modules["pymol"] = pymol_module
sys.modules["pymol.Qt"] = qt_module

{before_import}
import pymolplugins.host.plugin as plugin

{body}
'''.format(root=str(ROOT), before_import=before_import, body=body)
    completed = subprocess.run(
        [sys.executable, "-I", "-c", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "probe failed\nstdout:\n%s\nstderr:\n%s"
            % (completed.stdout, completed.stderr)
        )
    marker = next(
        line for line in reversed(completed.stdout.splitlines()) if line.startswith("RESULT=")
    )
    return json.loads(marker.split("=", 1)[1])


class HostManifestTests(unittest.TestCase):
    def test_declares_exact_canonical_and_autoloaded_compat_surface(self):
        canonical = {
            name for values in CANONICAL_COMMANDS.values() for name in values
        }
        compat = {
            name for values in AUTOLOADED_COMPAT_COMMANDS.values() for name in values
        }
        self.assertEqual(len(canonical), 72)
        self.assertEqual(
            compat,
            {
                "align_in_groups",
                "group_objects_by_name",
                "merge_groups",
                "merge_models_by_name",
                "sbs",
                "sbs_help",
                "select_sequence",
                "seq_select",
            },
        )
        self.assertEqual(len(declared_command_names()), 80)
        self.assertEqual(len(set(declared_command_names())), 80)
        self.assertEqual(set(COMMAND_TARGETS), canonical | compat)


class HelpCatalogTests(unittest.TestCase):
    def test_default_tasks_are_bounded_and_actionable(self):
        tasks = default_tasks()
        self.assertGreaterEqual(len(tasks), 8)
        self.assertLessEqual(len(tasks), 12)
        required = {
            "purpose",
            "minimal_usage",
            "example",
            "mutates",
            "undo",
            "next",
        }
        for task in tasks:
            self.assertTrue(required.issubset(task), task)
            self.assertTrue(task["minimal_usage"])
            self.assertTrue(task["example"])

    def test_every_registered_preset_has_exact_help(self):
        expected = set(STYLE_PRESETS) | set(INTERACTION_PRESETS.names())
        self.assertEqual(
            {name for name in expected if exact_help(name) is not None},
            expected,
        )
        self.assertTrue(expected.issubset(set(help_topics())))
        self.assertIn("pp_nav_start", help_topics())
        self.assertIn("surface", help_topics())

    def test_every_canonical_command_has_the_common_help_contract(self):
        required = {
            "purpose",
            "minimal_usage",
            "example",
            "mutates",
            "undo",
            "next",
        }
        for commands in CANONICAL_COMMANDS.values():
            for name in commands:
                entry = command_help(name)
                self.assertIsNotNone(entry, name)
                self.assertTrue(required.issubset(entry), name)
                self.assertTrue(entry["minimal_usage"], name)
                self.assertTrue(entry["example"], name)
        for commands in AUTOLOADED_COMPAT_COMMANDS.values():
            for name in commands:
                entry = command_help(name)
                self.assertIsNotNone(entry, name)
                self.assertTrue(required.issubset(entry), name)

    def test_tier1_help_uses_copyable_current_defaults_and_recovery(self):
        expected = {
            "pp_show": (
                "pp_show REPRESENTATION [, TARGET]",
                "pp_show surface, walk_root_a",
            ),
            "pp_nav": (
                "pp_nav [ACTION|TARGET]",
                'pp_nav groups, "walk_root_*"',
            ),
            "pp_align": (
                "pp_align MOBILE, REFERENCE",
                "pp_align mobile, reference, motif=GGH",
            ),
            "pp_orient": ("pp_orient TARGET", "pp_orient oligomer, axis=cyclic"),
            "pp_contacts": ("pp_contacts SOURCE", "pp_contacts complex, focus=organic"),
            "pp_metrics": (
                "pp_metrics SOURCE, target=CHAINS, binder=CHAINS",
                "pp_metrics complex, target=A, binder=B",
            ),
            "pp_jobs": (
                "pp_jobs [JOB_ID] | pp_jobs cancel, JOB_ID",
                "pp_jobs",
            ),
            "pp_undo": ("pp_undo [OPERATION_ID|list]", "pp_undo"),
        }
        self.assertEqual(set(expected), set(CANONICAL_COMMANDS["tasks"]))
        for name, (usage, example) in expected.items():
            with self.subTest(name=name):
                entry = command_help(name)
                self.assertEqual(entry["minimal_usage"], usage)
                self.assertEqual(entry["example"], example)
                self.assertTrue(example.startswith(name))

        self.assertEqual(command_help("pp_show")["undo"], "pp_undo")
        self.assertEqual(command_help("pp_align")["undo"], "pp_undo")
        self.assertEqual(command_help("pp_orient")["undo"], "pp_undo")
        self.assertIn("async result completes", command_help("pp_contacts")["undo"])
        self.assertIsNone(command_help("pp_metrics")["undo"])
        self.assertIn("all states", command_help("pp_show")["purpose"])
        self.assertIn("start-only", command_help("pp_nav")["purpose"])
        self.assertIn("explicit target", command_help("pp_orient")["purpose"])
        self.assertIn("asynchronously", command_help("pp_metrics")["purpose"])
        self.assertIn("backend failures", command_help("pp_jobs")["purpose"])
        self.assertIn("exact stored canonical reference", command_help("pp_undo")["purpose"])
        self.assertEqual(exact_help("surface")["undo"], "pp_undo [OPERATION_ID]")

        foreach = command_help("pp_foreach")
        self.assertEqual(foreach["example"], 'pp_foreach my_group, "show surface,{target}"')
        self.assertIn("commit=0", foreach["minimal_usage"])
        self.assertNotIn("commit=1", foreach["minimal_usage"])

    def test_help_supports_tasks_commands_categories_and_presets(self):
        result = _run_host_probe(
            r'''
queries = ["", "navigation", "grouping_compat", "group_objects_by_name", "pp_nav_start", "navg", "surface", "publication", "aligment", "结构对其"]
payload = {query: plugin.pp_help(query) for query in queries}
payload["console_surface"] = cmd.keyword["pp_help"][0]('"surface"')
payload["completion_registered"] = "pp_help" in cmd.auto_arg[0]
print("RESULT=" + json.dumps(payload, sort_keys=True))
'''
        )
        self.assertLessEqual(len(result[""]["tasks"]), 12)
        self.assertTrue(result["completion_registered"])
        self.assertEqual(result[""]["schema"], "pymolplugins.command-help/1.0")
        self.assertEqual(
            result[""]["categories"],
            {name: list(commands) for name, commands in CANONICAL_COMMANDS.items()},
        )
        self.assertEqual(
            result["navigation"]["categories"]["navigation"],
            list(CANONICAL_COMMANDS["navigation"]),
        )
        self.assertEqual(
            result["grouping_compat"]["compat_categories"]["grouping_compat"],
            list(AUTOLOADED_COMPAT_COMMANDS["grouping_compat"]),
        )
        self.assertEqual(
            result["group_objects_by_name"]["matches"][0]["category"],
            "grouping_compat",
        )
        self.assertEqual(result["pp_nav_start"]["matches"][0]["name"], "pp_nav_start")
        self.assertIn("pp_nav", result["navg"]["matches"][0]["example"])
        self.assertIn("surface", result["surface"]["matches"][0]["example"])
        self.assertEqual(
            result["publication"]["matches"][0]["name"], "publication_light"
        )
        self.assertEqual(
            result["console_surface"]["matches"][0]["name"], "surface"
        )
        self.assertEqual(result["aligment"]["matches"][0]["name"], "align")
        self.assertEqual(result["aligment"]["matches"][0]["matched_by"], "fuzzy")
        self.assertEqual(result["结构对其"]["matches"][0]["name"], "align")
        self.assertEqual(result["结构对其"]["matches"][0]["matched_by"], "fuzzy")
        for query in ("pp_nav_start", "navg", "surface", "publication"):
            match = result[query]["matches"][0]
            for field in (
                "purpose",
                "minimal_usage",
                "example",
                "mutates",
                "undo",
                "next",
            ):
                self.assertIn(field, match)

    def test_printed_help_includes_mutation_undo_and_next_steps(self):
        result = _run_host_probe(
            r'''
import contextlib
import io
stream = io.StringIO()
with contextlib.redirect_stdout(stream):
    plugin.pp_help("pp_show")
text = stream.getvalue()
print("RESULT=" + json.dumps({"text": text}, sort_keys=True))
'''
        )
        text = result["text"]
        self.assertIn("Changes scene: yes", text)
        self.assertIn("Undo: pp_undo", text)
        self.assertIn("Next:", text)

    def test_task_search_ranks_complete_intent_and_preserves_unicode(self):
        contacts = search_help("ligand contacts")[0]
        navigation = search_help("group batch")[0]
        self.assertEqual(contacts["name"], "interactions")
        self.assertEqual(contacts["matched_by"], "exact_phrase")
        self.assertEqual(contacts["unmatched_terms"], [])
        self.assertEqual(navigation["name"], "navigate")
        self.assertEqual(navigation["unmatched_terms"], [])
        for query, task in (
            ("导航", "navigate"),
            ("表面", "surface"),
            ("对齐", "align"),
            ("膜平面", "membrane"),
            ("配体接触", "interactions"),
        ):
            with self.subTest(query=query):
                self.assertEqual(search_help(query)[0]["name"], task)
        for query, task in (("aligment", "align"), ("结构对其", "align")):
            with self.subTest(fuzzy=query):
                match = search_help(query)[0]
                self.assertEqual(match["name"], task)
                self.assertEqual(match["matched_by"], "fuzzy")
        for query, task in (
            ("browse groups", "navigate"),
            ("sequence selection", "sequence motif"),
            ("structure align", "align"),
            ("axis align", "orient"),
            ("ligand contact", "interactions"),
            ("jobs", "pp_jobs"),
        ):
            with self.subTest(natural_language=query):
                match = search_help(query)[0]
                self.assertEqual(match["name"], task)
                self.assertNotIn("_rank_score", match)
        for query in (
            "ppi",
            "protein protein interactions",
            "hbond",
            "pi stacking",
            "small molecule contacts",
            "蛋白蛋白相互作用",
            "界面接触",
            "氢键",
            "盐桥",
            "配体环境",
        ):
            with self.subTest(interaction_intent=query):
                match = search_help(query)[0]
                self.assertEqual(match["name"], "interactions")
                self.assertEqual(match["matched_by"], "exact_phrase")
                self.assertEqual(match["unmatched_terms"], [])
        for query in ("出图", "渲染图片", "发表图片"):
            with self.subTest(style_intent=query):
                match = search_help(query)[0]
                self.assertEqual(match["name"], "style")
                self.assertEqual(match["matched_by"], "exact_phrase")
                self.assertEqual(match["unmatched_terms"], [])
        for query in (
            "rosetta metrics",
            "contact molecular surface",
            "buried unsatisfied residues",
            "接触分子表面",
            "埋藏未满足残基",
        ):
            with self.subTest(metric_intent=query):
                match = search_help(query)[0]
                self.assertEqual(match["name"], "worker metrics")
                self.assertEqual(match["matched_by"], "exact_phrase")
                self.assertEqual(match["unmatched_terms"], [])
        for query, command in (
            ("模糊匹配", "pp_target_resolve"),
            ("对象匹配", "pp_target_resolve"),
            ("iterate objects", "pp_foreach"),
            ("对象级迭代", "pp_foreach"),
        ):
            with self.subTest(target_intent=query):
                match = search_help(query)[0]
                self.assertEqual(match["name"], command)
                self.assertEqual(match["matched_by"], "exact_phrase")
                self.assertEqual(match["unmatched_terms"], [])
        self.assertEqual(exact_help("ＳＵＲＦＡＣＥ")["name"], "surface")
        self.assertEqual(search_help("🧪"), [])
        self.assertEqual(search_help("foobar"), [])

    def test_non_text_unicode_query_is_not_silently_treated_as_default_help(self):
        result = _run_host_probe(
            r'''
value = plugin.pp_help("🧪")
print("RESULT=" + json.dumps(value, sort_keys=True))
'''
        )
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["tasks"], [])


class HostCommandAuditTests(unittest.TestCase):
    def test_doctor_reports_and_load_repairs_an_unadapted_canonical_callback(self):
        result = _run_host_probe(
            r'''
from pymolplugins.navigation.commands import pp_nav_next

cmd.extend("pp_nav_next", pp_nav_next)
before = plugin.pp_doctor(check_worker=0)
loaded = plugin.pp_load()
after = plugin.pp_doctor(check_worker=0)
print("RESULT=" + json.dumps({
    "reported": before["command_audit"]["canonical_status"]["pp_nav_next"],
    "reason": before["overridden_commands"]["pp_nav_next"]["reason"],
    "repaired": "pp_nav_next" in loaded["repaired_commands"],
    "after": after["command_audit"]["canonical_status"]["pp_nav_next"],
}, sort_keys=True))
'''
        )
        self.assertEqual(result["reported"], "unadapted")
        self.assertIn("console adapter", result["reason"])
        self.assertTrue(result["repaired"])
        self.assertEqual(result["after"], "ok")

    def test_first_load_preserves_a_foreign_declared_slot_and_reports_it(self):
        result = _run_host_probe(
            r'''
doctor = plugin.pp_doctor(check_worker=0)
print("RESULT=" + json.dumps({
    "preserved": cmd.keyword["pp_nav_next"][0] is preexisting,
    "overridden": "pp_nav_next" in doctor["overridden_commands"],
    "foreign": "pp_nav_next" in doctor["foreign_commands"],
}, sort_keys=True))
''',
            before_import=r'''
def preexisting():
    return "foreign"

cmd.extend("pp_nav_next", preexisting)
''',
        )
        self.assertTrue(result["preserved"])
        self.assertTrue(result["overridden"])
        self.assertTrue(result["foreign"])

    def test_doctor_distinguishes_undeclared_foreign_and_overridden(self):
        result = _run_host_probe(
            r'''
def impostor():
    return "wrong"

def rogue():
    return "rogue"

rogue.__module__ = "pymolplugins.rogue"
cmd.extend("pp_nav_next", impostor)
cmd.extend("pp_external", impostor)
cmd.extend("pp_rogue", rogue)
doctor = plugin.pp_doctor(check_worker=0)
print("RESULT=" + json.dumps({
    "overridden": doctor["overridden_commands"],
    "foreign": doctor["foreign_commands"],
    "undeclared": doctor["undeclared_commands"],
}, sort_keys=True))
'''
        )
        self.assertIn("pp_nav_next", result["overridden"])
        self.assertIn("pp_external", result["foreign"])
        self.assertIn("pp_rogue", result["undeclared"])

    def test_load_repairs_missing_but_only_repairs_override_when_requested(self):
        result = _run_host_probe(
            r'''
original = cmd.keyword["pp_nav_next"][0]
navigation_module = sys.modules["pymolplugins.navigation.commands"]
del cmd.keyword["pp_nav_prev"]
del cmd.keyword["group_objects_by_name"]

def impostor():
    return "wrong"

cmd.extend("pp_nav_next", impostor)
checked = plugin.pp_load()
after_check = cmd.keyword["pp_nav_next"][0]
repaired = plugin.pp_load(repair=1)
after_repair = cmd.keyword["pp_nav_next"][0]
from pymolplugins.core.parameters import is_console_command
print("RESULT=" + json.dumps({
    "check_status": checked["status"],
    "missing_repaired": "pp_nav_prev" in checked["repaired_commands"],
    "compat_missing_repaired": "group_objects_by_name" in checked["repaired_commands"],
    "override_preserved": after_check is impostor,
    "check_overridden": "pp_nav_next" in checked["overridden_commands"],
    "repair_status": repaired["status"],
    "override_repaired": after_repair is not impostor,
    "identity_restored": getattr(after_repair, "__wrapped__", None) is getattr(original, "__wrapped__", None),
    "stateful_module_preserved": sys.modules["pymolplugins.navigation.commands"] is navigation_module,
    "compat_repair_is_console_adapted": is_console_command(cmd.keyword["group_objects_by_name"][0]),
}, sort_keys=True))
'''
        )
        self.assertEqual(result["check_status"], "partial")
        self.assertTrue(result["missing_repaired"])
        self.assertTrue(result["compat_missing_repaired"])
        self.assertTrue(result["override_preserved"])
        self.assertTrue(result["check_overridden"])
        self.assertEqual(result["repair_status"], "ok")
        self.assertTrue(result["override_repaired"])
        self.assertTrue(result["identity_restored"])
        self.assertTrue(result["stateful_module_preserved"])
        self.assertTrue(result["compat_repair_is_console_adapted"])


if __name__ == "__main__":
    unittest.main()
