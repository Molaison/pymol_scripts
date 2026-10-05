import io
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

from pymolplugins.tasks import commands, journal


class RepAtom:
    def __init__(self, model, index, reps=0, coord=(0.0, 0.0, 0.0)):
        self.model = model
        self.index = index
        self.reps = reps
        self.coord = coord
        self.segi = ""
        self.chain = "A"
        self.resi = str(index)
        self.inscode = ""
        self.resn = "ALA"
        self.name = "CA"
        self.alt = ""


class RepresentationCmd:
    def __init__(self):
        surface = commands._REPRESENTATION_MASKS["surface"]
        self.atoms = {
            "first": [RepAtom("first", 1, surface), RepAtom("first", 2, 0)],
            "second": [RepAtom("second", 1, 0), RepAtom("second", 2, 0)],
            "design_alpha": [RepAtom("design_alpha", 1, 0)],
            "design_alfa": [RepAtom("design_alfa", 1, 0)],
        }
        self.types = {name: "object:molecule" for name in self.atoms}
        self.types["root"] = "object:group"
        self.types["axis"] = "object:cgo"
        self.parents = {"root": "", "first": "root", "second": "root"}
        self.selections = {}
        self.properties = {}
        self.fail_show_target = ""

    def get_names(self, mode="all"):
        return list(self.types)

    def get_type(self, name):
        return self.types.get(name, "")

    def get_session(self, *args, **kwargs):
        return {
            "names": [
                [name, None, None, None, None, None, self.parents.get(name, "")]
                for name in self.types
            ]
        }

    def get_model(self, target, state=1):
        return types.SimpleNamespace(atom=list(self.atoms.get(target, ())))

    def count_states(self, target):
        return 1

    def iterate(self, target, expression, space):
        for atom in self.atoms[target]:
            exec(expression, dict(space, index=atom.index, reps=atom.reps))

    def get_property(self, name, object, state=0, quiet=1):
        return self.properties.get((object, name))

    def set_property(
        self, name, value, object="", state=0, proptype=6, quiet=1
    ):
        self.properties[(object, name)] = value

    def select_list(self, name, target, indices, **kwargs):
        existing = {atom.index for atom in self.atoms[target]}
        selected = [index for index in indices if index in existing]
        self.selections[name] = (target, selected)
        return len(selected)

    def delete(self, name):
        self.selections.pop(name, None)

    def _selected_atoms(self, target):
        if target in self.selections:
            object_name, indices = self.selections[target]
            return [atom for atom in self.atoms[object_name] if atom.index in indices]
        return self.atoms[target]

    def show(self, representation, target):
        mask = commands._REPRESENTATION_MASKS[representation]
        for atom in self._selected_atoms(target):
            atom.reps |= mask
        if target == self.fail_show_target:
            self.fail_show_target = ""
            raise RuntimeError("injected show failure")

    def hide(self, representation, target):
        mask = commands._REPRESENTATION_MASKS[representation]
        for atom in self._selected_atoms(target):
            atom.reps &= ~mask

    def get_type_for_alignment(self, name):
        return self.get_type(name)


class ExtensionCmd:
    def __init__(self):
        self.extensions = {}
        self.auto_arg = {0: {}, 1: {}}
        self.object_sc = object()

    def extend(self, name, function):
        self.extensions[name] = function

    def Shortcut(self, values):
        return tuple(values)


