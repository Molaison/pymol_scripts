import unittest

from tests.navigation.test_navigation_adapter import FakeCmd
from utilities.pymol_groups import expand_group_leaves, groups_containing, root_groups


class PymolGroupMetadataTests(unittest.TestCase):
    def test_nested_group_includes_non_molecule_leaf_types(self):
        fake = FakeCmd()
        self.assertEqual(
            set(expand_group_leaves(fake, "root_a")),
            {"a_model", "a_cgo", "a_map", "a_measurement"},
        )

    def test_roots_and_containing_groups_use_direct_parent_metadata(self):
        fake = FakeCmd()
        self.assertEqual(root_groups(fake), ["root_a", "root_b"])
        containing = groups_containing(fake, "a_map")
        self.assertEqual([name for name, _ in containing], ["nested", "root_a"])


if __name__ == "__main__":
    unittest.main()
