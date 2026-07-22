"""Windows Host PyMOL client for the bounded WSL computation worker."""

import json
import os
import sys
import traceback
from typing import Dict, Mapping, Optional
import uuid

from pymol import cmd

try:
    from pymol.Qt import QtCore
except ImportError:
    QtCore = None  # type: ignore

try:
    from pymolplugins.remote.client import (
        DEFAULT_WORKER_URL,
        WorkerRequestError,
        worker_request,
    )
    from pymolplugins.core.parameters import as_float, as_int, extend_command, split_names
    from pymolplugins.core.windows_paths import windows_long_path
except ImportError:
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from pymolplugins.remote.client import (  # type: ignore
        DEFAULT_WORKER_URL,
        WorkerRequestError,
        worker_request,
    )
    from pymolplugins.core.parameters import (  # type: ignore
        as_float,
        as_int,
        extend_command,
        split_names,
    )
    from pymolplugins.core.windows_paths import windows_long_path  # type: ignore


ROSETTA_SCHEMA = "pymolplugins.rosetta_metrics/1.0"


def _error_result(exc: Exception) -> Dict[str, object]:
    if isinstance(exc, WorkerRequestError):
        detail = exc.to_dict()
    else:
        detail = {
            "code": "client_error",
            "message": str(exc),
            "http_status": None,
            "retriable": False,
        }
    return {"status": "error", "error": str(exc), "error_detail": detail}