class TaskCommandTests(unittest.TestCase):
    def setUp(self):
        journal.reset_for_tests()
        self.previous_cmd = commands.cmd

    def tearDown(self):
        commands.cmd = self.previous_cmd
        journal.reset_for_tests()

    def test_pp_show_group_is_exactly_undoable_and_preview_is_read_only(self):
        fake = RepresentationCmd()
        commands.cmd = fake
        mask = commands._REPRESENTATION_MASKS["surface"]

        preview = commands.pp_show("surface", "root", preview=1)
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(journal.list_operations(), [])
        self.assertEqual(fake.properties, {})
        self.assertEqual([atom.reps & mask for atom in fake.atoms["first"]], [mask, 0])

        applied = commands.pp_show("surface", "root")
        self.assertEqual(applied["status"], "changed")
        self.assertTrue(
            all(atom.reps & mask for name in ("first", "second") for atom in fake.atoms[name])
        )
        restored = commands.pp_undo(applied["operation_id"])
        self.assertEqual(restored["status"], "restored")
        self.assertEqual([atom.reps & mask for atom in fake.atoms["first"]], [mask, 0])
        self.assertEqual([atom.reps & mask for atom in fake.atoms["second"]], [0, 0])

        all_preview = commands.pp_show("cartoon", preview=1)
        self.assertEqual(all_preview["status"], "preview")
        self.assertEqual(
            set(all_preview["result"]["targets"]), set(fake.atoms)
        )
        unsupported = commands.pp_show("surface", "axis", preview=1)
        self.assertEqual(unsupported["status"], "error")
        self.assertTrue(unsupported["scene_preserved"])

    def test_pp_show_covers_discrete_multistate_atoms(self):
        fake = RepresentationCmd()
        commands.cmd = fake
        mask = commands._REPRESENTATION_MASKS["surface"]
        state_two_atom = RepAtom("first", 3, 0)
        fake.atoms["first"].append(state_two_atom)
        ordinary_get_model = fake.get_model

        def get_model(target, state=1):
            if target == "first":
                selected = fake.atoms[target][:2] if state == 1 else [state_two_atom]
                return types.SimpleNamespace(atom=list(selected))
            return ordinary_get_model(target, state)

        fake.get_model = get_model
        fake.count_states = lambda target: 2 if target == "first" else 1

        applied = commands.pp_show("surface", "first")
        self.assertEqual(applied["status"], "changed")
        self.assertTrue(all(atom.reps & mask for atom in fake.atoms["first"]))
        restored = commands.pp_undo(applied["operation_id"])
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(
            [atom.reps & mask for atom in fake.atoms["first"]], [mask, 0, 0]
        )

    def test_pp_show_undo_refuses_an_identical_same_name_replacement(self):
        fake = RepresentationCmd()
        commands.cmd = fake
        mask = commands._REPRESENTATION_MASKS["surface"]
        applied = commands.pp_show("surface", "second")
        self.assertEqual(applied["status"], "changed")
        fake.atoms["second"] = [
            RepAtom("second", 1, mask),
            RepAtom("second", 2, mask),
        ]
        fake.properties.pop(
            ("second", journal._OBJECT_IDENTITY_PROPERTY), None
        )

        refused = commands.pp_undo(applied["operation_id"], force=1)

        self.assertEqual(refused["status"], "error")
        self.assertTrue(refused["scene_preserved"])
        self.assertIn("same-name replacement", refused["error"])
        self.assertEqual(len(journal.list_operations()), 1)

    def test_pp_show_stale_undo_refuses_then_force_restores(self):
        fake = RepresentationCmd()
        commands.cmd = fake
        mask = commands._REPRESENTATION_MASKS["surface"]
        applied = commands.pp_show("surface", "first")
        fake.atoms["first"][0].reps &= ~mask

        refused = commands.pp_undo(applied["operation_id"])
        self.assertEqual(refused["status"], "error")
        self.assertEqual(len(journal.list_operations()), 1)

        forced = commands.pp_undo(applied["operation_id"], force=1)
        self.assertEqual(forced["status"], "restored")
        self.assertEqual([atom.reps & mask for atom in fake.atoms["first"]], [mask, 0])

    def test_pp_show_failure_rolls_back_and_implicit_multi_match_is_refused(self):
        fake = RepresentationCmd()
        commands.cmd = fake
        mask = commands._REPRESENTATION_MASKS["surface"]
        fake.fail_show_target = "second"

        failed = commands.pp_show("surface", "root")
        self.assertEqual(failed["status"], "error")
        self.assertEqual([atom.reps & mask for atom in fake.atoms["first"]], [mask, 0])
        self.assertEqual([atom.reps & mask for atom in fake.atoms["second"]], [0, 0])
        self.assertEqual(journal.list_operations(), [])

        ambiguous = commands.pp_show(
            "surface", "desgn_alpha", match="fuzzy"
        )
        self.assertEqual(ambiguous["status"], "error")
        self.assertTrue(ambiguous["scene_preserved"])

    def test_registration_uses_console_quote_adapter_for_all_eight_facades(self):
        fake = ExtensionCmd()
        commands.register_commands(fake)
        self.assertEqual(set(fake.extensions), set(commands.TASK_COMMANDS))
        self.assertTrue(
            all(
                getattr(function, "_pymolplugins_console_adapter", False)
                for function in fake.extensions.values()
            )
        )
        self.assertEqual(
            set(fake.auto_arg[0]),
            {
                "pp_show", "pp_nav", "pp_align", "pp_orient",
                "pp_contacts", "pp_metrics", "pp_jobs", "pp_undo",
            },
        )
        self.assertIn("surface", fake.auto_arg[0]["pp_show"][0]())

    def test_pp_nav_dispatches_shorthand_and_actions(self):
        calls = []
        navigation = types.SimpleNamespace(
            pp_nav_start=lambda **kwargs: calls.append(("start", kwargs)) or {"status": "active"},
            pp_nav_next=lambda: calls.append(("next", {})) or {"status": "active"},
            pp_nav_prev=lambda: {"status": "active"},
            pp_nav_next_batch=lambda: {"status": "active"},
            pp_nav_prev_batch=lambda: {"status": "active"},
            pp_nav_goto=lambda value: {"status": "active", "cursor": value},
            pp_nav_pin=lambda value="": {"status": "active"},
            pp_nav_unpin=lambda value="": {"status": "active"},
            pp_nav_status=lambda quiet=0: {"status": "inactive"},
            pp_nav_stop=lambda force=0: {"status": "stopped"},
        )
        with mock.patch.object(commands, "_load", return_value=navigation):
            started = commands.pp_nav("groups", "a,b", window=2, step=1)
            stepped = commands.pp_nav("next")
        self.assertEqual(started["status"], "changed")
        self.assertEqual(stepped["status"], "changed")
        self.assertEqual(calls[0][1]["source"], "groups")
        self.assertEqual(calls[0][1]["entries"], "a,b")
        self.assertEqual(calls[0][1]["window_size"], 2)
        self.assertEqual(calls[1][0], "next")

    def test_pp_nav_rejects_ignored_targets_and_reports_boundaries(self):
        calls = []
        navigation = types.SimpleNamespace(
            pp_nav_next=lambda: calls.append("next") or {
                "status": "boundary", "no_change": True
            },
            pp_nav_prev=lambda: calls.append("prev") or {"status": "active"},
            pp_nav_next_batch=lambda: calls.append("next-page") or {"status": "active"},
            pp_nav_prev_batch=lambda: calls.append("prev-page") or {"status": "active"},
            pp_nav_status=lambda quiet=0: {"status": "inactive"},
            pp_nav_stop=lambda force=0: calls.append("stop") or {"status": "stopped"},
            pp_nav_goto=lambda value: calls.append(("goto", value)) or {"status": "active"},
            pp_nav_pin=lambda value="": calls.append(("pin", value)) or {"status": "active"},
            pp_nav_unpin=lambda value="": calls.append(("unpin", value)) or {"status": "active"},
        )
        with mock.patch.object(commands, "_load", return_value=navigation):
            rejected = commands.pp_nav("prev-page", target="groups")
            boundary = commands.pp_nav("next")
            rejected_option = commands.pp_nav("next", window=2)
        self.assertEqual(rejected["status"], "error")
        self.assertTrue(rejected["scene_preserved"])
        self.assertEqual(calls, ["next"])
        self.assertEqual(boundary["status"], "unchanged")
        self.assertEqual(rejected_option["status"], "error")
        self.assertTrue(rejected_option["scene_preserved"])

    def test_pp_nav_stale_gate_reports_that_the_scene_was_preserved(self):
        navigation = types.SimpleNamespace(
            pp_nav_next=lambda: {
                "status": "stale",
                "error": "group membership changed before transition",
            },
            pp_nav_prev=lambda: {"status": "active"},
            pp_nav_next_batch=lambda: {"status": "active"},
            pp_nav_prev_batch=lambda: {"status": "active"},
            pp_nav_status=lambda quiet=0: {"status": "active"},
        )
        with mock.patch.object(commands, "_load", return_value=navigation):
            result = commands.pp_nav("next")

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["scene_preserved"])

    def test_pp_align_infers_mode_and_journals_only_commits(self):
        fake = RepresentationCmd()
        commands.cmd = fake
        calls = []
        alignment = types.SimpleNamespace(
            pp_align_anchor=lambda *args, **kwargs: calls.append(("anchor", args, kwargs))
            or {"id": "align_1", "committed": bool(kwargs["commit"])},
            pp_align_motif=lambda *args, **kwargs: calls.append(("motif", args, kwargs))
            or {"id": "align_2", "committed": bool(kwargs["commit"])},
            pp_align_ligand=lambda *args, **kwargs: {"id": "align_3", "committed": True},
            pp_align_commit=lambda value: {"id": value, "committed": True},
        )
        with mock.patch.object(commands, "_load", return_value=alignment):
            preview = commands.pp_align("first", "second", preview=1)
            applied = commands.pp_align("first", "second", motif="GGH")
        self.assertEqual(preview["status"], "preview")
        self.assertNotIn("operation_id", preview)
        self.assertEqual(applied["status"], "changed")
        self.assertIn("operation_id", applied)
        self.assertEqual(calls[0][0], "anchor")
        self.assertIn("polymer and name CA", calls[0][1][0])
        self.assertEqual(calls[1][0], "motif")

    def test_pp_orient_and_unified_undo_route_to_canonical_record(self):
        geometry = types.SimpleNamespace(
            pp_orient_pca=lambda **kwargs: {
                "committed": True,
                "operation_id": "orient_1",
                "move_objects": ["obj"],
                "axis": {"confidence": 0.9},
            },
            pp_orientation_undo=lambda reference, force=0: {
                "status": "restored",
                "operation_id": reference,
            },
        )
        with mock.patch.object(commands, "_load", return_value=geometry):
            applied = commands.pp_orient("obj")
            restored = commands.pp_undo(applied["operation_id"])
        self.assertEqual(applied["status"], "changed")
        self.assertEqual(restored["status"], "restored")

    def test_pp_orient_requires_an_explicit_target_before_delegation(self):
        with mock.patch.object(commands, "_load") as load:
            result = commands.pp_orient()
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["scene_preserved"])
        load.assert_not_called()

    def test_pp_orient_rejects_irrelevant_vector_and_pca_options(self):
        geometry = mock.Mock()
        with mock.patch.object(commands, "_load", return_value=geometry):
            vector_force = commands.pp_orient(
                "model", axis="x", force=1, preview=1
            )
            vector_subunits = commands.pp_orient(
                "model", axis="x", subunits="A,B", preview=1
            )
            pca_subunits = commands.pp_orient(
                "model", axis="pca", subunits="A,B", preview=1
            )

        for result in (vector_force, vector_subunits, pca_subunits):
            self.assertEqual(result["status"], "error")
            self.assertTrue(result["scene_preserved"])
        geometry._move_objects.assert_not_called()
        geometry.pp_orient_pca.assert_not_called()

    def test_pp_contacts_auto_requires_one_ligand_and_records_exact_group(self):
        organic = types.SimpleNamespace(
            atom=[
                types.SimpleNamespace(
                    model="complex", segi="", chain="X", resi="1",
                    inscode="", resn="LIG"
                )
            ]
        )
        controller = types.SimpleNamespace(
            cmd=types.SimpleNamespace(get_model=lambda selection, state: organic)
        )
        owner_reference = "ppi-run:run-sync:ppi_contacts"
        preview_calls = []

        def run_interaction(*args, **kwargs):
            commands._record_interaction_completion(
                {
                    "status": "completed",
                    "owner_reference": owner_reference,
                    "replaced_owner_reference": "",
                    "preset": args[0],
                    "group": "ppi_contacts",
                }
            )
            return "ppi_contacts"

        interactions = types.SimpleNamespace(
            get_controller=lambda: controller,
            _resolve_public_source=lambda controller, source, state: source or "complex",
            _bounded_selection=lambda source, partner: "(%s) and (%s)" % (source, partner),
            _count_atoms=lambda cmd, selection, state: 1,
            preview_interaction=lambda *args, **kwargs: preview_calls.append(
                (args, kwargs)
            )
            or {"status": "preview", "preset": args[0]},
            pp_interact_run=run_interaction,
            interaction_owner_reference=lambda group: owner_reference,
            pp_interact_clear=lambda group: {"root_group": group},
        )
        with mock.patch.object(commands, "_load", return_value=interactions):
            result = commands.pp_contacts("complex")
            undone = commands.pp_undo(result["operation_id"])
            plip = commands.pp_contacts(
                "complex", focus="resn LIG", preset="plip_overview", preview=1
            )
            invalid_preview_wait = commands.pp_contacts(
                "complex", preview=1, wait="bad"
            )
            ignored_preview_wait = commands.pp_contacts(
                "complex", preview=1, wait=1
            )
            alias_plans = {
                alias: commands.pp_contacts(
                    "complex", focus="resn LIG", preset=alias, preview=1
                )
                for alias in ("contacts", "ligand", "binding_site")
            }
        self.assertEqual(result["status"], "changed")
        self.assertEqual(result["operation"]["reference"], owner_reference)
        self.assertEqual(undone["status"], "restored")
        self.assertEqual(len(journal.list_operations(include_undone=True)), 1)
        self.assertEqual(plip["result"]["partner1"], "")
        self.assertEqual(plip["result"]["partner2"], "resn LIG")
        self.assertEqual(invalid_preview_wait["status"], "error")
        self.assertTrue(invalid_preview_wait["scene_preserved"])
        self.assertEqual(ignored_preview_wait["status"], "error")
        self.assertTrue(ignored_preview_wait["scene_preserved"])
        for alias, planned in alias_plans.items():
            with self.subTest(alias=alias):
                self.assertEqual(planned["result"]["partner1"], "")
                self.assertEqual(planned["result"]["partner2"], "resn LIG")
        self.assertEqual(len(preview_calls), 4)

    def test_interaction_completion_supersedes_old_owner_and_undo_is_exact(self):
        first_reference = "ppi-run:run1:ppi_stable"
        second_reference = "ppi-run:run2:ppi_stable"
        first = commands._record_interaction_completion(
            {
                "status": "completed",
                "owner_reference": first_reference,
                "replaced_owner_reference": "",
                "preset": "ligand_contacts",
                "group": "ppi_stable",
            }
        )
        duplicate = commands._record_interaction_completion(
            {
                "status": "completed",
                "owner_reference": first_reference,
                "replaced_owner_reference": "",
                "preset": "ligand_contacts",
                "group": "ppi_stable",
            }
        )
        second = commands._record_interaction_completion(
            {
                "status": "completed",
                "owner_reference": second_reference,
                "replaced_owner_reference": first_reference,
                "preset": "ligand_contacts",
                "group": "ppi_stable",
            }
        )
        self.assertEqual(duplicate["operation_id"], first["operation_id"])
        self.assertEqual(
            [entry["operation_id"] for entry in journal.list_operations()],
            [second["operation_id"]],
        )
        inactive = {
            entry["operation_id"]: entry
            for entry in journal.list_operations(include_undone=True)
        }
        self.assertEqual(inactive[first["operation_id"]]["status"], "superseded")
        self.assertEqual(
            inactive[first["operation_id"]]["superseded_by"], second_reference
        )

        cleared = []
        interactions = types.SimpleNamespace(
            pp_interact_clear=lambda reference: cleared.append(reference)
            or {"status": "cleared", "owner_reference": reference}
        )
        with mock.patch.object(commands, "_load", return_value=interactions):
            stale = commands.pp_undo(first["operation_id"])
            restored = commands.pp_undo(second["operation_id"])
        self.assertEqual(stale["status"], "error")
        self.assertTrue(stale["scene_preserved"])
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(restored["delegated_command"], "pp_interact_clear")
        self.assertEqual(cleared, [second_reference])

    def test_contacts_preview_prints_a_copyable_exact_apply_command(self):
        controller = types.SimpleNamespace(cmd=object())

        def count_atoms(_cmd, selection, _state):
            return 0 if ")) and ((" in selection else 1

        interactions = types.SimpleNamespace(
            get_controller=lambda: controller,
            _resolve_public_source=lambda _controller, _source, _state: "complex",
            _bounded_selection=lambda source, value: "(%s) and (%s)"
            % (source, value),
            _count_atoms=count_atoms,
            preview_interaction=lambda *args, **kwargs: {
                "status": "preview",
                "preset": args[0],
            },
        )
        output = io.StringIO()
        with mock.patch.object(commands, "_load", return_value=interactions):
            with redirect_stdout(output):
                result = commands.pp_contacts(
                    "complex",
                    focus='chain A and name "CA,C1"',
                    partner="chain B and name O5'",
                    preset="interchain_basic",
                    preview=1,
                    group="review group",
                    state=2,
                )

        self.assertEqual(result["status"], "preview")
        self.assertIn(
            "Next: pp_contacts complex, "
            "focus='chain A and name \"CA,C1\"', "
            'partner="chain B and name O5\'", '
            "preset=interchain_basic, group=\"review group\", state=2",
            output.getvalue(),
        )

    def test_async_completion_listener_records_exactly_one_operation(self):
        listeners = {}
        previous_module = commands._INTERACTION_LISTENER_MODULE
        previous_token = commands._INTERACTION_LISTENER_TOKEN

        def register(callback):
            listeners["listener-1"] = callback
            return "listener-1"

        def unregister(token):
            listeners.pop(token, None)

        interactions = types.SimpleNamespace(
            register_interaction_completion_listener=register,
            unregister_interaction_completion_listener=unregister,
        )
        event = {
            "status": "completed",
            "owner_reference": "ppi-run:async-1:ppi_async",
            "replaced_owner_reference": "",
            "preset": "ligand_contacts",
            "group": "ppi_async",
        }
        try:
            commands._INTERACTION_LISTENER_MODULE = None
            commands._INTERACTION_LISTENER_TOKEN = ""
            token = commands._ensure_interaction_completion_listener(interactions)
            listeners[token](dict(event))
            listeners[token](dict(event))
            operations = journal.list_operations(include_undone=True)
            self.assertEqual(len(operations), 1)
            self.assertEqual(operations[0]["reference"], event["owner_reference"])
        finally:
            commands._INTERACTION_LISTENER_MODULE = previous_module
            commands._INTERACTION_LISTENER_TOKEN = previous_token

    def test_pp_metrics_never_guesses_partners_and_keeps_async_health_off_gui(self):
        calls = []
        remote = types.SimpleNamespace(
            worker_request=lambda *args, **kwargs: {
                "rosetta": {"health": {"available": True}}
            },
            pp_rosetta_metrics_async=lambda **kwargs: calls.append(kwargs)
            or {"status": "submitted", "job_id": "job1"},
            pp_rosetta_metrics=lambda **kwargs: {"status": "ok"},
            preview_rosetta_metrics=lambda **kwargs: {
                "status": "preview",
                "worker_contacted": False,
                "source_object": kwargs["selection"],
                **kwargs,
            },
        )
        with mock.patch.object(commands, "_load", return_value=remote):
            missing = commands.pp_metrics("complex")
            preview = commands.pp_metrics(
                "complex", target="A", binder="B", preview=1
            )
            submitted = commands.pp_metrics("complex", target="A", binder="B")
        self.assertEqual(missing["status"], "error")
        self.assertEqual(preview["status"], "preview")
        self.assertFalse(preview["result"]["worker_contacted"])
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(calls[0]["target_chains"], "A")
        self.assertEqual(calls[0]["binder_chains"], "B")

    def test_pp_metrics_rejects_invalid_wait_before_worker_access(self):
        with mock.patch.object(commands, "_load") as load:
            missing_source = commands.pp_metrics(target="A", binder="B")
            result = commands.pp_metrics(
                "complex", target="A", binder="B", wait="bad"
            )
            duplicate = commands.pp_metrics(
                "complex", target="A,A", binder="B"
            )
            ignored_preview_wait = commands.pp_metrics(
                "complex", target="A", binder="B", preview=1, wait=1
            )
        self.assertEqual(missing_source["status"], "error")
        self.assertTrue(missing_source["scene_preserved"])
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["scene_preserved"])
        self.assertEqual(duplicate["status"], "error")
        self.assertTrue(duplicate["scene_preserved"])
        self.assertEqual(ignored_preview_wait["status"], "error")
        self.assertTrue(ignored_preview_wait["scene_preserved"])
        load.assert_not_called()

    def test_pp_jobs_lists_and_inspects_without_dumping_full_json(self):
        remote = types.SimpleNamespace(
            pp_worker_jobs=lambda quiet=0: {
                "active": {"job1": {"kind": "rosetta_metrics", "status": "running"}},
                "history": [{"job_id": "job0", "kind": "plip", "status": "failed"}],
            },
            pp_worker_cancel=lambda job_id: {"status": "cancel_requested", "job_id": job_id},
        )
        with mock.patch.object(commands, "_load", return_value=remote):
            stream = io.StringIO()
            with redirect_stdout(stream):
                listed = commands.pp_jobs()
                inspected = commands.pp_jobs("job0")
        self.assertEqual(listed["status"], "listed")
        self.assertEqual(inspected["status"], "inspected")
        self.assertEqual(inspected["result"]["job_id"], "job0")
        self.assertEqual(inspected["result"]["status"], "failed")
        self.assertIn("1 active, 1 recent terminal", stream.getvalue())

    def test_console_facades_normalize_mixed_quoted_positional_and_keywords(self):
        extension = ExtensionCmd()
        commands.register_commands(extension)
        fake = RepresentationCmd()
        fake.types["next"] = "object:molecule"
        fake.atoms["next"] = [RepAtom("next", 1, 0)]
        commands.cmd = fake
        navigation_calls = []
        navigation = types.SimpleNamespace(
            pp_nav_start=lambda **kwargs: navigation_calls.append(kwargs)
            or {"status": "active"},
            pp_nav_next=lambda: {"status": "active"},
            pp_nav_prev=lambda: {"status": "active"},
            pp_nav_next_batch=lambda: {"status": "active"},
            pp_nav_prev_batch=lambda: {"status": "active"},
            pp_nav_goto=lambda value: {"status": "active", "cursor": value},
            pp_nav_pin=lambda value="": {"status": "active", "entry": value},
            pp_nav_unpin=lambda value="": {"status": "active", "entry": value},
            pp_nav_status=lambda quiet=0: {"status": "inactive"},
            pp_nav_stop=lambda force=0: {"status": "stopped"},
        )

        with mock.patch.object(commands, "_load", return_value=navigation):
            started = extension.extensions["pp_nav"](
                '"start"',
                target='"next"',
                source='"objects"',
                window='"2"',
                page='"4"',
                wrap='"0"',
                replace='"1"',
            )
            comma_entries = extension.extensions["pp_nav"](
                '"start"', target='"group_a,group_b"', source='"groups"'
            )
        shown = extension.extensions["pp_show"](
            '"surface"',
            '"root"',
            mode='"show"',
            match='"exact"',
            preview='"1"',
        )

        self.assertEqual(started["status"], "changed")
        self.assertEqual(navigation_calls[0]["entries"], "next")
        self.assertEqual(navigation_calls[0]["source"], "objects")
        self.assertEqual(navigation_calls[0]["window_size"], 2)
        self.assertEqual(navigation_calls[0]["batch_size"], 4)
        self.assertEqual(comma_entries["status"], "changed")
        self.assertEqual(navigation_calls[1]["entries"], "group_a,group_b")
        self.assertEqual(navigation_calls[1]["source"], "groups")
        self.assertEqual(shown["status"], "preview")
        self.assertEqual(set(shown["result"]["targets"]), {"first", "second"})

    def test_pp_nav_disambiguates_reserved_actions_from_explicit_targets(self):
        calls = []
        navigation = types.SimpleNamespace(
            pp_nav_start=lambda **kwargs: calls.append(("start", kwargs))
            or {"status": "active"},
            pp_nav_next=lambda: calls.append(("next", {})) or {"status": "active"},
            pp_nav_prev=lambda: {"status": "active"},
            pp_nav_next_batch=lambda: {"status": "active"},
            pp_nav_prev_batch=lambda: {"status": "active"},
            pp_nav_goto=lambda value: {"status": "active", "cursor": value},
            pp_nav_pin=lambda value="": {"status": "active", "entry": value},
            pp_nav_unpin=lambda value="": {"status": "active", "entry": value},
            pp_nav_status=lambda quiet=0: {"status": "inactive"},
            pp_nav_stop=lambda force=0: {"status": "stopped"},
        )
        with mock.patch.object(commands, "_load", return_value=navigation):
            action = commands.pp_nav("next")
            named_next = commands.pp_nav(target="next", source="objects")
            named_groups = commands.pp_nav(target="groups", source="objects")
            source_alias = commands.pp_nav("groups")
            call_count = len(calls)
            rejected_unused = commands.pp_nav("next", target="model")

        self.assertEqual(action["delegated_command"], "pp_nav_next")
        self.assertEqual(calls[0][0], "next")
        self.assertEqual(calls[1][1]["entries"], "next")
        self.assertEqual(calls[1][1]["source"], "objects")
        self.assertEqual(calls[2][1]["entries"], "groups")
        self.assertEqual(calls[2][1]["source"], "objects")
        self.assertEqual(calls[3][1]["entries"], "")
        self.assertEqual(calls[3][1]["source"], "groups")
        self.assertEqual(named_next["status"], "changed")
        self.assertEqual(named_groups["status"], "changed")
        self.assertEqual(source_alias["status"], "changed")
        self.assertEqual(rejected_unused["status"], "error")
        self.assertTrue(rejected_unused["scene_preserved"])
        self.assertEqual(len(calls), call_count)

    def test_pp_show_all_matches_and_whitelist_rejections_preserve_scene(self):
        fake = RepresentationCmd()
        fake.types.update(
            {
                "overlay_cgo": "object:cgo",
                "density_map": "object:map",
                "distance_measurement": "object:measurement",
            }
        )
        commands.cmd = fake
        mask = commands._REPRESENTATION_MASKS["surface"]
        before = {
            name: [atom.reps for atom in atoms]
            for name, atoms in fake.atoms.items()
        }

        all_preview = commands.pp_show("surface", preview=1)
        self.assertEqual(all_preview["status"], "preview")
        self.assertEqual(
            set(all_preview["result"]["targets"]), set(fake.atoms)
        )

        rejected = commands.pp_show("wireframe", "root")
        bad_mode = commands.pp_show("surface", "root", mode="toggle")

        self.assertEqual(rejected["status"], "error")
        self.assertTrue(rejected["scene_preserved"])
        self.assertEqual(bad_mode["status"], "error")
        self.assertTrue(bad_mode["scene_preserved"])
        self.assertEqual(
            before,
            {name: [atom.reps for atom in atoms] for name, atoms in fake.atoms.items()},
        )

        applied = commands.pp_show(
            "surface",
            "desgn_alpha",
            match="fuzzy",
            all_matches=1,
        )
        self.assertEqual(applied["status"], "changed")
        self.assertEqual(
            set(applied["result"]["targets"]),
            {"design_alpha", "design_alfa"},
        )
        self.assertTrue(fake.atoms["design_alpha"][0].reps & mask)
        self.assertTrue(fake.atoms["design_alfa"][0].reps & mask)

    def test_pp_align_rejects_motif_ligand_conflict_before_delegation(self):
        calls = []
        alignment = types.SimpleNamespace(
            pp_align_anchor=lambda *args, **kwargs: calls.append("anchor"),
            pp_align_motif=lambda *args, **kwargs: calls.append("motif"),
            pp_align_ligand=lambda *args, **kwargs: calls.append("ligand"),
            pp_align_commit=lambda *args, **kwargs: calls.append("commit"),
        )
        extension = ExtensionCmd()
        commands.register_commands(extension)
        with mock.patch.object(commands, "_load", return_value=alignment):
            direct = commands.pp_align(
                "first", "second", motif="GGH", ligand="LIG"
            )
            quoted = extension.extensions["pp_align"](
                '"first"',
                reference='"second"',
                motif='"GGH"',
                ligand='"LIG"',
            )
            apply_conflict = commands.pp_align(
                "apply", "align_record", motif="GGH", ligand="LIG"
            )
            apply_preview = commands.pp_align(
                "apply", "align_record", preview=1
            )

        self.assertEqual(direct["status"], "error")
        self.assertTrue(direct["scene_preserved"])
        self.assertEqual(quoted["status"], "error")
        self.assertTrue(quoted["scene_preserved"])
        self.assertEqual(apply_conflict["status"], "error")
        self.assertTrue(apply_conflict["scene_preserved"])
        self.assertEqual(apply_preview["status"], "error")
        self.assertTrue(apply_preview["scene_preserved"])
        self.assertEqual(calls, [])

    def test_pp_metrics_validates_quoted_partners_before_worker_access(self):
        calls = []
        remote = types.SimpleNamespace(
            worker_request=lambda *args, **kwargs: calls.append(("health", kwargs))
            or {"rosetta": {"health": {"available": True}}},
            pp_rosetta_metrics_async=lambda **kwargs: calls.append(("submit", kwargs))
            or {"status": "submitted", "job_id": "job1"},
            pp_rosetta_metrics=lambda **kwargs: {"status": "ok"},
        )
        extension = ExtensionCmd()
        commands.register_commands(extension)
        with mock.patch.object(commands, "_load", return_value=remote):
            missing = extension.extensions["pp_metrics"](
                '"complex"', target='"A"', binder='""'
            )
            overlap = extension.extensions["pp_metrics"](
                '"complex"', target='"A,B"', binder='"B,C"'
            )
            invalid_wait = extension.extensions["pp_metrics"](
                '"complex"', target='"A"', binder='"B"', wait='"bad"'
            )
            submitted = extension.extensions["pp_metrics"](
                '"complex and polymer"',
                target='"A,C"',
                binder='"B,D"',
                profile='"surface"',
                output_json='"C:\\path with spaces\\metrics.json"',
                url='"http://127.0.0.1:8765"',
            )

        self.assertEqual(missing["status"], "error")
        self.assertTrue(missing["scene_preserved"])
        self.assertEqual(overlap["status"], "error")
        self.assertTrue(overlap["scene_preserved"])
        self.assertEqual(invalid_wait["status"], "error")
        self.assertTrue(invalid_wait["scene_preserved"])
        self.assertEqual([kind for kind, _ in calls], ["submit"])
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(calls[-1][1]["target_chains"], "A,C")
        self.assertEqual(calls[-1][1]["binder_chains"], "B,D")
        self.assertEqual(calls[-1][1]["metrics"], "cms")
        self.assertEqual(calls[-1][1]["selection"], "complex and polymer")
        self.assertEqual(
            calls[-1][1]["output_json"],
            "C:\\path with spaces\\metrics.json",
        )
        self.assertEqual(calls[-1][1]["url"], "http://127.0.0.1:8765")

    def test_pp_jobs_and_pp_undo_accept_exact_quoted_ids(self):
        remote = types.SimpleNamespace(
            pp_worker_jobs=lambda quiet=0: {
                "active": {"job1": {"kind": "metrics", "status": "running"}},
                "history": [{"job_id": "job0", "kind": "plip", "status": "failed"}],
            },
            pp_worker_cancel=lambda job_id: {
                "status": "cancel_requested",
                "job_id": job_id,
            },
        )
        interaction_calls = []
        interactions = types.SimpleNamespace(
            pp_interact_clear=lambda group: interaction_calls.append(group)
            or {"status": "cleared", "root_group": group}
        )
        extension = ExtensionCmd()
        commands.register_commands(extension)
        operation = journal.record_operation("contacts", "ppi_exact", "contacts")

        def load(name):
            if name == "pymolplugins.remote.commands":
                return remote
            if name == "pymolplugins.interactions.controller":
                return interactions
            raise AssertionError(name)

        with mock.patch.object(commands, "_load", side_effect=load):
            inspected = extension.extensions["pp_jobs"]('"job0"')
            cancelled = extension.extensions["pp_jobs"](
                '"cancel"', job_id='"job1"'
            )
            preview = extension.extensions["pp_undo"](
                '"%s"' % operation["operation_id"], preview='"1"'
            )
            restored = extension.extensions["pp_undo"](
                '"%s"' % operation["operation_id"]
            )
            unknown_job = extension.extensions["pp_jobs"]('"missing"')
            unknown_undo = extension.extensions["pp_undo"]('"missing"')
            ambiguous_jobs = extension.extensions["pp_jobs"](
                '"job1"', job_id='"job0"'
            )

        self.assertEqual(inspected["status"], "inspected")
        self.assertEqual(inspected["result"]["job_id"], "job0")
        self.assertEqual(cancelled["result"]["job_id"], "job1")
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(interaction_calls, ["ppi_exact"])
        self.assertEqual(unknown_job["status"], "error")
        self.assertTrue(unknown_job["scene_preserved"])
        self.assertEqual(unknown_undo["status"], "error")
        self.assertTrue(unknown_undo["scene_preserved"])
        self.assertEqual(ambiguous_jobs["status"], "error")
        self.assertTrue(ambiguous_jobs["scene_preserved"])

    def test_pp_undo_rejects_an_ambiguous_owner_reference(self):
        first = journal.record_operation("contacts", "same_group", "first")
        second = journal.record_operation("contacts", "same_group", "second")

        ambiguous = commands.pp_undo("same_group")

        self.assertEqual(ambiguous["status"], "error")
        self.assertTrue(ambiguous["scene_preserved"])
        self.assertIn(first["operation_id"], ambiguous["error"])
        self.assertIn(second["operation_id"], ambiguous["error"])
        self.assertEqual(len(journal.list_operations()), 2)

    def test_pp_undo_preview_rejects_an_invalid_force_token(self):
        operation = journal.record_operation("contacts", "group", "contacts")

        result = commands.pp_undo(
            operation["operation_id"], preview=1, force="bad"
        )

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["scene_preserved"])
        self.assertEqual(len(journal.list_operations()), 1)

    def test_pp_undo_list_and_preview_reject_irrelevant_flags(self):
        operation = journal.record_operation("contacts", "group", "contacts")

        list_force = commands.pp_undo("list", force=1)
        list_preview = commands.pp_undo("list", preview=1)
        preview_force = commands.pp_undo(
            operation["operation_id"], preview=1, force=1
        )

        for result in (list_force, list_preview, preview_force):
            self.assertEqual(result["status"], "error")
            self.assertTrue(result["scene_preserved"])
        self.assertEqual(len(journal.list_operations()), 1)

    def test_delegated_partial_errors_preserve_scene_preserved_false(self):
        delegated_error = {
            "status": "error",
            "error": "injected partial mutation",
            "scene_preserved": False,
        }
        navigation = types.SimpleNamespace(
            pp_nav_next=lambda: dict(delegated_error),
            pp_nav_prev=lambda: {"status": "active"},
            pp_nav_next_batch=lambda: {"status": "active"},
            pp_nav_prev_batch=lambda: {"status": "active"},
            pp_nav_status=lambda quiet=0: {"status": "active"},
        )
        alignment = types.SimpleNamespace(
            pp_align_motif=lambda *args, **kwargs: dict(delegated_error),
        )
        remote = types.SimpleNamespace(
            worker_request=lambda *args, **kwargs: {
                "rosetta": {"health": {"available": True}}
            },
            pp_rosetta_metrics_async=lambda **kwargs: dict(delegated_error),
        )
        operation = journal.record_operation("contacts", "partial_group", "partial")
        interactions = types.SimpleNamespace(
            pp_interact_clear=lambda group: dict(delegated_error)
        )

        with mock.patch.object(commands, "_load", return_value=navigation):
            nav_result = commands.pp_nav("next")
        with mock.patch.object(commands, "_load", return_value=alignment):
            align_result = commands.pp_align(
                "first", "second", motif="GGH"
            )
        with mock.patch.object(commands, "_load", return_value=remote):
            metrics_result = commands.pp_metrics(
                "complex", target="A", binder="B"
            )
        with mock.patch.object(commands, "_load", return_value=interactions):
            undo_result = commands.pp_undo(operation["operation_id"])

        for result in (nav_result, align_result, metrics_result, undo_result):
            self.assertEqual(result["status"], "error")
            self.assertFalse(result["scene_preserved"])
        self.assertEqual(len(journal.list_operations()), 1)

    def test_worker_errors_without_scene_mutation_report_scene_preserved(self):
        worker_error = {"status": "error", "error": "worker unavailable"}
        remote = types.SimpleNamespace(
            worker_request=lambda *args, **kwargs: {
                "rosetta": {"health": {"available": True}}
            },
            pp_rosetta_metrics_async=lambda **kwargs: dict(worker_error),
            pp_worker_cancel=lambda job_id: dict(worker_error),
        )

        with mock.patch.object(commands, "_load", return_value=remote):
            metrics = commands.pp_metrics(
                "complex", target="A", binder="B"
            )
            cancelled = commands.pp_jobs("cancel", "job1")

        self.assertEqual(metrics["status"], "error")
        self.assertTrue(metrics["scene_preserved"])
        self.assertEqual(cancelled["status"], "error")
        self.assertTrue(cancelled["scene_preserved"])


if __name__ == "__main__":
    unittest.main()
