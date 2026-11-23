"""Annotation version identity follows logical sources, independently of cache paths."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from pymolplugins.annotation.cache import SourceIdentity
from pymolplugins.annotation.materializer import MaterializedStructure
from pymolplugins.annotation.provenance import (
    forget_loaded_source, record_loaded_source, source_version_token,
)


class AnnotationPortabilityTests(unittest.TestCase):
    def test_same_observation_survives_location_change_without_source_io(self):
        old = SourceIdentity("ssh", "ssh://host/old/model.pdb", 80, 1234)
        moved = replace(old, canonical_source="ssh://host/new/model.pdb")
        self.assertNotEqual(old.cache_key, moved.cache_key)
        with patch("os.stat", side_effect=AssertionError("stat")), patch(
            "builtins.open", side_effect=AssertionError("open")
        ):
            for logical in ("models/model.pdb", "directory:stable-id"):
                self.assertEqual(source_version_token(logical, old), source_version_token(logical, moved))
                loaded = {}
                record_loaded_source(loaded, "object", "entry", moved, logical_source=logical)
                self.assertEqual(loaded["object"].cache_key, moved.cache_key)

    def test_declared_source_and_each_observed_identity_field_matter(self):
        identity = SourceIdentity("ssh", "ssh://host/path/model.pdb", 80, 1234)
        original = source_version_token("model.pdb", identity)
        self.assertNotEqual(original, source_version_token("other.pdb", identity))
        for change in ({"provider": "file"}, {"size": 81}, {"mtime_ns": 1235},
                       {"sha256": "a" * 64}, {"version_token": "v2"}):
            with self.subTest(change=change):
                self.assertNotEqual(original, source_version_token("model.pdb", replace(identity, **change)))
        # Equal metadata without a supplied content digest cannot detect a byte
        # substitution preserving size/mtime. Do not pretend the token can.
        self.assertEqual(original, source_version_token("model.pdb", identity))

    def test_direct_eviction_uses_cache_identity_not_portable_version(self):
        old = SourceIdentity("direct", "C:/old/model.pdb", 80, 1234)
        moved = replace(old, canonical_source="C:/new/model.pdb")
        def materialized(identity, mode="direct"):
            return MaterializedStructure(identity.canonical_source, identity.canonical_source,
                                         identity, mode, None, False)
        for cached, expected in ((materialized(old), False), (materialized(moved), True),
                                 (materialized(old, "copy"), True)):
            objects = {}
            record_loaded_source(objects, "object", "entry", old, logical_source="entry")
            paths = {"entry": cached}
            forget_loaded_source(objects, "object", paths)
            self.assertEqual("entry" in paths, expected)
            self.assertEqual(objects, {})


if __name__ == "__main__":
    unittest.main()
