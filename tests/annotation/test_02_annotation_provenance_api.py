"""Real loading plus source-version labeling, without any GUI or source re-read."""

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

try:
    import pymol2
except ImportError:
    pymol2 = None

from pymolplugins.annotation.annotation_store import RowAnnotationStore
from pymolplugins.annotation.materializer import DirectMaterializer
from pymolplugins.annotation.provenance import activate_loaded_source, record_loaded_source


def pdb(path, x, remark):
    path.write_text(f"REMARK {remark}\n"
                    f"ATOM      1  CA  ALA A   1    {x:8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00           C\nEND\n",
                    encoding="ascii")


@unittest.skipIf(pymol2 is None, "Requires PyMOL Python")
class AnnotationProvenanceApiTests(unittest.TestCase):
    def test_replacement_is_unreviewed_and_old_object_keeps_its_observed_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "model.pdb"
            pdb(source, 0., "old")
            materializer = DirectMaterializer()
            old = materializer.materialize(source)
            session = pymol2.PyMOL()
            session.start()
            self.addCleanup(session.stop)
            api = session.cmd
            api.load(old.path, "old_view", zoom=0)
            loaded = {}
            record_loaded_source(loaded, "old_view", "stable-entry", old.identity,
                                 logical_source="stable-entry")
            replacement = root / "replacement.pdb"
            pdb(replacement, 99., "new longer remark")
            os.replace(replacement, source)
            new = materializer.materialize(source)
            self.assertNotEqual(old.identity.cache_key, new.identity.cache_key)
            store = RowAnnotationStore(str(root / "labels.jsonl"), versioned=True)
            source_bytes = source.read_bytes()

            native_stat = os.stat

            def no_source_stat(path, *args, **kwargs):
                if os.path.normcase(os.path.abspath(os.fspath(path))) == os.path.normcase(str(source)):
                    raise AssertionError("annotation inspected the structure")
                return native_stat(path, *args, **kwargs)

            with patch("os.stat", side_effect=no_source_stat), patch.object(materializer, "materialize", side_effect=AssertionError("source re-materialized")):
                activate_loaded_source(store, loaded, "old_view", "stable-entry")
                store.set("stable-entry", "quality", "Keep")
            self.assertEqual(api.get_coords("old_view", 1)[0][0], 0.)
            self.assertEqual(store.history()[0]["source_version"], loaded["old_view"].token)
            self.assertEqual(source.read_bytes(), source_bytes)

            api.load(new.path, "new_view", zoom=0)
            record_loaded_source(loaded, "new_view", "stable-entry", new.identity,
                                 logical_source="stable-entry")
            activate_loaded_source(store, loaded, "new_view", "stable-entry")
            self.assertEqual(api.get_coords("new_view", 1)[0][0], 99.)
            self.assertEqual(store.annotations_for("stable-entry"), {})
            self.assertEqual(store.snapshot_for_label("quality"), {})
            store.set("stable-entry", "quality", "Reject")
            activate_loaded_source(store, loaded, "old_view", "stable-entry")
            self.assertEqual(store.get("stable-entry", "quality"), "Keep")
            store.compact()
            replay = RowAnnotationStore(store.path, versioned=True)
            activate_loaded_source(replay, loaded, "new_view", "stable-entry")
            self.assertEqual(replay.get("stable-entry", "quality"), "Reject")
            activate_loaded_source(replay, loaded, "old_view", "stable-entry")
            self.assertEqual(replay.get("stable-entry", "quality"), "Keep")

            moved = root / "moved" / "model.pdb"
            moved.parent.mkdir()
            shutil.copy2(source, moved)
            moved_source = materializer.materialize(moved)
            api.load(moved_source.path, "moved_view", zoom=0)
            record_loaded_source(loaded, "moved_view", "stable-entry", moved_source.identity,
                                 logical_source="stable-entry")
            self.assertNotEqual(loaded["moved_view"].cache_key, loaded["new_view"].cache_key)
            self.assertEqual(loaded["moved_view"].token, loaded["new_view"].token)
            activate_loaded_source(replay, loaded, "moved_view", "stable-entry")
            self.assertEqual(api.get_coords("moved_view", 1)[0][0], 99.)
            self.assertEqual(replay.get("stable-entry", "quality"), "Reject")


if __name__ == "__main__":
    unittest.main()