def pp_worker_health(url: str = "", timeout: object = 3.0) -> Dict[str, object]:
    try:
        result = worker_request(
            "/v1/health",
            url=url,
            timeout=as_float(timeout, minimum=0.1, name="timeout"),
            expected_schema="pymolplugins.worker/1.0",
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return result
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return _error_result(exc)


def pp_worker_capabilities(url: str = "", timeout: object = 3.0) -> Dict[str, object]:
    try:
        result = worker_request(
            "/v1/capabilities",
            url=url,
            timeout=as_float(timeout, minimum=0.1, name="timeout"),
            expected_schema="pymolplugins.worker/1.0",
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return result
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return _error_result(exc)


def _export_pdb(selection: str, state: int) -> str:
    previous = {}
    settings = {"pdb_conect_all": 1, "pdb_conect_nodup": 1}
    try:
        for name, value in settings.items():
            previous[name] = cmd.get_setting_text(name)
            cmd.set(name, value)
        text = cmd.get_pdbstr(f"({selection})", state=state)
        if not text or not text.strip():
            raise ValueError(f"selection produced no PDB text: {selection}")
        return text
    finally:
        for name, value in previous.items():
            try:
                cmd.set(name, value)
            except Exception:
                traceback.print_exc()


def _rosetta_payload(
    selection: str,
    target_chains: object,
    binder_chains: object,
    metrics: object,
    state: int,
) -> Dict[str, object]:
    requested = split_names(metrics)
    if not requested:
        raise ValueError("metrics must contain at least one metric name")
    targets = split_names(target_chains)
    binders = split_names(binder_chains)
    if not targets or not binders:
        raise ValueError("target_chains and binder_chains must both be non-empty")
    return {
        "schema_version": 1,
        "structure": {"format": "pdb", "text": _export_pdb(selection, state)},
        "partners": {"target_chains": targets, "binder_chains": binders},
        "metrics": requested,
        "options": {},
    }


def _write_json_atomic(filename: str, payload: Mapping[str, object]) -> str:
    target = os.path.abspath(os.path.expanduser(str(filename).strip()))
    if not filename or os.path.isdir(windows_long_path(target)):
        raise ValueError("output_json must identify a JSON file")
    parent = os.path.dirname(target) or os.getcwd()
    if not os.path.isdir(windows_long_path(parent)):
        raise ValueError(f"output directory does not exist: {parent}")
    temporary = target + ".tmp." + uuid.uuid4().hex[:8]
    try:
        with open(windows_long_path(temporary), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(windows_long_path(temporary), windows_long_path(target))
    finally:
        if os.path.exists(windows_long_path(temporary)):
            os.unlink(windows_long_path(temporary))
    return target


def _finish_rosetta_result(
    result: Dict[str, object], output_json: str = ""
) -> Dict[str, object]:
    if result.get("schema") != ROSETTA_SCHEMA:
        raise WorkerRequestError(
            f"unexpected result schema: {result.get('schema')!r}",
            code="schema_mismatch",
            endpoint="/v1/analyze",
            detail={"expected_schema": ROSETTA_SCHEMA, "actual_schema": result.get("schema")},
        )
    if output_json:
        result = dict(result)
        result["output_json"] = _write_json_atomic(output_json, result)
    print(json.dumps(result.get("metrics", {}), indent=2, sort_keys=True))
    return result


def pp_rosetta_metrics(
    selection="all",
    target_chains="A",
    binder_chains="B",
    metrics="cms,bunsat_bound_heavy,bunsat_interface_delta",
    state=1,
    output_json="",
    url="",
    timeout=120.0,
) -> Dict[str, object]:
    """Synchronous console/batch request; use the async command in the GUI."""
    try:
        parsed_state = as_int(state, minimum=1, name="state")
        payload = _rosetta_payload(
            str(selection), target_chains, binder_chains, metrics, parsed_state
        )
        result = worker_request(
            "/v1/analyze",
            payload,
            url=url,
            timeout=as_float(timeout, minimum=0.1, name="timeout"),
            expected_schema=ROSETTA_SCHEMA,
        )
        return _finish_rosetta_result(result, str(output_json))
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return _error_result(exc)


_ASYNC_JOBS: Dict[str, Dict[str, object]] = globals().get("_ASYNC_JOBS", {})
_ASYNC_BRIDGE = globals().get("_ASYNC_BRIDGE", None)


if QtCore is not None:
    class _WorkerBridge(QtCore.QObject):
        completed = QtCore.Signal(str, object, object)

        def __init__(self):
            super().__init__()
            self.completed.connect(
                self._dispatch, QtCore.Qt.ConnectionType.QueuedConnection
                if hasattr(QtCore.Qt, "ConnectionType")
                else QtCore.Qt.QueuedConnection,
            )

        @QtCore.Slot(str, object, object)
        def _dispatch(self, job_id, result, error):
            _complete_async_job(job_id, result, error)


    class _WorkerRunnable(QtCore.QRunnable):
        def __init__(self, bridge, job_id, endpoint, payload, url, timeout, schema):
            super().__init__()
            self.bridge = bridge
            self.job_id = job_id
            self.endpoint = endpoint
            self.payload = payload
            self.url = url
            self.timeout = timeout
            self.schema = schema

        @QtCore.Slot()
        def run(self):
            try:
                result = worker_request(
                    self.endpoint,
                    self.payload,
                    url=self.url,
                    timeout=self.timeout,
                    expected_schema=self.schema,
                )
                self.bridge.completed.emit(self.job_id, result, None)
            except Exception as exc:
                self.bridge.completed.emit(self.job_id, None, exc)


def _bridge():
    global _ASYNC_BRIDGE
    if QtCore is None:
        raise RuntimeError("PyMOL Qt is unavailable; asynchronous requests cannot start")
    if QtCore.QCoreApplication.instance() is None:
        raise RuntimeError("no Qt application/event loop is active")
    if _ASYNC_BRIDGE is None:
        _ASYNC_BRIDGE = _WorkerBridge()
    return _ASYNC_BRIDGE


def _complete_async_job(job_id: str, result, error) -> None:
    job = _ASYNC_JOBS.pop(job_id, None)
    if job is None:
        return
    if job.get("cancelled"):
        print(
            f"Worker job {job_id} result suppressed after cancel request; "
            "the backend calculation may still have run to completion"
        )
        return
    if error is not None:
        print(f"Worker job {job_id} failed: {error}")
        traceback.print_exception(type(error), error, error.__traceback__)
        return
    try:
        if job.get("kind") == "plip_interaction":
            from pymolplugins.interactions.controller import get_controller

            record = get_controller(cmd).commit_remote_result(
                job["submission"], result
            )
            print(
                f"Worker job {job_id} completed: interaction group="
                f"{record.manifest.root_group}"
            )
        else:
            _finish_rosetta_result(result, str(job.get("output_json", "")))
            print(f"Worker job {job_id} completed")
    except Exception as exc:
        print(f"Worker job {job_id} result handling failed: {exc}")
        traceback.print_exc()


def pp_rosetta_metrics_async(
    selection="all",
    target_chains="A",
    binder_chains="B",
    metrics="cms,bunsat_bound_heavy,bunsat_interface_delta",
    state=1,
    output_json="",
    url="",
    timeout=120.0,
) -> Dict[str, object]:
    """Export on the main thread, then perform only HTTP work off-thread."""
    try:
        parsed_state = as_int(state, minimum=1, name="state")
        payload = _rosetta_payload(
            str(selection), target_chains, binder_chains, metrics, parsed_state
        )
        parsed_timeout = as_float(timeout, minimum=0.1, name="timeout")
        bridge = _bridge()
        job_id = uuid.uuid4().hex[:12]
        _ASYNC_JOBS[job_id] = {
            "kind": "rosetta_metrics",
            "output_json": str(output_json),
            "cancelled": False,
        }
        runnable = _WorkerRunnable(
            bridge,
            job_id,
            "/v1/analyze",
            payload,
            str(url),
            parsed_timeout,
            ROSETTA_SCHEMA,
        )
        _ASYNC_JOBS[job_id]["runnable"] = runnable
        QtCore.QThreadPool.globalInstance().start(runnable)
        print(
            f"Worker job {job_id} submitted; cancel suppresses result handling "
            "but cannot interrupt an in-progress PyRosetta C++ call"
        )
        return {"status": "submitted", "job_id": job_id}
    except Exception as exc:
        print(f"Error: {exc}")
        traceback.print_exc()
        return _error_result(exc)


def submit_plip_interaction(submission, url: str = "", timeout: float = 120.0):
    """Submit an already-exported PLIP request without calling cmd off-thread."""

    bridge = _bridge()
    job_id = uuid.uuid4().hex[:12]
    _ASYNC_JOBS[job_id] = {
        "kind": "plip_interaction",
        "submission": submission,
        "preset": submission.preset,
        "group": submission.group,
        "cancelled": False,
    }
    runnable = _WorkerRunnable(
        bridge,
        job_id,
        "/v1/interactions/plip",
        submission.request,
        str(url),
        float(timeout),
        "pymolplugins.interactions/1.0",
    )
    _ASYNC_JOBS[job_id]["runnable"] = runnable
    QtCore.QThreadPool.globalInstance().start(runnable)
    print(
        f"Worker job {job_id} submitted for {submission.preset}; "
        "cancel suppresses rendering but does not interrupt backend work"
    )
    return {
        "status": "submitted",
        "job_id": job_id,
        "run_id": submission.request["run_id"],
        "preset": submission.preset,
        "requested_group": submission.group,
    }


def pp_worker_cancel(job_id: str) -> Dict[str, object]:
    value = str(job_id).strip()
    job = _ASYNC_JOBS.get(value)
    if job is None:
        return {"status": "unknown", "job_id": value}
    job["cancelled"] = True
    print(
        f"Worker job {value}: result suppression requested; network/backend work "
        "is not forcibly interrupted"
    )
    return {"status": "cancel_requested", "job_id": value}


def pp_worker_jobs() -> Dict[str, object]:
    result = {
        job_id: {
            "kind": job.get("kind"),
            "cancelled": bool(job.get("cancelled")),
            "preset": job.get("preset"),
            "group": job.get("group"),
        }
        for job_id, job in _ASYNC_JOBS.items()
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


extend_command(cmd, "pp_worker_health", pp_worker_health)
extend_command(cmd, "pp_worker_capabilities", pp_worker_capabilities)
extend_command(cmd, "pp_rosetta_metrics", pp_rosetta_metrics)
extend_command(cmd, "pp_rosetta_metrics_async", pp_rosetta_metrics_async)
extend_command(cmd, "pp_worker_cancel", pp_worker_cancel)
extend_command(cmd, "pp_worker_jobs", pp_worker_jobs)
