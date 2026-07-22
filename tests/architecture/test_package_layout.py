import ast
import importlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pymolplugins.host.manifest import (
    CANONICAL_COMMANDS,
    CANONICAL_MODULES,
    command_count,
)


ROOT = Path(__file__).resolve().parents[2]


class PackageLayoutTests(unittest.TestCase):
    def test_manifest_has_unique_new_package_modules_and_63_commands(self):
        self.assertEqual(command_count(), 63)
        self.assertTrue(
            all(name.startswith("pymolplugins.") for name in CANONICAL_MODULES)
        )
        commands = [
            command
            for values in CANONICAL_COMMANDS.values()
            for command in values
        ]
        self.assertEqual(len(commands), len(set(commands)))
        self.assertIn("pp_target_resolve", commands)
        self.assertIn("pp_foreach", commands)

    def test_canonical_package_never_imports_historical_namespaces(self):
        forbidden = {
            "analysis",
            "plugins",
            "services",
            "utilities",
            "visualization",
            "workflows",
        }
        violations = []
        for path in sorted((ROOT / "pymolplugins").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in forbidden:
                            violations.append((path.relative_to(ROOT), alias.name))
                    continue
                if module.split(".", 1)[0] in forbidden:
                    violations.append((path.relative_to(ROOT), module))
        self.assertEqual(violations, [])

    def test_representative_old_imports_are_identity_preserving_shims(self):
        old_math = importlib.import_module("utilities.math3d")
        new_math = importlib.import_module("pymolplugins.core.math3d")
        self.assertIs(old_math.identity4, new_math.identity4)

        old_navigation = importlib.import_module(
            "workflows.dataset_navigation.navigation"
        )
        new_navigation = importlib.import_module(
            "pymolplugins.navigation.commands"
        )
        self.assertIs(old_navigation.pp_nav_start, new_navigation.pp_nav_start)

        old_schema = importlib.import_module(
            "analysis.interactions.interaction_schema"
        )
        new_schema = importlib.import_module("pymolplugins.interactions.schema")
        self.assertIs(old_schema.InteractionResult, new_schema.InteractionResult)

        old_worker = importlib.import_module("services.worker")
        new_worker = importlib.import_module("pymolplugins.worker.app")
        self.assertIs(old_worker.WorkerApp, new_worker.WorkerApp)

    def test_interaction_and_remote_shims_bootstrap_in_pymol_run_namespace(self):
        cases = (
            (
                "analysis/interactions/interaction_presets.py",
                "pymolplugins.interactions.presets",
                "PRESETS",
            ),
            (
                "analysis/interactions/interaction_schema.py",
                "pymolplugins.interactions.schema",
                "InteractionResult",
            ),
            (
                "analysis/interactions/plip_adapter.py",
                "pymolplugins.interactions.plip_protocol",
                "validate_plip_request",
            ),
            (
                "analysis/interactions/plip_interactions.py",
                "pymolplugins.interactions.compat.plip_dialog",
                "plip_interface",
            ),
            (
                "analysis/interactions/plip_runner.py",
                "pymolplugins.interactions.plip_runner",
                "build_raw_payload",
            ),
            (
                "analysis/interactions/pymol_interactions.py",
                "pymolplugins.interactions.controller",
                "pp_interact_run",
            ),
            (
                "analysis/remote_metrics.py",
                "pymolplugins.remote.commands",
                "pp_worker_health",
            ),
        )
        source_template = r'''
import os
import sys
import types

root = os.path.realpath({root!r})
shim = os.path.realpath({shim!r})
expected_module = {expected_module!r}
expected_symbol = {expected_symbol!r}

# Isolated mode plus this explicit filter models a Host process which has not
# imported the toolkit and was started outside the repository.
sys.path[:] = [
    entry for entry in sys.path
    if os.path.realpath(entry or os.getcwd()) != root
]
assert not any(
    name == "pymolplugins" or name.startswith("pymolplugins.")
    for name in sys.modules
)

class FakeCmd:
    def __init__(self):
        self.extended = {{}}

    def extend(self, name, function):
        self.extended[name] = function

pymol_module = types.ModuleType("pymol")
pymol_module.__path__ = []
pymol_module.__package__ = "pymol"
pymol_module.__file__ = os.path.join(os.getcwd(), "not-the-repository", "pymol.py")
pymol_module.__script__ = shim
pymol_module.cmd = FakeCmd()

qt_module = types.ModuleType("pymol.Qt")
qt_module.QtWidgets = types.SimpleNamespace(QDialog=object)
pymol_module.Qt = qt_module
sys.modules["pymol"] = pymol_module
sys.modules["pymol.Qt"] = qt_module

with open(shim, "r", encoding="utf-8") as handle:
    code = compile(handle.read(), shim, "exec")
exec(code, pymol_module.__dict__)

assert expected_module in sys.modules, expected_module
assert expected_symbol in pymol_module.__dict__, expected_symbol
module_file = os.path.realpath(sys.modules[expected_module].__file__)
assert os.path.commonpath((root, module_file)) == root, module_file
'''
        with tempfile.TemporaryDirectory() as outside_cwd:
            for relative_path, expected_module, expected_symbol in cases:
                shim = ROOT / relative_path
                source = source_template.format(
                    root=str(ROOT),
                    shim=str(shim),
                    expected_module=expected_module,
                    expected_symbol=expected_symbol,
                )
                with self.subTest(shim=relative_path):
                    completed = subprocess.run(
                        [sys.executable, "-I", "-c", source],
                        cwd=outside_cwd,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        "stdout:\n%s\nstderr:\n%s"
                        % (completed.stdout, completed.stderr),
                    )


if __name__ == "__main__":
    unittest.main()
