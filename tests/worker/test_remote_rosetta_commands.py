import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from pymolplugins.interactions.controller import RemoteInteractionStaleError
from pymolplugins.remote.client import WorkerRequestError


class _FakeCmd:
    def __init__(self):
        self.extensions = {}
        self.object_list = ["complex"]
        self.object_types = {"complex": "object:molecule"}
        self.get_object_list_calls = []
        self.get_pdbstr_calls = []
        self.settings = {
            "pdb_conect_all": "0",
            "pdb_conect_nodup": "0",
        }

    def extend(self, name, function):
        self.extensions[name] = function

    def get_setting_text(self, name):
        return self.settings[name]

    def set(self, name, value):
        self.settings[name] = str(value)

    def get_pdbstr(self, selection, state=1):
        self.get_pdbstr_calls.append((selection, state))
        return "ATOM      1  CA  ALA A   1       0.000   0.000   0.000\nEND\n"

    def get_object_list(self, selection="(all)"):
        self.get_object_list_calls.append(selection)
        return list(self.object_list)

    def get_type(self, name):
        return self.object_types.get(name, "object:molecule")


class _BoundSignal:
    def __init__(self):
        self.callback = None

    def connect(self, callback, *args):
        self.callback = callback

    def emit(self, *args):
        if self.callback is not None:
            self.callback(*args)


class _Signal:
    def __set_name__(self, owner, name):
        self.name = "_signal_" + name

    def __get__(self, instance, owner):
        if instance is None:
            return self
        value = getattr(instance, self.name, None)
        if value is None:
            value = _BoundSignal()
            setattr(instance, self.name, value)
        return value


class _ThreadPool:
    instance = None

    def __init__(self):
        self.started = []

    @classmethod
    def globalInstance(cls):
        if cls.instance is None:
            cls.instance = cls()
        return cls.instance

    def start(self, runnable):
        self.started.append(runnable)


class _PlipCommitController:
    def __init__(self):
        self.commits = []
        self.stale_runs = set()

    def commit_remote_result(
        self, submission, payload, *, expected_current=False
    ):
        if not expected_current:
            raise AssertionError("async PLIP completion skipped the current-source gate")
        if submission.request["run_id"] in self.stale_runs:
            raise RemoteInteractionStaleError("synthetic changed source")
        self.commits.append((submission.request["run_id"], payload))
        return types.SimpleNamespace(
            manifest=types.SimpleNamespace(root_group=submission.output_group),
            result=types.SimpleNamespace(
                summary={"counts_by_type": {"hydrogen_bond": 1}},
                warnings=(),
            ),
        )


def _slot(*args, **kwargs):
    return lambda function: function


def _fake_qt_core():
    return types.SimpleNamespace(
        QObject=object,
        QRunnable=object,
        Signal=lambda *args: _Signal(),
        Slot=_slot,
        QCoreApplication=types.SimpleNamespace(instance=lambda: object()),
        QThreadPool=_ThreadPool,
        Qt=types.SimpleNamespace(
            QueuedConnection=0,
            ConnectionType=types.SimpleNamespace(QueuedConnection=0),
        ),
    )


def _load_commands_module(module=None, fake_cmd=None):
    fake_cmd = fake_cmd or _FakeCmd()
    pymol = types.ModuleType("pymol")
    pymol.cmd = fake_cmd
    qt = types.ModuleType("pymol.Qt")
    qt.QtCore = _fake_qt_core()
    module_name = "pymolplugins.remote._commands_contract_test"
    filename = (
        Path(__file__).resolve().parents[2]
        / "pymolplugins"
        / "remote"
        / "commands.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, filename)
    module = module or importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"pymol": pymol, "pymol.Qt": qt}):
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module, fake_cmd


