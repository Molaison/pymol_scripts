import io
import inspect
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import pymolplugins.interactions.controller as interactions
from pymolplugins.interactions.controller import InteractionController
from pymolplugins.interactions.presets import PRESETS
from tests.interactions.test_pymol_interactions import FakeAtom, FakeCmd


class UsabilityFakeCmd(FakeCmd):
    """Fake with the object/rename APIs needed by the public workflow."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.types = {name: "object:molecule" for name in self.names}
        self.extensions = {}
        self.rename_calls = []
        self.fail_rename_targets = set()
        self.fail_next_interface_show = False

    def _selected_atoms(self, selection):
        text = str(selection)
        if "none" in text:
            return []
        selected = super()._selected_atoms(selection)
        molecular_names = {
            name for name, kind in self.types.items() if kind == "object:molecule"
        }
        mentioned = {
            name
            for name in molecular_names
            if name == text.strip("() ") or "(%s)" % name in text
        }
        if mentioned:
            selected = [atom for atom in selected if atom.model in mentioned]
        return selected

    def get_names(self, mode="all", enabled_only=0):
        if mode == "objects":
            return sorted(name for name in self.names if name in self.types)
        return super().get_names(mode)

    def get_type(self, name):
        return self.types.get(name, "")

    def get_object_list(self, selection="all"):
        return sorted({atom.model for atom in self._selected_atoms(selection)})

    def select(self, name, selection):
        super().select(name, selection)
        self.types[name] = "selection"

    def create(self, name, selection, source_state, target_state):
        super().create(name, selection, source_state, target_state)
        self.types[name] = "object:molecule"

    def distance(self, name, selection1, selection2, **kwargs):
        value = super().distance(name, selection1, selection2, **kwargs)
        self.types[name] = "object:measurement"
        return value

    def pseudoatom(self, object_name, **kwargs):
        super().pseudoatom(object_name, **kwargs)
        self.types[object_name] = "object:molecule"

    def group(self, name, members="", **kwargs):
        super().group(name, members)
        self.types[name] = "object:group"

    def set_name(self, old, new):
        if new in self.fail_rename_targets:
            self.fail_rename_targets.remove(new)
            raise RuntimeError("injected rename failure for %s" % new)
        if old not in self.names:
            raise RuntimeError("cannot rename missing name %s" % old)
        if new in self.names:
            raise RuntimeError("cannot rename over existing name %s" % new)
        self.rename_calls.append((old, new))
        self.names.remove(old)
        self.names.add(new)
        kind = self.types.pop(old, "")
        if kind:
            self.types[new] = kind

    def delete(self, name):
        super().delete(name)
        self.types.pop(name, None)

    def show(self, representation, selection):
        super().show(representation, selection)
        if self.fail_next_interface_show and selection.endswith("__interface"):
            self.fail_next_interface_show = False
            raise RuntimeError("injected staged render failure")

    def extend(self, name, function):
        self.extensions[name] = function


class ContactFakeCmd(UsabilityFakeCmd):
    def __init__(self, multi_object=False):
        atoms = [
            FakeAtom(1, "CA", "C", "ALA", "A", "1", (0.0, 0.0, 0.0)),
            FakeAtom(2, "CB", "C", "ALA", "A", "1", (0.0, 1.0, 0.0)),
            FakeAtom(3, "C1", "C", "LIG", "X", "101", (3.0, 0.0, 0.0)),
            FakeAtom(4, "O1", "O", "LIG", "X", "101", (3.8, 0.0, 0.0)),
        ]
        if multi_object:
            atoms.append(
                FakeAtom(
                    1,
                    "CA",
                    "C",
                    "GLY",
                    "B",
                    "1",
                    (20.0, 0.0, 0.0),
                    model="second_complex",
                )
            )
        super().__init__(atoms=atoms, interaction_pairs=False)

    def find_pairs(self, selection1, selection2, **kwargs):
        self.find_pair_calls.append((selection1, selection2, dict(kwargs)))
        if "not hydro" in selection1 and "not hydro" in selection2:
            # Two atom pairs describe the same ligand/receptor residue contact;
            # the preset should keep only the closest representative.
            return [(("complex", 3), ("complex", 1)), (("complex", 4), ("complex", 1))]
        return []


class FailOneDeleteContactCmd(ContactFakeCmd):
    def __init__(self):
        super().__init__()
        self.fail_delete_name = ""
        self.delete_failed = False

    def delete(self, name):
        if name == self.fail_delete_name and not self.delete_failed:
            self.delete_failed = True
            raise RuntimeError("injected clear delete failure for %s" % name)
        return super().delete(name)


class InteractionUsabilityTests(unittest.TestCase):
    def test_public_run_signature_covers_every_declared_preset_parameter(self):
        public_parameters = set(
            inspect.signature(interactions.pp_interact_run).parameters
        )
        declared = {
            parameter
            for definition in PRESETS.definitions()
            for parameter in definition.parameters
        }
        self.assertTrue(declared.issubset(public_parameters))

    def test_one_command_infers_the_only_source_and_renders_ligand_contacts(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        stream = io.StringIO()
        with patch.object(interactions, "get_controller", return_value=controller):
            with redirect_stdout(stream):
                group = interactions.pp_interact_run()

        record = controller.get(group)
        self.assertEqual(record.result.run.source.object, "complex")
        self.assertEqual(record.result.run.parameters["preset"], "ligand_contacts")
        self.assertEqual(
            record.result.summary["counts_by_type"], {"close_contact": 1}
        )
        self.assertIn("Using source=complex", stream.getvalue())
        self.assertIn("status=ready", stream.getvalue())
        roles = {entry.role: entry.name for entry in record.manifest.created}
        self.assertIn("partner_ligand_copy", roles)
        self.assertIn("interface_copy", roles)
        self.assertIn("manifest_carrier", roles)
        self.assertIn(
            ("show", "sticks", roles["partner_ligand_copy"]), fake.calls
        )
        self.assertIn(("show", "sticks", roles["interface_copy"]), fake.calls)
        measurement = next(
            entry
            for entry in record.manifest.created
            if entry.role == "interaction_measurement"
        )
        self.assertIn(
            ("set", "dash_color", "yellow", measurement.name), fake.calls
        )
        self.assertIn(
            ("hide", "everything", roles["manifest_carrier"]), fake.calls
        )
        self.assertIn(("disable", roles["manifest_carrier"]), fake.calls)
        self.assertTrue(
            any(key[0] == roles["manifest_carrier"] for key in fake.properties)
        )

        with patch.object(interactions, "get_controller", return_value=controller):
            with redirect_stdout(io.StringIO()):
                repeated_group = interactions.pp_interact_run()
        self.assertEqual(repeated_group, group)
        self.assertEqual(controller.get(group).result.run.source.object, "complex")

    def test_omitted_source_refuses_an_ambiguous_multi_object_scene(self):
        fake = ContactFakeCmd(multi_object=True)
        controller = InteractionController(fake)
        with patch.object(interactions, "get_controller", return_value=controller):
            with self.assertRaisesRegex(ValueError, "source is ambiguous.*complex"):
                interactions.pp_interact_run()
        self.assertEqual(fake.group_calls, [])

    def test_public_command_forwards_every_local_cutoff(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        with patch.object(interactions, "get_controller", return_value=controller):
            with redirect_stdout(io.StringIO()):
                group = interactions.pp_interact_run(
                    "ligand_site",
                    "complex",
                    hbond_cutoff="2.8",
                    salt_cutoff="3.9",
                    site_cutoff="6.0",
                )
        parameters = controller.get(group).result.run.parameters
        self.assertEqual(parameters["hbond_cutoff"], 2.8)
        self.assertEqual(parameters["salt_cutoff"], 3.9)
        self.assertEqual(parameters["site_cutoff"], 6.0)

    def test_ligand_keyword_is_a_clear_alias_for_legacy_partner2(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        with patch.object(interactions, "get_controller", return_value=controller):
            with redirect_stdout(io.StringIO()):
                group = interactions.pp_interact_run(
                    "ligand_contacts", "complex", ligand="resn LIG"
                )
            with self.assertRaisesRegex(ValueError, "ligand or partner2"):
                interactions.pp_interact_run(
                    "ligand_contacts",
                    "complex",
                    partner2="resn LIG",
                    ligand="organic",
                )
        parameters = controller.get(group).result.run.parameters
        self.assertEqual(parameters["partner2"], "resn LIG")

    def test_unsupported_visual_options_are_rejected_instead_of_ignored(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        with patch.object(interactions, "get_controller", return_value=controller):
            with self.assertRaisesRegex(ValueError, "surface/labels"):
                interactions.pp_interact_run(
                    "ligand_contacts", "complex", surface=1
                )
        self.assertEqual(fake.group_calls, [])

    def test_explicit_interchain_partners_must_be_nonempty_and_disjoint(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        with self.assertRaisesRegex(ValueError, "partner1.*no atoms"):
            controller.run(
                "interchain_basic",
                "complex",
                partner1="none",
                partner2="chain X",
            )
        with self.assertRaisesRegex(ValueError, "overlap"):
            controller.run(
                "interchain_basic",
                "complex",
                partner1="chain A",
                partner2="chain A",
            )
        self.assertEqual(fake.group_calls, [])

    def test_automatic_interchain_requires_two_chain_candidates(self):
        atoms = [
            FakeAtom(1, "CA", "C", "ALA", "A", "1", (0, 0, 0)),
            FakeAtom(2, "CB", "C", "ALA", "A", "1", (1, 0, 0)),
        ]
        fake = UsabilityFakeCmd(atoms=atoms, interaction_pairs=False)
        with self.assertRaisesRegex(ValueError, "fewer than two chains"):
            InteractionController(fake).run("interchain_basic", "complex")
        self.assertEqual(fake.group_calls, [])

    def test_ligand_run_rejects_an_empty_receptor_before_render(self):
        atoms = [FakeAtom(1, "C1", "C", "LIG", "X", "1", (0, 0, 0))]
        fake = UsabilityFakeCmd(atoms=atoms, interaction_pairs=False)
        with self.assertRaisesRegex(ValueError, "receptor selection has no atoms"):
            InteractionController(fake).run("ligand_contacts", "complex")
        self.assertEqual(fake.group_calls, [])

    def test_contact_interface_cutoff_cannot_hide_reported_contacts(self):
        fake = ContactFakeCmd()
        with self.assertRaisesRegex(ValueError, "site_cutoff.*contact_cutoff"):
            InteractionController(fake).run(
                "ligand_contacts",
                "complex",
                parameters={"site_cutoff": 3.0, "contact_cutoff": 4.0},
            )
        self.assertEqual(fake.group_calls, [])

    def test_zero_result_console_is_explicit_and_prints_warnings(self):
        fake = UsabilityFakeCmd(interaction_pairs=False)
        controller = InteractionController(fake)
        stream = io.StringIO()
        with patch.object(interactions, "get_controller", return_value=controller):
            with redirect_stdout(stream):
                interactions.pp_interact_run("ligand_quick", "complex")
        output = stream.getvalue()
        self.assertIn("status=empty", output)
        self.assertIn("Warning: no local ligand interactions found", output)
        record = next(iter(controller._runs.values()))
        roles = {entry.role: entry.name for entry in record.manifest.created}
        self.assertIn("partner_ligand_copy", roles)
        self.assertIn("interface_copy", roles)
        self.assertIn(("show", "sticks", roles["partner_ligand_copy"]), fake.calls)
        self.assertIn(("show", "sticks", roles["interface_copy"]), fake.calls)

    def test_run_qualified_owner_refuses_to_clear_a_replaced_stable_group(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        first = controller.run(
            "ligand_contacts", "complex", group="ppi_stable_owner"
        )
        first_reference = controller.owner_reference("ppi_stable_owner")
        second = controller.run(
            "ligand_contacts", "complex", group="ppi_stable_owner"
        )
        second_reference = controller.owner_reference("ppi_stable_owner")

        self.assertNotEqual(first.manifest.run_id, second.manifest.run_id)
        self.assertNotEqual(first_reference, second_reference)
        names = set(fake.names)
        deletes = list(fake.delete_calls)
        with self.assertRaises(interactions.StaleInteractionOwnerError):
            controller.clear(first_reference)
        self.assertIs(controller.get("ppi_stable_owner"), second)
        self.assertEqual(fake.names, names)
        self.assertEqual(fake.delete_calls, deletes)

        manifest = controller.clear(second_reference)
        self.assertEqual(manifest, second.manifest)
        self.assertTrue(set(second.manifest.created_names).isdisjoint(fake.names))

    def test_malformed_owner_references_are_rejected_without_scene_mutation(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        record = controller.run(
            "ligand_contacts", "complex", group="ppi_owner_parse"
        )
        for reference in (
            "ppi-run:",
            "ppi-run:missing-group",
            "ppi-run::ppi_owner_parse",
            "ppi-run:bad:ppi_owner_parse:extra",
        ):
            with self.subTest(reference=reference):
                names = set(fake.names)
                deletes = list(fake.delete_calls)
                with self.assertRaises(ValueError):
                    controller.clear(reference)
                self.assertEqual(fake.names, names)
                self.assertEqual(fake.delete_calls, deletes)
                self.assertIs(controller.get("ppi_owner_parse"), record)

    def test_partial_clear_is_reported_and_same_exact_owner_can_retry(self):
        fake = FailOneDeleteContactCmd()
        controller = InteractionController(fake)
        record = controller.run(
            "ligand_contacts", "complex", group="ppi_retry_clear"
        )
        reference = record.owner_reference
        carrier = next(
            entry.name
            for entry in record.manifest.created
            if entry.role == "manifest_carrier"
        )
        fake.fail_delete_name = next(
            name
            for name in interactions._retryable_clear_order(record.manifest)
            if name not in {record.manifest.root_group, carrier}
        )

        with self.assertRaises(interactions.InteractionClearError) as caught:
            controller.clear(reference)

        self.assertFalse(caught.exception.scene_preserved)
        self.assertEqual(caught.exception.owner_reference, reference)
        self.assertIn("retry", str(caught.exception))
        self.assertIs(controller.get("ppi_retry_clear"), record)
        self.assertIn(fake.fail_delete_name, fake.names)
        self.assertIn(carrier, fake.names)
        self.assertIn(record.manifest.root_group, fake.names)
        self.assertTrue(
            set(record.manifest.created_names).difference(fake.names)
        )

        cleared = controller.clear(reference)
        self.assertEqual(cleared, record.manifest)
        with self.assertRaises(KeyError):
            controller.get("ppi_retry_clear")

    def test_partial_clear_can_recover_from_carrier_after_controller_loss(self):
        fake = FailOneDeleteContactCmd()
        controller = interactions.get_controller(fake)
        record = controller.run(
            "ligand_contacts", "complex", group="ppi_reload_retry_clear"
        )
        reference = record.owner_reference
        carrier = next(
            entry.name
            for entry in record.manifest.created
            if entry.role == "manifest_carrier"
        )
        fake.fail_delete_name = next(
            name
            for name in interactions._retryable_clear_order(record.manifest)
            if name not in {record.manifest.root_group, carrier}
        )
        with self.assertRaises(interactions.InteractionClearError):
            controller.clear(reference)

        self.assertIn(carrier, fake.names)
        delattr(fake, interactions._CMD_CONTROLLER_ATTR)
        delattr(fake, interactions._CMD_RUNS_ATTR)
        previous = interactions._CONTROLLER
        try:
            interactions._CONTROLLER = None
            recovered = interactions.get_controller(fake)
            restored = recovered.get("ppi_reload_retry_clear")
            self.assertIsNone(restored.result)
            self.assertEqual(restored.owner_reference, reference)
            recovered.clear(reference)
            self.assertTrue(
                set(record.manifest.created_names).isdisjoint(fake.names)
            )
        finally:
            interactions._CONTROLLER = previous

    def test_failed_replacement_cleanup_stays_owned_across_reload_and_clear(self):
        fake = FailOneDeleteContactCmd()
        controller = interactions.get_controller(fake)
        first = controller.run(
            "ligand_contacts", "complex", group="ppi_retired_cleanup"
        )
        first_carrier = interactions._manifest_carrier_name(first.manifest)
        fake.fail_delete_name = next(
            name
            for name in first.manifest.delete_order
            if name not in {first.manifest.root_group, first_carrier}
        )

        second = controller.run(
            "ligand_contacts", "complex", group="ppi_retired_cleanup"
        )

        self.assertEqual(len(second.retired_manifests), 1)
        self.assertIn(fake.fail_delete_name, fake.names)
        delattr(fake, interactions._CMD_CONTROLLER_ATTR)
        delattr(fake, interactions._CMD_RUNS_ATTR)
        previous = interactions._CONTROLLER
        try:
            interactions._CONTROLLER = None
            recovered = interactions.get_controller(fake)
            restored = recovered.get("ppi_retired_cleanup")
            self.assertEqual(restored.owner_reference, second.owner_reference)
            self.assertEqual(len(restored.retired_manifests), 1)
            recovered.clear(restored.owner_reference)
            self.assertNotIn(fake.fail_delete_name, fake.names)
            self.assertTrue(
                set(second.manifest.created_names).isdisjoint(fake.names)
            )
        finally:
            interactions._CONTROLLER = previous

    def test_externally_damaged_active_manifest_is_not_recovered_as_owned(self):
        fake = ContactFakeCmd()
        controller = interactions.get_controller(fake)
        record = controller.run(
            "ligand_contacts", "complex", group="ppi_damaged_active"
        )
        ordinary = next(
            name
            for name in interactions._retryable_clear_order(record.manifest)
            if name
            not in {
                record.manifest.root_group,
                interactions._manifest_carrier_name(record.manifest),
            }
        )
        fake.delete(ordinary)
        delattr(fake, interactions._CMD_CONTROLLER_ATTR)
        delattr(fake, interactions._CMD_RUNS_ATTR)
        previous = interactions._CONTROLLER
        try:
            interactions._CONTROLLER = None
            recovered = interactions.get_controller(fake)
            with self.assertRaises(KeyError):
                recovered.get("ppi_damaged_active")
        finally:
            interactions._CONTROLLER = previous

    def test_completion_event_is_exact_and_listener_failure_cannot_rollback_commit(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        events = []

        def capture(event):
            self.assertEqual(
                controller.owner_reference(event["group"]),
                event["owner_reference"],
            )
            events.append(event)

        good_token = interactions.register_interaction_completion_listener(
            capture
        )
        bad_token = interactions.register_interaction_completion_listener(
            lambda event: (_ for _ in ()).throw(RuntimeError("listener boom"))
        )
        try:
            with redirect_stdout(io.StringIO()):
                first = controller.run(
                    "ligand_contacts", "complex", group="ppi_event"
                )
                second = controller.run(
                    "ligand_contacts", "complex", group="ppi_event"
                )
        finally:
            interactions.unregister_interaction_completion_listener(good_token)
            interactions.unregister_interaction_completion_listener(bad_token)

        self.assertIs(controller.get("ppi_event"), second)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["owner_reference"], first.owner_reference)
        self.assertEqual(events[1]["owner_reference"], second.owner_reference)
        self.assertEqual(events[1]["replaced_run_id"], first.manifest.run_id)
        self.assertEqual(
            events[1]["replaced_owner_reference"], first.owner_reference
        )
        self.assertEqual(events[0]["replaced_owner_reference"], "")
        self.assertEqual(events[1]["counts"], {"close_contact": 1})
        self.assertEqual(events[1]["counts_by_type"], {"close_contact": 1})

    def test_completion_listeners_receive_detached_count_dictionaries(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        observed = []

        def mutate(payload):
            payload["counts"]["injected"] = 1
            payload["counts_by_type"].clear()

        first = interactions.register_interaction_completion_listener(mutate)
        second = interactions.register_interaction_completion_listener(
            observed.append
        )
        try:
            controller.run(
                "ligand_contacts", "complex", group="ppi_detached_event"
            )
        finally:
            interactions.unregister_interaction_completion_listener(first)
            interactions.unregister_interaction_completion_listener(second)

        self.assertEqual(observed[0]["counts"], {"close_contact": 1})
        self.assertEqual(
            observed[0]["counts_by_type"], {"close_contact": 1}
        )

    def test_failed_transaction_emits_no_completion_event(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        controller.run("ligand_contacts", "complex", group="ppi_no_event")
        events = []
        token = interactions.register_interaction_completion_listener(events.append)
        fake.fail_next_interface_show = True
        try:
            with self.assertRaisesRegex(RuntimeError, "injected staged render failure"):
                controller.run(
                    "ligand_contacts", "complex", group="ppi_no_event"
                )
        finally:
            interactions.unregister_interaction_completion_listener(token)
        self.assertEqual(events, [])

    def test_local_preview_runs_full_validation_without_owner_side_effects(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        names = set(fake.names)
        properties = dict(fake.properties)
        runs = dict(controller._runs)
        listeners = dict(interactions._COMPLETION_LISTENERS)

        preview = controller.preview(
            "ligand_contacts",
            "complex",
            partner2="resn LIG",
            group="ppi_preview",
        )

        self.assertEqual(preview["status"], "preview")
        self.assertFalse(preview["scene_mutated"])
        self.assertFalse(preview["backend_contacted"])
        self.assertEqual(
            preview["summary"]["counts_by_type"], {"close_contact": 1}
        )
        self.assertEqual(fake.names, names)
        self.assertEqual(fake.properties, properties)
        self.assertEqual(controller._runs, runs)
        self.assertEqual(interactions._COMPLETION_LISTENERS, listeners)
        self.assertEqual(fake.group_calls, [])

    def test_public_preview_and_run_plan_the_same_default_stable_group(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        with patch.object(interactions, "get_controller", return_value=controller):
            preview = interactions.preview_interaction(
                "ligand_contacts", "complex", partner2="resn LIG"
            )
            with redirect_stdout(io.StringIO()):
                group = interactions.pp_interact_run(
                    "ligand_contacts", "complex", ligand="resn LIG"
                )

        self.assertEqual(preview["group"], group)
        self.assertEqual(group, "ppi_complex_ligand_contacts")

    def test_preview_reuses_partner_and_preset_validation_without_mutation(self):
        cases = []

        ligand_only = UsabilityFakeCmd(
            atoms=[FakeAtom(1, "C1", "C", "LIG", "X", "1", (0, 0, 0))],
            interaction_pairs=False,
        )
        cases.append(
            (
                ligand_only,
                lambda controller: controller.preview(
                    "ligand_contacts", "complex"
                ),
                "receptor selection has no atoms",
            )
        )

        single_chain = UsabilityFakeCmd(
            atoms=[
                FakeAtom(1, "CA", "C", "ALA", "A", "1", (0, 0, 0)),
                FakeAtom(2, "CB", "C", "ALA", "A", "1", (1, 0, 0)),
            ],
            interaction_pairs=False,
        )
        cases.append(
            (
                single_chain,
                lambda controller: controller.preview(
                    "interchain_basic", "complex"
                ),
                "fewer than two chains",
            )
        )

        overlap = ContactFakeCmd()
        cases.append(
            (
                overlap,
                lambda controller: controller.preview(
                    "interchain_basic",
                    "complex",
                    partner1="chain A",
                    partner2="chain A",
                ),
                "overlap",
            )
        )

        multiple_ligands = UsabilityFakeCmd(interaction_pairs=False)
        cases.append(
            (
                multiple_ligands,
                lambda controller: controller.preview(
                    "plip_overview", "complex"
                ),
                "default organic matches 2",
            )
        )

        for fake, invoke, message in cases:
            with self.subTest(message=message):
                controller = InteractionController(fake)
                names = set(fake.names)
                properties = dict(fake.properties)
                with self.assertRaisesRegex(ValueError, message):
                    invoke(controller)
                self.assertEqual(fake.names, names)
                self.assertEqual(fake.properties, properties)
                self.assertEqual(controller._runs, {})
                self.assertEqual(fake.group_calls, [])

    def test_plip_preview_builds_request_without_http_or_scene_outputs(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        names = set(fake.names)

        def build_request(cmd_api, source, **kwargs):
            return {
                "run_id": "preview-remote",
                "source": {
                    "object": source,
                    "state": kwargs["state"],
                    "sha256": "d" * 64,
                },
                "partners": [dict(value) for value in kwargs["partners"]],
                "parameters": dict(kwargs["parameters"]),
            }

        with patch.object(
            interactions,
            "build_plip_request_from_pymol",
            side_effect=build_request,
        ) as build:
            preview = controller.preview(
                "plip_overview",
                "complex",
                partner2="resn LIG",
                group="ppi_plip_preview",
            )

        build.assert_called_once()
        self.assertEqual(preview["backend"], "plip-wsl")
        self.assertFalse(preview["backend_contacted"])
        self.assertIsNone(preview["summary"]["counts_by_type"])
        self.assertEqual(fake.names, names)
        self.assertEqual(controller._runs, {})
        self.assertEqual(fake.group_calls, [])

    def test_preset_list_explains_backend_example_and_limitation(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        stream = io.StringIO()
        with patch.object(interactions, "get_controller", return_value=controller):
            with redirect_stdout(stream):
                names = interactions.pp_interact_list()
        output = stream.getvalue()
        self.assertIn("ligand_contacts", names)
        self.assertIn("backend=pymol-local", output)
        self.assertIn("Example:", output)
        self.assertIn("Limit:", output)

    def test_backend_status_uses_typed_unknown_and_names_local_fallbacks(self):
        with redirect_stdout(io.StringIO()):
            status = interactions.pp_interact_backend_status()
        plip = status["backends"]["plip-wsl"]
        self.assertIsNone(plip["available"])
        self.assertEqual(plip["check"], "pp_worker_health")
        self.assertEqual(plip["local_fallbacks"], ["ligand_contacts", "ligand_site"])

    def test_registered_console_adapter_returns_concise_actionable_usage_errors(self):
        fake = ContactFakeCmd()
        interactions.register_commands(fake)
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = fake.extensions["pp_interact_run"]("ligand_contcts")
        self.assertEqual(result["status"], "error")
        self.assertIn("ligand_contacts", result["error"])
        self.assertIn("pp_interact_list", result["action"])
        self.assertNotIn("Traceback", stream.getvalue())

    def test_registered_console_adapter_unquotes_ligand_selection(self):
        fake = ContactFakeCmd()
        interactions.register_commands(fake)
        with redirect_stdout(io.StringIO()):
            group = fake.extensions["pp_interact_run"](
                "ligand_contacts", "complex", ligand='"resn LIG"'
            )
        record = interactions.get_controller(fake).get(group)
        self.assertEqual(record.result.run.parameters["partner2"], "resn LIG")

    def test_registered_clear_reports_stale_owner_without_traceback_or_delete(self):
        fake = ContactFakeCmd()
        interactions.register_commands(fake)
        controller = interactions.get_controller(fake)
        first = controller.run(
            "ligand_contacts", "complex", group="ppi_console_stale"
        )
        controller.run("ligand_contacts", "complex", group="ppi_console_stale")
        names = set(fake.names)
        deletes = list(fake.delete_calls)
        stream = io.StringIO()

        with redirect_stdout(stream):
            result = fake.extensions["pp_interact_clear"](first.owner_reference)

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["scene_preserved"])
        self.assertIn("replaced by a newer run", result["action"])
        self.assertNotIn("Traceback", stream.getvalue())
        self.assertEqual(fake.names, names)
        self.assertEqual(fake.delete_calls, deletes)

    def test_remote_replace_false_preflights_owned_group_before_request_build(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        original = controller.run(
            "ligand_contacts", "complex", group="ppi_remote_busy"
        )
        with patch.object(
            interactions,
            "build_plip_request_from_pymol",
            side_effect=AssertionError("request must not be built"),
        ) as build:
            with self.assertRaisesRegex(ValueError, "already exists"):
                controller.prepare_remote(
                    "plip_overview",
                    "complex",
                    partner2="resn LIG",
                    group="ppi_remote_busy",
                    replace=False,
                )
        build.assert_not_called()
        self.assertIs(controller.get("ppi_remote_busy"), original)

    def test_plip_refuses_ambiguous_default_organic_ligands_before_request(self):
        fake = UsabilityFakeCmd(interaction_pairs=False)
        controller = InteractionController(fake)
        with patch.object(
            interactions,
            "build_plip_request_from_pymol",
            side_effect=AssertionError("request must not be built"),
        ) as build:
            with self.assertRaisesRegex(ValueError, "default organic matches 2"):
                controller.prepare_remote("plip_overview", "complex")
        build.assert_not_called()

    def test_local_replace_false_preflights_before_contact_search(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        original = controller.run(
            "ligand_contacts", "complex", group="ppi_local_busy"
        )
        calls_before = len(fake.find_pair_calls)
        with self.assertRaisesRegex(ValueError, "already exists"):
            controller.run(
                "ligand_contacts",
                "complex",
                group="ppi_local_busy",
                replace=False,
            )
        self.assertEqual(len(fake.find_pair_calls), calls_before)
        self.assertIs(controller.get("ppi_local_busy"), original)

    def test_invalid_output_path_fails_before_contact_search_or_scene_change(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        with tempfile.TemporaryDirectory() as directory:
            missing = os.path.join(directory, "missing", "x.json")
            with self.assertRaisesRegex(ValueError, "output directory does not exist"):
                controller.run(
                    "ligand_contacts", "complex", output_json=missing
                )
        self.assertEqual(fake.find_pair_calls, [])
        self.assertEqual(fake.group_calls, [])

    def test_failed_replacement_render_leaves_prior_scene_exactly_intact(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        original = controller.run(
            "ligand_contacts", "complex", group="ppi_transaction"
        )
        old_names = set(original.manifest.created_names)
        fake.fail_next_interface_show = True

        with self.assertRaisesRegex(RuntimeError, "injected staged render failure"):
            controller.run(
                "ligand_contacts",
                "complex",
                group="ppi_transaction",
                replace=True,
            )

        self.assertIs(controller.get("ppi_transaction"), original)
        self.assertTrue(old_names.issubset(fake.names))
        self.assertFalse(any("__pp_interact_stage" in name for name in fake.names))

    def test_failed_export_commit_rolls_back_scene_and_preserves_old_file(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        original = controller.run(
            "ligand_contacts", "complex", group="ppi_export_transaction"
        )
        old_names = set(original.manifest.created_names)
        events = []
        token = interactions.register_interaction_completion_listener(events.append)
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "contacts.json")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("old-result\n")
            try:
                with patch.object(
                    interactions.os,
                    "replace",
                    side_effect=OSError("injected export failure"),
                ):
                    with self.assertRaisesRegex(OSError, "injected export failure"):
                        controller.run(
                            "ligand_contacts",
                            "complex",
                            group="ppi_export_transaction",
                            output_json=target,
                            replace=True,
                        )
            finally:
                interactions.unregister_interaction_completion_listener(token)
            with open(target, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "old-result\n")

        self.assertIs(controller.get("ppi_export_transaction"), original)
        self.assertTrue(old_names.issubset(fake.names))
        self.assertFalse(any("__pp_interact_" in name for name in fake.names))
        self.assertEqual(events, [])

    def test_scene_and_json_commit_together_on_success(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "contacts.json")
            record = controller.run(
                "ligand_contacts",
                "complex",
                group="ppi_scene_json",
                output_json=target,
            )
            with open(target, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        self.assertEqual(payload["schema"], "pymolplugins.interactions/1.0")
        self.assertEqual(payload["run"]["id"], record.result.run.id)
        self.assertIn("ppi_scene_json", fake.names)

    def test_failed_group_promotion_restores_parked_prior_group(self):
        fake = ContactFakeCmd()
        controller = InteractionController(fake)
        original = controller.run(
            "ligand_contacts", "complex", group="ppi_rename_transaction"
        )
        old_names = set(original.manifest.created_names)
        fake.fail_rename_targets.add("ppi_rename_transaction")

        with self.assertRaisesRegex(RuntimeError, "injected rename failure"):
            controller.run(
                "ligand_contacts",
                "complex",
                group="ppi_rename_transaction",
                replace=True,
            )

        self.assertIs(controller.get("ppi_rename_transaction"), original)
        self.assertTrue(old_names.issubset(fake.names))
        self.assertFalse(any("__pp_interact_" in name for name in fake.names))

    def test_cmd_attached_manifest_survives_module_controller_reset(self):
        fake = ContactFakeCmd()
        controller = interactions.get_controller(fake)
        record = controller.run("ligand_contacts", "complex", group="ppi_reload")
        previous = interactions._CONTROLLER
        try:
            interactions._CONTROLLER = None
            restored = interactions.get_controller(fake)
            self.assertIs(restored, controller)
            self.assertIs(restored.get("ppi_reload"), record)
        finally:
            interactions._CONTROLLER = previous

    def test_plain_run_registry_rehydrates_a_fresh_controller_after_hot_reload(self):
        fake = ContactFakeCmd()
        controller = interactions.get_controller(fake)
        record = controller.run(
            "ligand_contacts", "complex", group="ppi_hot_reload"
        )

        class StaleController:
            def __init__(self, cmd_api, runs):
                self.cmd = cmd_api
                self._runs = runs

        stale = StaleController(fake, controller._runs)
        setattr(fake, interactions._CMD_CONTROLLER_ATTR, stale)
        previous = interactions._CONTROLLER
        try:
            interactions._CONTROLLER = None
            restored = interactions.get_controller(fake)
            self.assertIsNot(restored, stale)
            self.assertIs(restored.get("ppi_hot_reload"), record)
            self.assertIs(restored._runs, controller._runs)
        finally:
            interactions._CONTROLLER = previous

    def test_scene_manifest_recovers_exact_clear_after_controller_state_is_lost(self):
        fake = ContactFakeCmd()
        controller = interactions.get_controller(fake)
        record = controller.run(
            "ligand_contacts", "complex", group="ppi_scene_restore"
        )
        created = set(record.manifest.created_names)
        delattr(fake, interactions._CMD_CONTROLLER_ATTR)
        delattr(fake, interactions._CMD_RUNS_ATTR)
        previous = interactions._CONTROLLER
        try:
            interactions._CONTROLLER = None
            recovered = interactions.get_controller(fake)
            restored_record = recovered.get("ppi_scene_restore")
            self.assertIsNone(restored_record.result)
            self.assertEqual(restored_record.manifest, record.manifest)
            replacement = recovered.run(
                "ligand_contacts",
                "complex",
                group="ppi_scene_restore",
                replace=True,
            )
            self.assertIsNotNone(replacement.result)
            old_children = created - {"ppi_scene_restore"}
            self.assertTrue(old_children.isdisjoint(fake.names))
            recovered.clear("ppi_scene_restore")
            self.assertNotIn("ppi_scene_restore", fake.names)
        finally:
            interactions._CONTROLLER = previous

    def test_manifest_loaded_after_plugin_start_is_recovered_on_next_command(self):
        scene = ContactFakeCmd()
        scene_controller = InteractionController(scene)
        record = scene_controller.run(
            "ligand_contacts", "complex", group="ppi_late_session"
        )
        output_names = set(record.manifest.created_names)

        host = ContactFakeCmd()
        controller = interactions.get_controller(host)
        self.assertEqual(controller._runs, {})
        host.names.update(output_names)
        host.types.update(
            {
                name: scene.types[name]
                for name in output_names
                if name in scene.types
            }
        )
        host.properties.update(scene.properties)

        refreshed = interactions.get_controller(host)
        recovered = refreshed.get("ppi_late_session")
        self.assertIsNone(recovered.result)
        self.assertEqual(recovered.manifest, record.manifest)


if __name__ == "__main__":
    unittest.main()
