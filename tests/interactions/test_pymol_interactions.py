import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

import pymolplugins.interactions.controller as pymol_interactions
from pymolplugins.interactions.schema import (
    AtomRef,
    BackendInfo,
    Geometry,
    Interaction,
    InteractionResult,
    InteractionSite,
    Participant,
    Partner,
    ResidueRef,
    RunInfo,
    SourceInfo,
)
from pymolplugins.interactions.controller import (
    InteractionController,
    RemoteInteractionSubmission,
    get_controller,
)
from pymolplugins.remote.client import WorkerRequestError


class FakeAtom:
    def __init__(
        self,
        index,
        name,
        symbol,
        resn,
        chain,
        resi,
        coord,
        model="complex",
        segi="",
        resv=None,
        serial=None,
    ):
        self.index = index
        self.name = name
        self.symbol = symbol
        self.resn = resn
        self.chain = chain
        self.resi = resi
        self.coord = coord
        self.model = model
        self.segi = segi
        self.resv = resv
        self.id = serial
        self.alt = ""


class FakeModel:
    def __init__(self, atoms):
        self.atom = list(atoms)


class FakeCmd:
    def __init__(self, atoms=None, interaction_pairs=True, salt_pairs=False):
        self.atoms = list(
            atoms
            or (
                FakeAtom(1, "NZ", "N", "LYS", "", "42", (0.0, 0.0, 0.0), resv=42),
                FakeAtom(2, "O1", "O", "LIG", "X", "10A", (2.9, 0.0, 0.0), resv=10),
                FakeAtom(3, "C1", "C", "LIG", "X", "11", (4.0, 0.0, 0.0), resv=11),
                FakeAtom(4, "O", "O", "HOH", "W", "1", (8.0, 0.0, 0.0), resv=1),
                FakeAtom(5, "ZN", "ZN", "ZN", "M", "2", (9.0, 0.0, 0.0), resv=2),
            )
        )
        self.interaction_pairs = interaction_pairs
        self.salt_pairs = salt_pairs
        self.names = {atom.model for atom in self.atoms}
        self.calls = []
        self.delete_calls = []
        self.find_pair_calls = []
        self.get_model_calls = []
        self.group_calls = []

    def _selected_atoms(self, selection):
        text = str(selection)
        index_values = {int(value) for value in re.findall(r"\bindex\s+(\d+)", text)}
        if index_values:
            return [atom for atom in self.atoms if atom.index in index_values]
        if "within" in text:
            return [atom for atom in self.atoms if atom.resn == "LYS"]
        if "not (" in text and ("resn LIG" in text or "organic" in text):
            return [atom for atom in self.atoms if atom.resn not in {"LIG", "HOH", "ZN"}]
        if "resn LIG" in text or "organic" in text:
            return [atom for atom in self.atoms if atom.resn == "LIG"]
        chain_match = re.search(r'chain\s+"([^"]*)"', text)
        if chain_match:
            return [atom for atom in self.atoms if atom.chain == chain_match.group(1)]
        return list(self.atoms)

    def get_model(self, selection, state=1):
        self.get_model_calls.append((selection, state))
        return FakeModel(self._selected_atoms(selection))

    def count_atoms(self, selection, state=1):
        return len(self._selected_atoms(selection))

    def get_chains(self, selection, state=1):
        self.calls.append(("get_chains", selection, state))
        return sorted({atom.chain for atom in self._selected_atoms(selection)})

    def find_pairs(self, selection1, selection2, **kwargs):
        self.find_pair_calls.append((selection1, selection2, dict(kwargs)))
        if not self.interaction_pairs:
            return []
        # One receptor-donor -> ligand-acceptor H bond.  Salt queries and the
        # reverse H-bond direction intentionally return zero rows.
        if (
            "donor" in selection1
            and "not (" in selection1
            and "acceptor" in selection2
            and "resn LIG" in selection2
        ):
            return [(('complex', 1), ('complex', 2))]
        if (
            self.salt_pairs
            and "formal_charge > 0" in selection1
            and "not (" in selection1
            and "formal_charge < 0" in selection2
            and "resn LIG" in selection2
        ):
            return [(('complex', 1), ('complex', 2))]
        if (
            "donor" in selection1
            and 'chain ""' in selection1
            and "acceptor" in selection2
            and 'chain "X"' in selection2
        ):
            return [(('complex', 1), ('complex', 2))]
        return []

    def get_names(self, mode="all"):
        return sorted(self.names)

    def select(self, name, selection):
        self.names.add(name)
        self.calls.append(("select", name, selection))

    def create(self, name, selection, source_state, target_state):
        self.names.add(name)
        self.calls.append(("create", name, selection, source_state, target_state))

    def distance(self, name, selection1, selection2, **kwargs):
        self.names.add(name)
        self.calls.append(("distance", name, selection1, selection2, dict(kwargs)))
        return 1.0

    def group(self, name, members=""):
        self.names.add(name)
        self.group_calls.append((name, members))

    def show(self, representation, selection):
        self.calls.append(("show", representation, selection))

    def color(self, color, selection):
        self.calls.append(("color", color, selection))

    def set(self, setting, value, selection=""):
        self.calls.append(("set", setting, value, selection))

    def hide(self, representation, selection=""):
        self.calls.append(("hide", representation, selection))

    def label(self, selection, expression):
        self.calls.append(("label", selection, expression))

    def disable(self, name):
        self.calls.append(("disable", name))

    def delete(self, name):
        self.delete_calls.append(name)
        self.names.discard(name)

    def extend(self, name, function):
        self.calls.append(("extend", name, function))


