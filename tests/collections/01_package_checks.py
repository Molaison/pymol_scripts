"""Headless checks for canonical imports, preview state and publication ownership."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def child(code, *arguments):
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code, *map(str, arguments)],
        cwd=REPO, capture_output=True, text=True, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ).stdout.strip()


class CollectionsPackage(unittest.TestCase):
    def test_commands_import_without_host_and_legacy_core_has_one_owner(self):
        self.assertEqual(child("""
import importlib.abc
import sys
class NoHost(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname.split('.')[0] in {'pymol', 'pymol2', 'numpy', 'PyQt5', 'PySide2'}
                or fullname in {'pymolplugins.collections.plugin', 'pymolplugins.collections.core'}):
            raise AssertionError('Eager dependency: ' + fullname)
sys.meta_path.insert(0, NoHost())
from pymolplugins.collections.commands import pp_collections
assert pp_collections.__defaults__ == ('',)
print('lazy')
"""), "lazy")
        from pymolplugins.collections import core
        from workflows.dataset_navigation import collections_core

        self.assertIs(collections_core, core)

    def test_native_preview_restores_scene_and_keeps_first_state(self):
        import numpy as np
        import pymol2
        from pymol import cmd as global_cmd
        from pymolplugins.collections import core

        commands_before = set(global_cmd.keyword)
        from pymolplugins.collections import plugin

        self.assertEqual(set(global_cmd.keyword), commands_before)
        with tempfile.TemporaryDirectory() as temporary, pymol2.PyMOL() as instance:
            cmd = instance.cmd
            cmd.set("max_threads", 1)
            cmd.viewport(160, 120)
            cmd.load(str(REPO / "data/helical_bundle.pdb"), "user_structure")
            first = cmd.get_coords("user_structure", 1).copy()
            cmd.create("user_structure", "user_structure", 1, 2)
            cmd.load_coords(first + [30, 0, 0], "user_structure", 2)
            source = Path(temporary) / "ensemble.pdb"
            cmd.save(str(source), "user_structure", state=0)
            cmd.select("user_selection", "user_structure and name CA")
            cmd.select("hidden_selection", "user_structure and name N")
            cmd.disable("hidden_selection")
            cmd.enable("user_selection")
            cmd.frame(2)
            cmd.set("all_states", 1)
            enabled = set(cmd.get_names("all", enabled_only=1))
            view = cmd.get_view()
            row = {"id": "ensemble", "path": str(source), "matrix": np.eye(4).tolist(),
                   "coordinate_signature": core.coordinate_signature(first)}
            manifest = {"samples": [row], "config": {},
                        "bounds": [first.min(0).tolist(), first.max(0).tolist()]}
            panel = SimpleNamespace(manifest=manifest, previous_scene=None,
                                    require_manifest=lambda: None,
                                    status=SimpleNamespace(setText=lambda text: None))
            scene = plugin.CollectionPreview(panel)
            with patch.object(plugin, "cmd", cmd):
                scene.show_sample(0)
                self.assertEqual(cmd.get_object_state(scene.object_name), 1)
                self.assertFalse(cmd.get_setting_boolean("all_states", scene.object_name))
                self.assertEqual(cmd.get_state(), 2)
                self.assertTrue(cmd.get_setting_boolean("all_states"))
                np.testing.assert_array_equal(cmd.get_coords(scene.object_name, 1), first)
                preview = scene.object_name
                preview_view = cmd.get_view()
                row["path"] = str(Path(temporary) / "absent.pdb")
                with self.assertRaises(Exception):
                    scene.show_sample(0)
                self.assertEqual(scene.object_name, preview)
                self.assertEqual(cmd.get_names("objects", enabled_only=1), [preview])
                np.testing.assert_allclose(cmd.get_view(), preview_view)
                scene.restore()
            self.assertEqual(set(cmd.get_names("all", enabled_only=1)), enabled)
            self.assertNotIn(preview, cmd.get_names("all"))
            np.testing.assert_allclose(cmd.get_view(), view)
            np.testing.assert_array_equal(cmd.get_coords("user_structure", 1), first)

    def test_directory_locks_exclude_other_processes_and_release_for_retry(self):
        from pymolplugins.collections import core

        code = """
from pathlib import Path
import sys
from pymolplugins.collections import core
try:
    with getattr(core, sys.argv[1])(Path(sys.argv[2])):
        print('owned')
except (core.ExportInProgress, core.CollectionEditInProgress):
    print('busy')
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            other = directory / "other"
            other.mkdir()
            for name in ("export_directory_lock", "_collection_edit_lock", "_collection_request_lock"):
                with self.subTest(lock=name):
                    with getattr(core, name)(directory):
                        self.assertEqual(child(code, name, directory), "busy")
                        self.assertEqual(child(code, name, other), "owned")
                    self.assertEqual(child(code, name, directory), "owned")

    def test_render_receipt_binds_source_bytes_and_png(self):
        from pymolplugins.collections import core

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.pdb"
            source.write_bytes((REPO / "data/helical_bundle.pdb").read_bytes())
            scores = directory / "scores.json"
            core.write_json(scores, [{"path": str(source), "score": 1}])
            output = directory / "collection"
            manifest = core.create_collection(
                {"scores": str(scores), "metrics": [{"name": "score"}], "count": 1,
                 "binder": "polymer.protein", "orientation": "pca"}, output)
            path = output / "01_collection.json"
            with patch.object(core.os, "cpu_count", return_value=1):
                report = core.export_collection(path, workers=1, width=96, height=96)
                self.assertTrue(report["results"][0]["ok"], report)
                image = Path(report["results"][0]["image"])
                image_bytes = image.read_bytes()
                self.assertTrue(image_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
                retried = core.export_collection(path, workers=1, width=96, height=96, retry_failed=True)
                self.assertEqual(retried["reused"], 1)
                self.assertEqual(retried["processes"], [])
            images = output / "images"
            image.write_bytes(image_bytes[:24])
            self.assertEqual(core.current_images(manifest, images, verify_contents=True), {})
            image.write_bytes(image_bytes)
            before = source.stat()
            original = source.read_bytes()
            source.write_bytes(original.replace(b"GLY", b"ALA", 1))
            self.assertTrue(source.read_bytes() != original)
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(source.stat().st_size, before.st_size)
            self.assertEqual(core.current_images(manifest, images, verify_contents=True), {})
            with self.assertRaisesRegex(ValueError, "展示页"):
                core.gallery_page(path, core.TaskControl())


if __name__ == "__main__":
    unittest.main(verbosity=2)