def _payload_and_result(module):
    payload = module._rosetta_payload(
        "complex",
        "A",
        "B",
        "cms,bunsat_bound_heavy,bunsat_interface_delta",
        1,
    )
    expected = module._expected_rosetta_contract(payload)
    result = {
        "schema": module.ROSETTA_SCHEMA,
        "input_sha256": hashlib.sha256(
            payload["structure"]["text"].encode("utf-8")
        ).hexdigest(),
        "partners": {
            "target_chains": ["A"],
            "binder_chains": ["B"],
        },
        "metrics": {
            "cms": 12.5,
            "bunsat_bound_heavy": 4,
            "bunsat_interface_delta": 2,
        },
        "provenance": {
            "backend": "pyrosetta",
            "pyrosetta_version": "test-pyrosetta",
            "rosetta_version": "test-rosetta",
            "init_flags": "-test",
            "selectors": {
                "target": {"chains": ["A"]},
                "binder": {"chains": ["B"]},
                "interface": {"expression": "A_B"},
                "pose_chains": ["A", "B"],
            },
            "options": {},
            "resolved_options": {},
            "cutoffs": {},
            "scorefunction": {},
        },
        "warnings": [],
    }
    return payload, expected, result


def _plip_submission(label, group):
    return types.SimpleNamespace(
        request={
            "run_id": label,
            "source": {"object": "complex", "state": 1},
        },
        preset="plip_overview",
        group=group,
        output_group=group,
        output_json="",
    )