class FailOnInterfaceShowCmd(FakeCmd):
    def __init__(self, *args, failures_remaining=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.failures_remaining = failures_remaining

    def show(self, representation, selection):
        super().show(representation, selection)
        if selection.endswith("__interface") and self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("synthetic renderer failure")


class PolarFakeCmd(FakeCmd):
    def __init__(self):
        atoms = (
            FakeAtom(1, "N1", "N", "ASN", "A", "1", (0.0, 0.0, 0.0)),
            FakeAtom(2, "O1", "O", "ASP", "B", "2", (3.0, 0.0, 0.0)),
            FakeAtom(3, "N2", "N", "GLN", "A", "3", (0.0, 5.0, 0.0)),
            FakeAtom(4, "O2", "O", "GLU", "B", "4", (3.8, 5.0, 0.0)),
            FakeAtom(5, "N3", "N", "LYS", "A", "5", (0.0, 10.0, 0.0)),
            FakeAtom(6, "N4", "N", "ARG", "B", "6", (3.0, 10.0, 0.0)),
            FakeAtom(7, "O3", "O", "ASP", "A", "7", (0.0, 15.0, 0.0)),
            FakeAtom(8, "O4", "O", "GLU", "B", "8", (3.0, 15.0, 0.0)),
        )
        super().__init__(atoms=atoms, interaction_pairs=False)

    def find_pairs(self, selection1, selection2, **kwargs):
        self.find_pair_calls.append((selection1, selection2, dict(kwargs)))
        if "donor and not acceptor" in selection1:
            return [(('complex', 5), ('complex', 6))]
        if "acceptor and not donor" in selection1:
            return [(('complex', 7), ('complex', 8))]
        if kwargs.get("mode") == 1 and "donor" in selection1:
            pairs = [(('complex', 1), ('complex', 2))]
            if float(kwargs["cutoff"]) > 3.6:
                pairs.append((('complex', 3), ('complex', 4)))
            return pairs
        return []


class PymolInteractionAdapterTests(unittest.TestCase):
    @staticmethod
    def _participant(role, index, resn="LIG", resi="10"):
        return Participant(
            role,
            AtomRef(index, index, "A%d" % index, "C", object="complex"),
            ResidueRef(resn, "X", "", resi),
        )

    def test_controller_is_attached_to_cmd_for_script_reload_compatibility(self):
        fake = FakeCmd(interaction_pairs=False)
        first = get_controller(fake)
        second = get_controller(fake)
        self.assertIs(first, second)
        self.assertIs(
            getattr(fake, "_pymolplugins_interaction_controller_v1"), first
        )

    def test_ligand_quick_keeps_exact_ligand_boundary_and_manifest(self):
        fake = FakeCmd()
        controller = InteractionController(fake)
        record = controller.run(
            "ligand_quick",
            "complex",
            partner2="resn LIG",
            state=2,
            group="ppi_fixture",
        )

        self.assertEqual(record.result.summary["counts_by_type"], {"hydrogen_bond": 1})
        ligand = next(value for value in record.result.partners if value.role == "ligand")
        self.assertEqual(ligand.atom_count, 2)
        self.assertIn("resn LIG", ligand.selection)
        self.assertNotEqual(ligand.selection.strip(), "complex")
        self.assertEqual(len(record.result.sites), 2)
        interaction = record.result.interactions[0]
        ligand_participant = interaction.participants[1]
        self.assertEqual(ligand_participant.residue.resi, "10A")
        self.assertEqual(ligand_participant.residue.insertion, "A")
        self.assertTrue(
            all(call[2].get("state1") == 2 for call in fake.find_pair_calls)
        )

        distance_calls = [call for call in fake.calls if call[0] == "distance"]
        self.assertEqual(len(distance_calls), 1)
        self.assertIn("index 2", distance_calls[0][3])
        self.assertEqual(distance_calls[0][4]["state"], 2)
        self.assertFalse(any(call[:3] == ("hide", "everything", "") for call in fake.calls))
        self.assertFalse(
            any(call[0] == "color" and call[-1] == "all" for call in fake.calls)
        )
        grouped_members = {member for _, member in fake.group_calls if member}
        self.assertNotIn("complex", grouped_members)
        self.assertNotIn("complex", record.manifest.created_names)
        self.assertEqual(
            len(record.manifest.created_names), len(set(record.manifest.created_names))
        )
        self.assertEqual(record.manifest.input_hash, record.result.run.source.sha256)
        self.assertEqual(
            record.manifest.stale_fingerprint,
            record.result.run.source.sha256,
        )
        self.assertEqual(record.manifest.parameters, record.result.run.parameters)
        self.assertIn("complex", record.manifest.source_objects)

        manifest_names = record.manifest.created_names
        controller.clear("ppi_fixture")
        self.assertEqual(fake.delete_calls, list(reversed(manifest_names)))
        self.assertTrue(all("*" not in name for name in fake.delete_calls))

    def test_default_ligand_is_organic_and_excludes_water_and_metal(self):
        fake = FakeCmd(interaction_pairs=False)
        record = InteractionController(fake).run(
            "ligand_quick", "complex", group="ppi_default_ligand"
        )
        ligand = next(value for value in record.result.partners if value.role == "ligand")
        self.assertIn("organic", ligand.selection)
        self.assertEqual(ligand.atom_count, 2)
        self.assertEqual(record.result.summary["counts_by_type"], {})
        self.assertIn("no local ligand interactions found", record.result.warnings)

    def test_interchain_handles_blank_chain_and_renders_local_hbond(self):
        fake = FakeCmd(interaction_pairs=True)
        record = InteractionController(fake).run(
            "interchain_basic", "complex", state=1, group="ppi_blank_chain"
        )
        selections = [partner.selection for partner in record.result.partners]
        self.assertTrue(any('chain ""' in selection for selection in selections))
        self.assertGreaterEqual(len(record.result.sites), 1)
        self.assertEqual(record.result.summary["counts_by_type"], {"hydrogen_bond": 1})

    def test_polar_qc_classifies_without_legacy_global_setting_mutation(self):
        fake = PolarFakeCmd()
        record = InteractionController(fake).run(
            "polar_qc", "complex", partner1="chain A", partner2="chain B"
        )
        self.assertEqual(
            record.result.summary["counts_by_type"],
            {"hydrogen_bond": 2, "polar_clash": 2},
        )
        self.assertEqual(
            {value.subtype for value in record.result.interactions},
            {"good", "weak", "donor_donor", "acceptor_acceptor"},
        )
        measurement_names = {
            entry.name
            for entry in record.manifest.created
            if entry.kind == "measurement"
        }
        self.assertTrue(any("polar_good" in name for name in measurement_names))
        self.assertTrue(any("polar_weak" in name for name in measurement_names))
        self.assertTrue(any("donor_clash" in name for name in measurement_names))
        self.assertTrue(any("acceptor_clash" in name for name in measurement_names))
        self.assertFalse(
            any(call[0] == "set" and str(call[1]).startswith("h_bond_") for call in fake.calls)
        )

    def test_polar_qc_rejects_weak_cutoff_below_good_cutoff_before_render(self):
        fake = PolarFakeCmd()
        with self.assertRaisesRegex(ValueError, "weak_cutoff"):
            InteractionController(fake).run(
                "polar_qc",
                "complex",
                partner1="chain A",
                partner2="chain B",
                parameters={"good_cutoff": 4.0, "weak_cutoff": 3.5},
            )
        self.assertEqual(fake.group_calls, [])

    def test_remote_plip_result_is_identity_checked_filtered_and_rendered(self):
        atoms = list(FakeCmd().atoms)
        atoms.append(FakeAtom(6, "C2", "C", "ALA", "A", "6", (1, 1, 0)))
        fake = FakeCmd(atoms=atoms, interaction_pairs=False)
        controller = InteractionController(fake)
        source = SourceInfo("complex", 1, "a" * 64)
        kept = Interaction(
            "plip_pi_kept",
            "pi_stacking",
            "parallel",
            (
                self._participant("protein_ring", 1, "PHE", "1"),
                self._participant("protein_ring", 6, "PHE", "1"),
                self._participant("ligand_ring", 2),
                self._participant("ligand_ring", 3),
            ),
            Geometry(4.2, (12.0,)),
        )
        removed = Interaction(
            "plip_other_ligand",
            "hydrogen_bond",
            "protein_donor",
            (
                self._participant("donor", 1, "LYS", "1"),
                self._participant("acceptor", 3, "DRG", "11"),
            ),
            Geometry(3.1, (145.0,)),
        )
        parameters = {
            "preset": "plip_overview",
            "interface_selection": "complex and index 1",
            "site_cutoff": 5.0,
        }
        result = InteractionResult.create(
            RunInfo("remote-run", BackendInfo("plip", "3.0.0"), source, parameters),
            (
                Partner("receptor", "complex and polymer", 2),
                Partner("ligand", "complex and index 2", 1),
            ),
            (InteractionSite("site_selected", (kept, removed)),),
        )
        submission = RemoteInteractionSubmission(
            request={"run_id": "remote-run", "source": source.to_dict()},
            preset="plip_overview",
            group="ppi_remote",
            output_json="",
            replace=True,
            ligand_identities=(("complex", 2),),
        )
        record = controller.commit_remote_result(submission, result.to_dict())
        self.assertEqual(record.result.summary["counts_by_type"], {"pi_stacking": 1})
        self.assertIn("removed 1 interaction", " ".join(record.result.warnings))
        distance = next(call for call in fake.calls if call[0] == "distance")
        self.assertIn("index 1", distance[2])
        self.assertIn("index 2", distance[3])
        self.assertNotIn("index 6", distance[3])
        self.assertNotIn("index 3", distance[3])
        manifest_names = record.manifest.created_names
        controller.clear("ppi_remote")
        self.assertEqual(fake.delete_calls, list(reversed(manifest_names)))

    def test_prepare_remote_builds_exact_partner_metadata_before_http(self):
        fake = FakeCmd(interaction_pairs=False)
        controller = InteractionController(fake)

        def build_request(cmd_api, source, **kwargs):
            self.assertIs(cmd_api, fake)
            self.assertEqual(source, "complex")
            self.assertEqual(kwargs["state"], 2)
            self.assertEqual(kwargs["preset"], "plip_publication")
            partners = kwargs["partners"]
            self.assertEqual([item["role"] for item in partners], ["receptor", "ligand"])
            self.assertIn("resn LIG", partners[1]["selection"])
            self.assertIn("interface_selection", kwargs["parameters"])
            return {
                "run_id": "prepared-run",
                "source": {"object": source, "state": 2, "sha256": "b" * 64},
            }

        with patch(
            "pymolplugins.interactions.controller.build_plip_request_from_pymol",
            side_effect=build_request,
        ):
            submission = controller.prepare_remote(
                "plip_publication",
                "complex",
                partner1="polymer.protein",
                partner2="resn LIG",
                state=2,
                group="ppi_prepared",
                parameters={"surface": True},
            )
        self.assertEqual(submission.request["run_id"], "prepared-run")
        self.assertEqual(submission.group, "ppi_prepared")
        self.assertEqual(
            submission.ligand_identities, (("complex", 2), ("complex", 3))
        )

    def test_invalid_remote_identity_creates_no_outputs(self):
        fake = FakeCmd(interaction_pairs=False)
        controller = InteractionController(fake)
        source = SourceInfo("complex", 1, "a" * 64)
        result = InteractionResult.create(
            RunInfo(
                "wrong-run",
                BackendInfo("plip", "3.0.0"),
                source,
                {"preset": "plip_overview"},
            ),
            (),
            (),
        )
        submission = RemoteInteractionSubmission(
            request={"run_id": "expected-run", "source": source.to_dict()},
            preset="plip_overview",
            group="ppi_never_created",
            output_json="",
            replace=True,
            ligand_identities=(("complex", 2),),
        )
        with self.assertRaisesRegex(ValueError, "run id"):
            controller.commit_remote_result(submission, result.to_dict())
        self.assertEqual(fake.group_calls, [])
        self.assertEqual(fake.delete_calls, [])

    def test_remote_transport_failure_creates_no_partial_output(self):
        fake = FakeCmd(interaction_pairs=False)
        controller = InteractionController(fake)
        names_before = set(fake.names)
        request = {
            "run_id": "transport-run",
            "source": {"object": "complex", "state": 1, "sha256": "c" * 64},
        }
        failure = WorkerRequestError(
            "synthetic timeout",
            code="timeout",
            endpoint="/v1/interactions/plip",
            retriable=True,
        )
        with patch.object(
            pymol_interactions, "get_controller", return_value=controller
        ), patch.object(
            pymol_interactions,
            "build_plip_request_from_pymol",
            return_value=request,
        ), patch("pymolplugins.remote.client.worker_request", side_effect=failure):
            with self.assertRaises(WorkerRequestError):
                pymol_interactions.pp_interact_run(
                    "plip_overview",
                    "complex",
                    partner2="resn LIG",
                    async_mode=0,
                )
        self.assertEqual(set(fake.names), names_before)
        self.assertEqual(fake.group_calls, [])
        self.assertEqual(fake.delete_calls, [])

    def test_unknown_clear_never_calls_delete(self):
        fake = FakeCmd()
        controller = InteractionController(fake)
        with self.assertRaisesRegex(KeyError, "nothing was deleted"):
            controller.clear("ppi_missing_*")
        self.assertEqual(fake.delete_calls, [])

    def test_renderer_failure_cleans_only_recorded_partial_outputs(self):
        fake = FailOnInterfaceShowCmd(interaction_pairs=False)
        controller = InteractionController(fake)
        with self.assertRaisesRegex(RuntimeError, "synthetic renderer failure"):
            controller.run(
                "ligand_quick",
                "complex",
                partner2="resn LIG",
                group="ppi_partial",
            )
        self.assertGreater(len(fake.delete_calls), 0)
        self.assertNotIn("complex", fake.delete_calls)
        self.assertTrue(all("*" not in name for name in fake.delete_calls))
        with self.assertRaises(KeyError):
            controller.get("ppi_partial")

    def test_failed_replacement_restores_the_prior_exact_result(self):
        fake = FailOnInterfaceShowCmd(
            interaction_pairs=False,
            failures_remaining=0,
        )
        controller = InteractionController(fake)
        original = controller.run(
            "ligand_quick",
            "complex",
            partner2="resn LIG",
            group="ppi_replace",
        )
        original_payload = original.result.to_dict()
        original_names = original.manifest.created_names
        fake.failures_remaining = 1

        with self.assertRaisesRegex(RuntimeError, "synthetic renderer failure"):
            controller.run(
                "ligand_quick",
                "complex",
                partner2="resn LIG",
                group="ppi_replace",
                replace=True,
            )

        restored = controller.get("ppi_replace")
        self.assertEqual(restored.result.to_dict(), original_payload)
        self.assertEqual(restored.manifest.created_names, original_names)
        self.assertTrue(set(original_names).issubset(fake.names))

    def test_export_is_valid_versioned_interaction_result(self):
        fake = FakeCmd(interaction_pairs=True, salt_pairs=True)
        controller = InteractionController(fake)
        record = controller.run(
            "ligand_site",
            "complex",
            partner2="resn LIG",
            group="ppi_export",
            parameters={"surface": True, "labels": True},
        )
        with tempfile.TemporaryDirectory() as directory:
            filename = os.path.join(directory, "result.json")
            self.assertEqual(controller.export("ppi_export", filename), filename)
            with open(filename, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        restored = InteractionResult.from_dict(payload)
        self.assertEqual(restored, record.result)
        self.assertEqual(payload["schema"], "pymolplugins.interactions/1.0")
        self.assertEqual(
            restored.summary["counts_by_type"],
            {"hydrogen_bond": 1, "salt_bridge": 1},
        )
        interface_names = {
            entry.name
            for entry in record.manifest.created
            if entry.role == "interface_copy"
        }
        self.assertTrue(
            any(
                call[0] == "show"
                and call[1] == "surface"
                and call[2] in interface_names
                for call in fake.calls
            )
        )
        self.assertTrue(
            any(
                call[0] == "label"
                and any(name in call[1] for name in interface_names)
                for call in fake.calls
            )
        )

    def test_long_source_name_produces_bounded_unique_output_names(self):
        long_model = "complex_" + "x" * 220
        atoms = [
            FakeAtom(1, "N", "N", "ALA", "A", "1", (0, 0, 0), model=long_model),
            FakeAtom(2, "O1", "O", "LIG", "X", "2", (3, 0, 0), model=long_model),
        ]
        fake = FakeCmd(atoms=atoms, interaction_pairs=False)
        record = InteractionController(fake).run("ligand_quick", long_model)
        self.assertLessEqual(len(record.manifest.root_group), 100)
        self.assertTrue(all(len(name) <= 180 for name in record.manifest.created_names))
        self.assertEqual(
            len(record.manifest.created_names), len(set(record.manifest.created_names))
        )


if __name__ == "__main__":
    unittest.main()
