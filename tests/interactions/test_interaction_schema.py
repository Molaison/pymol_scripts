import json
import unittest

from pymolplugins.interactions.presets import (
    PRESETS,
    PresetDefinition,
    PresetRegistry,
)
from pymolplugins.interactions.schema import (
    AtomRef,
    BackendInfo,
    Geometry,
    Interaction,
    InteractionResult,
    InteractionSite,
    ManifestEntry,
    OutputManifest,
    Participant,
    Partner,
    ResidueRef,
    RunInfo,
    SchemaError,
    SourceInfo,
)


def _minimal_result():
    donor = Participant(
        "donor",
        AtomRef(1, 101, "NZ", "N", object="complex"),
        ResidueRef("LYS", "", "", "42A", "A"),
    )
    acceptor = Participant(
        "acceptor",
        AtomRef(2, 202, "O1", "O", alt="B", object="complex"),
        ResidueRef("LIG", "X", "SEG", "7", ""),
    )
    interaction = Interaction(
        "hydrogen_bond_deadbeef",
        "hydrogen_bond",
        "donor_acceptor",
        (donor, acceptor),
        Geometry(2.9, (155.0,)),
    )
    return InteractionResult.create(
        RunInfo(
            "run-1",
            BackendInfo("pymol-local", "1.0"),
            SourceInfo("complex", 2, "0" * 64),
            {"preset": "ligand_quick"},
        ),
        (Partner("ligand", "complex and resn LIG", 1),),
        (InteractionSite("lig_x_7", (interaction,)),),
        ("fixture warning",),
    )


class InteractionSchemaTests(unittest.TestCase):
    def test_round_trip_preserves_resi_insertion_alt_and_summary(self):
        original = _minimal_result()
        restored = InteractionResult.from_json(original.to_json())
        self.assertEqual(restored, original)
        participant = restored.sites[0].interactions[0].participants[0]
        self.assertEqual(participant.residue.resi, "42A")
        self.assertEqual(participant.residue.insertion, "A")
        self.assertIsInstance(participant.residue.resi, str)
        self.assertEqual(
            restored.summary["counts_by_type"], {"hydrogen_bond": 1}
        )

    def test_summary_must_be_derived_from_interaction_rows(self):
        payload = _minimal_result().to_dict()
        payload["summary"]["counts_by_type"]["hydrogen_bond"] = 99
        with self.assertRaisesRegex(SchemaError, "does not match interactions"):
            InteractionResult.from_dict(payload)

    def test_public_payload_is_plain_json(self):
        payload = json.loads(json.dumps(_minimal_result().to_dict()))
        self.assertEqual(payload["schema"], "pymolplugins.interactions/1.0")
        self.assertEqual(payload["run"]["source"]["state"], 2)

    def test_manifest_is_exact_unique_and_never_contains_source(self):
        manifest = OutputManifest.create(
            "run-1",
            "ppi_fixture",
            "complex",
            "ligand_quick",
            (
                ManifestEntry("ppi_fixture__ligand", "object", "ligand"),
                ManifestEntry("ppi_fixture", "group", "root"),
            ),
            source_objects=("complex",),
            input_hash="0" * 64,
            parameters={"preset": "ligand_quick"},
            stale_fingerprint="0" * 64,
        )
        restored = OutputManifest.from_json(manifest.to_json())
        self.assertEqual(restored.created_names, ("ppi_fixture__ligand", "ppi_fixture"))
        self.assertEqual(restored.delete_order, ("ppi_fixture", "ppi_fixture__ligand"))
        self.assertEqual(restored.source_objects, ("complex",))
        self.assertEqual(restored.input_hash, "0" * 64)
        self.assertEqual(restored.parameters["preset"], "ligand_quick")
        self.assertEqual(restored.stale_fingerprint, "0" * 64)
        with self.assertRaisesRegex(SchemaError, "source object"):
            OutputManifest.create(
                "run-1",
                "ppi_fixture",
                "complex",
                "ligand_quick",
                (
                    ManifestEntry("complex", "object", "bad"),
                    ManifestEntry("ppi_fixture", "group", "root"),
                ),
            )


class PresetRegistryTests(unittest.TestCase):
    def test_initial_presets_are_discoverable(self):
        self.assertEqual(
            PRESETS.names(),
            (
                "interchain_basic",
                "ligand_contacts",
                "ligand_quick",
                "ligand_site",
                "plip_overview",
                "plip_publication",
                "polar_qc",
            ),
        )
        self.assertEqual(PRESETS.resolve("binding_site").name, "ligand_site")
        self.assertEqual(PRESETS.resolve("contacts").name, "ligand_contacts")
        self.assertTrue(PRESETS.resolve("ligand_contacts").limitations)
        self.assertFalse(
            PRESETS.resolve("ligand_quick").parameters.get("surface", {}).get(
                "default", False
            )
        )

    def test_aliases_and_canonical_names_share_one_namespace(self):
        first = PresetDefinition("first", "first preset", "local", aliases=("shared",))
        second = PresetDefinition("shared", "second preset", "local")
        registry = PresetRegistry((first,))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            registry.register(second)


if __name__ == "__main__":
    unittest.main()