class RemoteRosettaCommandContractTests(unittest.TestCase):
    def setUp(self):
        _ThreadPool.instance = None
        self.module, self.cmd = _load_commands_module()

    def assert_contract_error(self, result, expected):
        with self.assertRaises(WorkerRequestError) as captured:
            self.module._finish_rosetta_result(result, expected)
        self.assertEqual(captured.exception.code, "scientific_contract_error")

    def test_valid_result_is_bound_to_the_submitted_contract(self):
        _, expected, result = _payload_and_result(self.module)
        result["provenance"]["selectors"]["pose_chains"] = ["", "A", "B"]

        with redirect_stdout(io.StringIO()):
            finished = self.module._finish_rosetta_result(result, expected)

        self.assertEqual(finished["metrics"]["cms"], 12.5)

    def test_current_backend_success_payload_passes_host_v1_validator(self):
        from tests.worker.test_rosetta_backend import FakePyRosetta, backend_with

        payload = self.module._rosetta_payload(
            "complex",
            "A",
            "B",
            "cms,bunsat_bound_heavy,bunsat_interface_delta",
            1,
        )
        result = backend_with(FakePyRosetta()).analyze(payload)
        expected = self.module._expected_rosetta_contract(payload)

        with redirect_stdout(io.StringIO()):
            finished = self.module._finish_rosetta_result(result, expected)

        self.assertEqual(finished["schema"], self.module.ROSETTA_SCHEMA)

    def test_empty_metrics_wrong_hash_and_wrong_partners_are_rejected(self):
        _, expected, valid = _payload_and_result(self.module)
        cases = []
        empty = dict(valid)
        empty["metrics"] = {}
        cases.append(empty)
        wrong_hash = dict(valid)
        wrong_hash["input_sha256"] = "0" * 64
        cases.append(wrong_hash)
        wrong_partners = dict(valid)
        wrong_partners["partners"] = {
            "target_chains": ["B"],
            "binder_chains": ["A"],
        }
        cases.append(wrong_partners)

        for result in cases:
            with self.subTest(result=result):
                self.assert_contract_error(result, expected)
        self.assert_contract_error(valid, {})

    def test_wrong_backend_types_and_nonfinite_metrics_are_rejected(self):
        _, expected, valid = _payload_and_result(self.module)
        wrong_backend = dict(valid)
        wrong_backend["provenance"] = dict(valid["provenance"])
        wrong_backend["provenance"]["backend"] = "not-pyrosetta"
        wrong_count = dict(valid)
        wrong_count["metrics"] = dict(valid["metrics"])
        wrong_count["metrics"]["bunsat_bound_heavy"] = 4.0
        wrong_float = dict(valid)
        wrong_float["metrics"] = dict(valid["metrics"])
        wrong_float["metrics"]["cms"] = 12
        nonfinite = dict(valid)
        nonfinite["metrics"] = dict(valid["metrics"])
        nonfinite["metrics"]["cms"] = float("nan")

        for result in (wrong_backend, wrong_count, wrong_float, nonfinite):
            with self.subTest(result=result):
                self.assert_contract_error(result, expected)

    def test_contract_mismatch_never_writes_output(self):
        _, expected, result = _payload_and_result(self.module)
        result["input_sha256"] = "f" * 64
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "result.json")
            with open(output, "w", encoding="utf-8") as handle:
                handle.write("keep\n")

            with self.assertRaises(WorkerRequestError):
                self.module._finish_rosetta_result(result, expected, output)

            with open(output, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "keep\n")

    def test_sync_output_path_is_preflighted_before_http(self):
        calls = []
        self.module.worker_request = lambda *args, **kwargs: calls.append(args)

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = self.module.pp_rosetta_metrics(
                output_json="/definitely/missing/result.json"
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(calls, [])

    def test_async_job_keeps_expected_contract_and_preflights_output(self):
        with redirect_stdout(io.StringIO()):
            submitted = self.module.pp_rosetta_metrics_async()
        job = self.module._ASYNC_JOBS[submitted["job_id"]]
        self.assertIn("expected_contract", job)
        self.assertEqual(
            tuple(job["expected_contract"]["requested_metrics"]),
            ("cms", "bunsat_bound_heavy", "bunsat_interface_delta"),
        )
        started = list(_ThreadPool.globalInstance().started)

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rejected = self.module.pp_rosetta_metrics_async(
                output_json="/definitely/missing/result.json"
            )

        self.assertEqual(rejected["status"], "error")
        self.assertEqual(_ThreadPool.globalInstance().started, started)

    def test_rosetta_preflight_rejects_multi_object_and_nonmolecule_selections(self):
        cases = (
            (["left", "right"], {}, "exactly one molecule object"),
            (["map_only"], {"map_only": "object:map"}, "not a molecule"),
        )
        for objects, object_types, message in cases:
            with self.subTest(objects=objects):
                self.cmd.object_list = objects
                self.cmd.object_types = object_types
                started = list(_ThreadPool.globalInstance().started)
                before_exports = list(self.cmd.get_pdbstr_calls)
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    rejected = self.module.pp_rosetta_metrics_async(
                        selection="same_chain_ids"
                    )

                self.assertEqual(rejected["status"], "error")
                self.assertIn(message, rejected["error"])
                self.assertEqual(_ThreadPool.globalInstance().started, started)
                self.assertEqual(self.cmd.get_pdbstr_calls, before_exports)

    def test_rosetta_single_molecule_preflight_happens_before_main_thread_export(self):
        self.cmd.object_list = ["complex"]

        with redirect_stdout(io.StringIO()):
            submitted = self.module.pp_rosetta_metrics_async(selection="complex")

        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(self.cmd.get_object_list_calls[-1], "(complex)")
        self.assertEqual(self.cmd.get_pdbstr_calls[-1], ("(complex)", 1))

    def test_rosetta_preview_validates_without_export_or_worker_access(self):
        before_exports = list(self.cmd.get_pdbstr_calls)
        with patch.object(self.module, "worker_request") as request:
            preview = self.module.preview_rosetta_metrics(
                "complex",
                "A",
                "B",
                "cms,bunsat_bound_heavy",
                timeout=37,
            )
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(preview["source_object"], "complex")
        self.assertEqual(preview["target_chains"], ["A"])
        self.assertFalse(preview["worker_contacted"])
        self.assertFalse(preview["structure_exported"])
        self.assertEqual(self.cmd.get_pdbstr_calls, before_exports)
        request.assert_not_called()

        with self.assertRaisesRegex(ValueError, "duplicates"):
            self.module.preview_rosetta_metrics(
                "complex", "A,A", "B", "cms"
            )

    def test_async_completion_uses_saved_contract_before_output(self):
        _, expected, result = _payload_and_result(self.module)
        result["partners"] = {
            "target_chains": ["B"],
            "binder_chains": ["A"],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "async.json")
            with open(output, "w", encoding="utf-8") as handle:
                handle.write("keep\n")
            job_id = "contract-job"
            self.module._ASYNC_JOBS[job_id] = {
                "kind": "rosetta_metrics",
                "expected_contract": expected,
                "output_json": output,
                "cancelled": False,
            }

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.module._complete_async_job(job_id, result, None)

            with open(output, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "keep\n")
            self.assertNotIn(job_id, self.module._ASYNC_JOBS)


    def test_completed_rosetta_history_keeps_only_bounded_result_summary(self):
        _, _, result = _payload_and_result(self.module)
        with redirect_stdout(io.StringIO()):
            submitted = self.module.pp_rosetta_metrics_async()
            status = self.module._complete_async_job(
                submitted["job_id"], result, None
            )
            jobs = self.module.pp_worker_jobs()

        self.assertEqual(status["status"], "completed")
        self.assertEqual(jobs["active"], {})
        entry = jobs["history"][0]
        self.assertEqual(entry["kind"], "rosetta_metrics")
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["result_summary"]["metrics"], result["metrics"])
        self.assertEqual(entry["result_summary"]["warnings_count"], 0)
        self.assertEqual(entry["request_summary"]["source"], "complex")
        self.assertEqual(entry["request_summary"]["target_chains"], ["A"])
        self.assertEqual(entry["request_summary"]["binder_chains"], ["B"])
        self.assertEqual(
            entry["request_summary"]["metrics"],
            ["cms", "bunsat_bound_heavy", "bunsat_interface_delta"],
        )
        self.assertEqual(entry["output_json"], "")
        serialized = json.dumps(jobs, sort_keys=True)
        self.assertNotIn("ATOM", serialized)
        self.assertNotIn("structure", serialized)

    def test_rosetta_start_failure_moves_job_to_history_without_payload(self):
        pool = _ThreadPool.globalInstance()
        with patch.object(
            pool, "start", side_effect=RuntimeError("synthetic start failure")
        ):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rejected = self.module.pp_rosetta_metrics_async()
                jobs = self.module.pp_worker_jobs()

        self.assertEqual(rejected["status"], "error")
        self.assertEqual(jobs["active"], {})
        self.assertEqual(jobs["history"][0]["status"], "failed")
        self.assertEqual(jobs["history"][0]["kind"], "rosetta_metrics")
        self.assertNotIn("ATOM", json.dumps(jobs, sort_keys=True))

    def test_async_rosetta_submission_does_no_http_or_health_on_gui_thread(self):
        _, _, result = _payload_and_result(self.module)
        with patch.object(
            self.module, "worker_request", return_value=result
        ) as request, patch.object(
            self.module, "pp_worker_health"
        ) as health, patch.object(
            self.module, "pp_worker_capabilities"
        ) as capabilities:
            with redirect_stdout(io.StringIO()):
                submitted = self.module.pp_rosetta_metrics_async(timeout=37)

            request.assert_not_called()
            health.assert_not_called()
            capabilities.assert_not_called()
            runnable = _ThreadPool.globalInstance().started[-1]
            self.assertEqual(runnable.endpoint, "/v1/analyze")
            self.assertEqual(runnable.timeout, 37.0)

            with redirect_stdout(io.StringIO()):
                runnable.run()

        request.assert_called_once()
        self.assertEqual(request.call_args.args[0], "/v1/analyze")
        self.assertEqual(request.call_args.kwargs["timeout"], 37.0)
        with redirect_stdout(io.StringIO()):
            jobs = self.module.pp_worker_jobs()
        self.assertNotIn(submitted["job_id"], jobs["active"])
        self.assertEqual(jobs["history"][0]["status"], "completed")

    def test_atomic_json_failure_preserves_target_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "metrics.json")
            with open(output, "w", encoding="utf-8") as handle:
                handle.write("keep\n")

            for failure_target in (self.module.json, self.module.os):
                method = "dump" if failure_target is self.module.json else "replace"
                with self.subTest(method=method), patch.object(
                    failure_target,
                    method,
                    side_effect=RuntimeError("synthetic atomic failure"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "atomic failure"):
                        self.module._write_json_atomic(output, {"metrics": {"cms": 1.0}})

                with open(output, "r", encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "keep\n")
                self.assertEqual(
                    [name for name in os.listdir(directory) if ".tmp." in name],
                    [],
                )

            written = self.module._write_json_atomic(
                output, {"metrics": {"cms": 2.5}}
            )
            self.assertEqual(written, output)
            with open(output, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["metrics"]["cms"], 2.5)
            self.assertEqual(
                [name for name in os.listdir(directory) if ".tmp." in name], []
            )


class RemotePlipOrderingTests(unittest.TestCase):
    def setUp(self):
        _ThreadPool.instance = None
        self.module, self.cmd = _load_commands_module()
        self.controller = _PlipCommitController()
        self.controller_patch = patch(
            "pymolplugins.interactions.controller.get_controller",
            return_value=self.controller,
        )
        self.controller_patch.start()

    def tearDown(self):
        self.controller_patch.stop()

    def submit(self, label, group):
        return self.module.submit_plip_interaction(
            _plip_submission(label, group)
        )["job_id"]

    def test_newer_same_group_completion_wins_when_results_arrive_out_of_order(self):
        first = self.submit("A", "ppi_same")
        second = self.submit("B", "ppi_same")

        second_status = self.module._complete_async_job(
            second, {"payload": "B"}, None
        )
        first_status = self.module._complete_async_job(
            first, {"payload": "A"}, None
        )

        self.assertEqual([item[0] for item in self.controller.commits], ["B"])
        self.assertEqual(second_status["status"], "completed")
        self.assertEqual(first_status["status"], "stale")
        with redirect_stdout(io.StringIO()):
            history = self.module.pp_worker_jobs()["history"]
        self.assertEqual(
            [entry["status"] for entry in history], ["stale", "completed"]
        )

    def test_different_groups_have_independent_epochs(self):
        first = self.submit("A", "ppi_left")
        second = self.submit("B", "ppi_right")

        self.module._complete_async_job(second, {"payload": "B"}, None)
        self.module._complete_async_job(first, {"payload": "A"}, None)

        self.assertEqual(
            [item[0] for item in self.controller.commits], ["B", "A"]
        )

    def test_cancelled_old_job_cannot_remove_or_overwrite_new_reservation(self):
        first = self.submit("A", "ppi_cancel")
        self.module.pp_worker_cancel(first)
        second = self.submit("B", "ppi_cancel")

        cancelled = self.module._complete_async_job(
            first, {"payload": "A"}, None
        )
        current = self.module._complete_async_job(
            second, {"payload": "B"}, None
        )

        self.assertEqual([item[0] for item in self.controller.commits], ["B"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(current["status"], "completed")
        with redirect_stdout(io.StringIO()):
            history = self.module.pp_worker_jobs()["history"]
        self.assertEqual(
            [entry["status"] for entry in history], ["completed", "cancelled"]
        )

    def test_failed_old_job_does_not_release_newer_group_reservation(self):
        first = self.submit("A", "ppi_failure")
        second = self.submit("B", "ppi_failure")

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            failed = self.module._complete_async_job(
                first, None, RuntimeError("synthetic transport failure")
            )
        reservation = self.module._PLIP_GROUP_RESERVATIONS["ppi_failure"]
        current = self.module._complete_async_job(
            second, {"payload": "B"}, None
        )

        self.assertEqual(failed["status"], "failed")
        self.assertEqual(reservation["job_id"], second)
        self.assertEqual(current["status"], "completed")
        self.assertEqual([item[0] for item in self.controller.commits], ["B"])
        with redirect_stdout(io.StringIO()):
            history = self.module.pp_worker_jobs()["history"]
        self.assertEqual(
            [entry["status"] for entry in history], ["completed", "failed"]
        )

    def test_source_stale_completion_releases_only_its_own_reservation(self):
        self.controller.stale_runs.add("A")
        first = self.submit("A", "ppi_source_stale")

        stale = self.module._complete_async_job(
            first, {"payload": "A"}, None
        )
        self.assertEqual(stale["status"], "stale")
        self.assertNotIn("ppi_source_stale", self.module._PLIP_GROUP_RESERVATIONS)

        second = self.submit("B", "ppi_source_stale")
        current = self.module._complete_async_job(
            second, {"payload": "B"}, None
        )

        self.assertEqual(current["status"], "completed")
        self.assertEqual([item[0] for item in self.controller.commits], ["B"])

    def test_start_failure_cleans_the_job_and_only_its_reservation(self):
        pool = _ThreadPool.globalInstance()
        with patch.object(
            pool, "start", side_effect=RuntimeError("synthetic start failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic start failure"):
                self.submit("A", "ppi_start_failure")

        self.assertEqual(self.module._ASYNC_JOBS, {})
        self.assertNotIn("ppi_start_failure", self.module._PLIP_GROUP_RESERVATIONS)

        current = self.submit("B", "ppi_start_failure")
        status = self.module._complete_async_job(
            current, {"payload": "B"}, None
        )
        self.assertEqual(status["status"], "completed")


class RemoteWorkerJobHistoryTests(unittest.TestCase):
    def setUp(self):
        _ThreadPool.instance = None
        self.module, self.cmd = _load_commands_module()
        self.controller = _PlipCommitController()
        self.controller_patch = patch(
            "pymolplugins.interactions.controller.get_controller",
            return_value=self.controller,
        )
        self.controller_patch.start()

    def tearDown(self):
        self.controller_patch.stop()

    def submit(self, label, group="ppi_history"):
        with redirect_stdout(io.StringIO()):
            return self.module.submit_plip_interaction(
                _plip_submission(label, group)
            )["job_id"]

    def jobs(self):
        with redirect_stdout(io.StringIO()):
            return self.module.pp_worker_jobs()

    def test_jobs_separates_active_and_history_and_returns_defensive_copies(self):
        job_id = self.submit("history-A")
        active = self.jobs()

        self.assertEqual(set(active), {"active", "history"})
        self.assertEqual(active["history"], [])
        self.assertEqual(active["active"][job_id]["status"], "running")
        self.assertEqual(
            active["active"][job_id]["request_summary"]["source"],
            "complex",
        )
        self.assertRegex(active["active"][job_id]["submitted_at"], r"Z$")
        active_json = json.dumps(active, sort_keys=True)
        self.assertNotIn("submission", active_json)
        self.assertNotIn("runnable", active_json)
        active["active"][job_id]["group"] = "mutated"
        self.assertEqual(self.jobs()["active"][job_id]["group"], "ppi_history")

        sensitive_payload = {
            "structure": {"text": "ATOM SECRET-PDB-TEXT"},
            "payload": "large-result-body",
        }
        with redirect_stdout(io.StringIO()):
            completed = self.module._complete_async_job(
                job_id, sensitive_payload, None
            )
        jobs = self.jobs()

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(jobs["active"], {})
        entry = jobs["history"][0]
        self.assertEqual(entry["job_id"], job_id)
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["group"], "ppi_history")
        self.assertEqual(entry["request_summary"]["run_id"], "history-A")
        self.assertEqual(entry["request_summary"]["source"], "complex")
        self.assertEqual(
            entry["result_summary"]["counts_by_type"],
            {"hydrogen_bond": 1},
        )
        self.assertRegex(entry["finished_at"], r"Z$")
        self.assertIsNone(entry["error"])
        serialized = json.dumps(jobs, sort_keys=True)
        self.assertNotIn("SECRET-PDB-TEXT", serialized)
        self.assertNotIn("large-result-body", serialized)

        entry["result_summary"]["counts_by_type"]["hydrogen_bond"] = 999
        self.assertEqual(
            self.jobs()["history"][0]["result_summary"]["counts_by_type"],
            {"hydrogen_bond": 1},
        )

    def test_async_plip_submission_only_runs_http_inside_the_runnable(self):
        submission = _plip_submission("async-plip", "ppi_async_plip")
        with patch.object(
            self.module,
            "worker_request",
            return_value={"payload": "remote-result"},
        ) as request, patch.object(
            self.module, "pp_worker_health"
        ) as health, patch.object(
            self.module, "pp_worker_capabilities"
        ) as capabilities:
            with redirect_stdout(io.StringIO()):
                submitted = self.module.submit_plip_interaction(
                    submission, timeout=29
                )

            request.assert_not_called()
            health.assert_not_called()
            capabilities.assert_not_called()
            runnable = _ThreadPool.globalInstance().started[-1]
            self.assertEqual(runnable.endpoint, "/v1/interactions/plip")
            self.assertEqual(runnable.timeout, 29.0)

            with redirect_stdout(io.StringIO()):
                runnable.run()

        request.assert_called_once()
        self.assertEqual(request.call_args.args[0], "/v1/interactions/plip")
        self.assertEqual(request.call_args.kwargs["timeout"], 29.0)
        jobs = self.jobs()
        self.assertNotIn(submitted["job_id"], jobs["active"])
        self.assertEqual(jobs["history"][0]["status"], "completed")

    def test_invalid_plip_timeout_never_reserves_or_starts_a_job(self):
        submission = _plip_submission("invalid-timeout", "ppi_invalid_timeout")
        for timeout in (False, 0, float("nan"), float("inf")):
            with self.subTest(timeout=timeout):
                started = list(_ThreadPool.globalInstance().started)
                with self.assertRaisesRegex(ValueError, "timeout"):
                    self.module.submit_plip_interaction(
                        submission, timeout=timeout
                    )
                self.assertEqual(_ThreadPool.globalInstance().started, started)
                self.assertEqual(self.module._ASYNC_JOBS, {})
                self.assertNotIn(
                    "ppi_invalid_timeout",
                    self.module._PLIP_GROUP_RESERVATIONS,
                )

    def test_cancel_and_timeout_remain_inspectable_with_actionable_state(self):
        cancelled_id = self.submit("cancel-visible", "ppi_cancel_visible")
        with redirect_stdout(io.StringIO()):
            cancel_result = self.module.pp_worker_cancel(cancelled_id)
        active = self.jobs()

        self.assertEqual(cancel_result["status"], "cancel_requested")
        self.assertEqual(
            active["active"][cancelled_id]["status"], "cancel_requested"
        )
        self.assertEqual(active["history"], [])

        with redirect_stdout(io.StringIO()):
            self.module._complete_async_job(cancelled_id, None, None)
        self.assertEqual(self.jobs()["history"][0]["status"], "cancelled")

        timeout_id = self.submit("timeout-visible", "ppi_timeout_visible")
        timeout = WorkerRequestError(
            "worker request timed out after 3 seconds",
            code="timeout",
            endpoint="/v1/interactions/plip",
            retriable=True,
        )
        with redirect_stdout(io.StringIO()):
            failed = self.module._complete_async_job(timeout_id, None, timeout)
        timeout_entry = self.jobs()["history"][0]

        self.assertEqual(failed["status"], "failed")
        self.assertEqual(timeout_entry["error"]["code"], "timeout")
        self.assertTrue(timeout_entry["error"]["retriable"])
        self.assertIn("increase timeout", timeout_entry["error"]["action"])

    def test_terminal_statuses_are_retained_and_history_is_bounded(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            for index in range(105):
                job_id = "cancel-%03d" % index
                self.module._ASYNC_JOBS[job_id] = {
                    "kind": "rosetta_metrics",
                    "cancelled": True,
                    "submitted_at": "2026-01-01T00:00:00Z",
                }
                self.module._complete_async_job(job_id, None, None)

        history = self.jobs()["history"]
        self.assertEqual(len(history), 100)
        self.assertEqual(history[0]["job_id"], "cancel-104")
        self.assertEqual(history[-1]["job_id"], "cancel-005")
        self.assertTrue(all(entry["status"] == "cancelled" for entry in history))

    def test_history_survives_module_reload_without_exposing_its_mutable_store(self):
        job_id = self.submit("reload-A")
        with redirect_stdout(io.StringIO()):
            self.module._complete_async_job(job_id, {"payload": "A"}, None)
        store = self.module._WORKER_JOB_HISTORY

        reloaded, _ = _load_commands_module(self.module, self.cmd)

        self.assertIs(reloaded._WORKER_JOB_HISTORY, store)
        jobs = self.jobs()
        self.assertEqual(jobs["history"][0]["job_id"], job_id)
        jobs["history"].clear()
        self.assertEqual(len(self.module._WORKER_JOB_HISTORY), 1)

    def test_expected_worker_failure_is_actionable_without_traceback(self):
        job_id = "backend-unavailable"
        self.module._ASYNC_JOBS[job_id] = {
            "kind": "rosetta_metrics",
            "cancelled": False,
            "submitted_at": "2026-01-01T00:00:00Z",
        }
        error = WorkerRequestError(
            "PyRosetta is unavailable: not installed",
            code="backend_error",
            endpoint="/v1/analyze",
            http_status=422,
        )

        with patch.object(self.module.traceback, "print_exception") as trace:
            with redirect_stdout(io.StringIO()) as stdout:
                status = self.module._complete_async_job(job_id, None, error)

        self.assertEqual(status["status"], "failed")
        trace.assert_not_called()
        self.assertIn("pp_worker_health", stdout.getvalue())
        history_error = self.jobs()["history"][0]["error"]
        self.assertEqual(history_error["code"], "backend_error")
        self.assertIn("pp_worker_health", history_error["action"])

    def test_plip_unavailable_action_names_an_explicit_local_fallback(self):
        error = WorkerRequestError(
            "PLIP backend unavailable: not installed",
            code="backend_unavailable",
            endpoint="/v1/interactions/plip",
            http_status=422,
        )

        action = self.module._worker_error_action(error)

        self.assertIn("pp_worker_health", action)
        self.assertIn("pp_contacts", action)
        self.assertIn("ligand_contacts", action)

        job_id = self.submit("plip-unavailable", "ppi_plip_unavailable")
        with redirect_stdout(io.StringIO()):
            failed = self.module._complete_async_job(job_id, None, error)
        history_error = self.jobs()["history"][0]["error"]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(history_error["code"], "backend_unavailable")
        self.assertIn("pp_contacts", history_error["action"])

    def test_internal_failure_traces_but_does_not_persist_exception_payload(self):
        job_id = "internal-sensitive"
        self.module._ASYNC_JOBS[job_id] = {
            "kind": "rosetta_metrics",
            "cancelled": False,
            "submitted_at": "2026-01-01T00:00:00Z",
        }
        error = RuntimeError("ATOM SECRET-INTERNAL-PDB-TEXT")

        with patch.object(self.module.traceback, "print_exception") as trace:
            with redirect_stdout(io.StringIO()):
                status = self.module._complete_async_job(job_id, None, error)

        self.assertEqual(status["status"], "failed")
        trace.assert_called_once()
        history_error = self.jobs()["history"][0]["error"]
        self.assertEqual(history_error["code"], "internal_error")
        self.assertNotIn("SECRET-INTERNAL-PDB-TEXT", json.dumps(history_error))

    def test_unknown_cancel_points_to_the_existing_jobs_command(self):
        with redirect_stdout(io.StringIO()) as stdout:
            result = self.module.pp_worker_cancel("missing-job")

        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["hint"], "run pp_worker_jobs")
        self.assertIn("pp_worker_jobs", stdout.getvalue())

    def test_sync_input_error_is_concise_without_traceback(self):
        with patch.object(self.module.traceback, "print_exc") as trace:
            with redirect_stdout(io.StringIO()) as stdout:
                result = self.module.pp_rosetta_metrics(metrics="")

        self.assertEqual(result["status"], "error")
        self.assertIn("Input error", stdout.getvalue())
        trace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
