"""Windows Host PyMOL client for the bounded WSL computation worker."""

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
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
    from pymolplugins.core.parameters import (
        as_bool,
        as_float,
        as_int,
        extend_command,
        split_names,
    )
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
        as_bool,
        as_float,
        as_int,
        extend_command,
        split_names,
    )
    from pymolplugins.core.windows_paths import windows_long_path  # type: ignore


ROSETTA_SCHEMA = "pymolplugins.rosetta_metrics/1.0"
ROSETTA_INTEGER_METRICS = frozenset(
    {"bunsat_bound_heavy", "bunsat_interface_delta"}
)
_EXPECTED_RUNTIME_ERRORS = (
    "PyMOL Qt is unavailable",
    "no Qt application/event loop is active",
)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _worker_error_action(exc: Exception) -> str:
    if isinstance(exc, WorkerRequestError):
        code = exc.code
        message = str(exc).lower()
        if code in {"backend_error", "backend_unavailable"}:
            backend = (
                "PyRosetta"
                if "pyrosetta" in message
                else "PLIP"
                if "plip" in message
                else "the requested backend"
            )
            if "unavailable" in message or code == "backend_unavailable":
                action = (
                    f"Run pp_worker_health; install or activate {backend} in the "
                    "WSL worker environment, restart the worker, and retry."
                )
                if backend == "PLIP":
                    action += (
                        " For an explicitly local fallback, run pp_contacts "
                        "SOURCE, focus=LIGAND or pp_interact_run ligand_contacts."
                    )
                return action
            return (
                "Check the selection, chains, metrics, and worker health with "
                "pp_worker_health before retrying."
            )
        if code == "unreachable":
            return (
                "Start the WSL worker, verify PYMOLPLUGINS_WORKER_URL, then run "
                "pp_worker_health."
            )
        if code == "timeout":
            return "Run pp_worker_health, then retry or increase timeout."
        if code == "scientific_contract_error":
            return (
                "Do not use this result. Run pp_worker_capabilities and report the "
                "backend/version with the job history entry."
            )
        if code == "schema_mismatch":
            return (
                "Restart a worker version compatible with this plugin, then run "
                "pp_worker_capabilities."
            )
        if code in {
            "invalid_request",
            "request_too_large",
            "unsupported_media_type",
        }:
            return (
                "Check the command arguments and selections; run "
                "pp_worker_capabilities for worker limits."
            )
        return "Run pp_worker_health and pp_worker_capabilities, then retry."
    if isinstance(exc, ValueError):
        return "Check the command arguments and PyMOL selections."
    if isinstance(exc, RuntimeError) and str(exc).startswith(_EXPECTED_RUNTIME_ERRORS):
        return "Run this asynchronous command inside the PyMOL Qt application."
    return ""


def _is_expected_worker_error(exc: Exception) -> bool:
    return bool(
        isinstance(exc, (WorkerRequestError, ValueError))
        or (
            isinstance(exc, RuntimeError)
            and str(exc).startswith(_EXPECTED_RUNTIME_ERRORS)
        )
    )


def _report_worker_error(
    exc: Exception, *, context: str = "", current_exception: bool = True
) -> None:
    if isinstance(exc, WorkerRequestError):
        label = "Worker error [%s]" % exc.code
    elif isinstance(exc, ValueError):
        label = "Input error"
    elif _is_expected_worker_error(exc):
        label = "Worker error"
    else:
        label = "Internal error"
    prefix = (str(context).strip() + " ") if context else ""
    print(f"{prefix}{label}: {exc}")
    action = _worker_error_action(exc)
    if action:
        print(f"Action: {action}")
    if not _is_expected_worker_error(exc):
        if current_exception:
            traceback.print_exc()
        else:
            traceback.print_exception(type(exc), exc, exc.__traceback__)


def _scientific_contract_error(message: str, field: str) -> WorkerRequestError:
    return WorkerRequestError(
        message,
        code="scientific_contract_error",
        endpoint="/v1/analyze",
        retriable=False,
        detail={"field": field},
    )


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
    result = {"status": "error", "error": str(exc), "error_detail": detail}
    action = _worker_error_action(exc)
    if action:
        result["action"] = action
    return result


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
        _report_worker_error(exc)
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
        _report_worker_error(exc)
        return _error_result(exc)


def _selection_molecule_object(selection: str) -> Optional[str]:
    value = str(selection).strip()
    if not value:
        raise ValueError("selection must identify exactly one molecule object")
    get_object_list = getattr(cmd, "get_object_list", None)
    if not callable(get_object_list):
        return None
    try:
        raw_names = get_object_list(f"({value})")
    except Exception as exc:
        raise ValueError(
            "could not resolve selection to one molecule object: %s" % exc
        ) from exc
    if isinstance(raw_names, str):
        raw_names = (raw_names,)
    try:
        names = tuple(dict.fromkeys(str(name) for name in raw_names if str(name)))
    except TypeError as exc:
        raise ValueError("PyMOL returned an invalid object list for selection") from exc
    if len(names) != 1:
        raise ValueError(
            "selection must resolve to exactly one molecule object; got %d: %s"
            % (len(names), ", ".join(names) or "<none>")
        )
    get_type = getattr(cmd, "get_type", None)
    if callable(get_type):
        try:
            object_type = str(get_type(names[0]))
        except Exception as exc:
            raise ValueError(
                "could not inspect selection object %s: %s" % (names[0], exc)
            ) from exc
        if object_type != "object:molecule":
            raise ValueError(
                "selection object %s is not a molecule (%s)"
                % (names[0], object_type or "unknown type")
            )
    return names[0]


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
    source_summary: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    selection_value = str(selection).strip()
    source_object = _selection_molecule_object(selection_value)
    if source_summary is not None:
        source_summary.update(
            {
                "selection": selection_value,
                "source_object": source_object,
            }
        )
    requested = split_names(metrics)
    if not requested:
        raise ValueError("metrics must contain at least one metric name")
    if len(set(requested)) != len(requested):
        raise ValueError("metrics must not contain duplicates")
    targets = split_names(target_chains)
    binders = split_names(binder_chains)
    if not targets or not binders:
        raise ValueError("target_chains and binder_chains must both be non-empty")
    if len(set(targets)) != len(targets) or len(set(binders)) != len(binders):
        raise ValueError("target_chains and binder_chains must not contain duplicates")
    overlap = sorted(set(targets).intersection(binders))
    if overlap:
        raise ValueError("target and binder chains overlap: %s" % ", ".join(overlap))
    return {
        "schema_version": 1,
        "structure": {
            "format": "pdb",
            "text": _export_pdb(selection_value, state),
        },
        "partners": {"target_chains": targets, "binder_chains": binders},
        "metrics": requested,
        "options": {},
    }


def _expected_rosetta_contract(
    payload: Mapping[str, object],
) -> Dict[str, object]:
    structure = payload["structure"]
    partners = payload["partners"]
    if not isinstance(structure, Mapping) or not isinstance(partners, Mapping):
        raise ValueError("internal Rosetta payload is malformed")
    text = structure.get("text")
    target_chains = partners.get("target_chains")
    binder_chains = partners.get("binder_chains")
    metrics = payload.get("metrics")
    options = payload.get("options", {})
    if not isinstance(text, str):
        raise ValueError("internal Rosetta structure text is malformed")
    if not isinstance(target_chains, list) or not isinstance(binder_chains, list):
        raise ValueError("internal Rosetta partner contract is malformed")
    if not isinstance(metrics, list) or not all(
        isinstance(metric, str) and metric for metric in metrics
    ):
        raise ValueError("internal Rosetta metric contract is malformed")
    if not isinstance(options, Mapping):
        raise ValueError("internal Rosetta option contract is malformed")
    return {
        "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "target_chains": tuple(target_chains),
        "binder_chains": tuple(binder_chains),
        "requested_metrics": tuple(metrics),
        "options": dict(options),
    }


def _preflight_json_output(filename: str) -> str:
    raw = str(filename).strip()
    if not raw:
        return ""
    target = os.path.abspath(os.path.expanduser(raw))
    long_target = windows_long_path(target)
    if os.path.exists(long_target):
        if not os.path.isfile(long_target):
            raise ValueError("output_json must identify a regular JSON file")
        if not os.access(long_target, os.W_OK):
            raise ValueError(f"output_json is not writable: {target}")
    parent = os.path.dirname(target) or os.getcwd()
    long_parent = windows_long_path(parent)
    if not os.path.isdir(long_parent):
        raise ValueError(f"output directory does not exist: {parent}")
    if not os.access(long_parent, os.W_OK):
        raise ValueError(f"output directory is not writable: {parent}")
    return target


def preview_rosetta_metrics(
    selection: object,
    target_chains: object,
    binder_chains: object,
    metrics: object,
    state: object = 1,
    output_json: object = "",
    url: object = "",
    timeout: object = 120.0,
) -> Dict[str, object]:
    """Validate a Rosetta request plan without exporting PDB or contacting WSL."""

    selection_value = str(selection).strip()
    parsed_state = as_int(state, minimum=1, name="state")
    timeout_value = as_float(timeout, minimum=0.1, name="timeout")
    source_object = _selection_molecule_object(selection_value)
    targets = split_names(target_chains)
    binders = split_names(binder_chains)
    requested = split_names(metrics)
    if not targets or not binders:
        raise ValueError("target_chains and binder_chains must both be non-empty")
    if len(set(targets)) != len(targets) or len(set(binders)) != len(binders):
        raise ValueError("target_chains and binder_chains must not contain duplicates")
    overlap = sorted(set(targets).intersection(binders))
    if overlap:
        raise ValueError("target and binder chains overlap: %s" % ", ".join(overlap))
    if not requested:
        raise ValueError("metrics must contain at least one metric name")
    if len(set(requested)) != len(requested):
        raise ValueError("metrics must not contain duplicates")
    return {
        "schema": "pymolplugins.rosetta_metrics_preview/1.0",
        "status": "preview",
        "scene_mutated": False,
        "worker_contacted": False,
        "structure_exported": False,
        "selection": selection_value,
        "source_object": source_object,
        "state": parsed_state,
        "target_chains": targets,
        "binder_chains": binders,
        "metrics": requested,
        "output_json": _preflight_json_output(str(output_json)),
        "url": str(url),
        "timeout": timeout_value,
    }


def _write_json_atomic(filename: str, payload: Mapping[str, object]) -> str:
    target = _preflight_json_output(filename)
    if not target:
        raise ValueError("output_json must identify a JSON file")
    temporary = target + ".tmp." + uuid.uuid4().hex[:8]
    try:
        with open(windows_long_path(temporary), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        os.replace(windows_long_path(temporary), windows_long_path(target))
    finally:
        if os.path.exists(windows_long_path(temporary)):
            os.unlink(windows_long_path(temporary))
    return target


def _contract_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _scientific_contract_error(
            f"Rosetta result {field} must be an object", field
        )
    return value


def _contract_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _scientific_contract_error(
            f"Rosetta result {field} must be a non-empty string", field
        )
    return value


def _contract_chains(
    value: object, field: str, *, allow_blank: bool = False
) -> tuple:
    if not isinstance(value, list) or not all(
        isinstance(chain, str) and (allow_blank or bool(chain)) for chain in value
    ):
        raise _scientific_contract_error(
            f"Rosetta result {field} must be an array of chain strings", field
        )
    return tuple(value)


def _validated_expected_contract(expected: Mapping[str, object]) -> tuple:
    expected_hash = expected.get("input_sha256")
    expected_targets = tuple(expected.get("target_chains", ()))
    expected_binders = tuple(expected.get("binder_chains", ()))
    requested = tuple(expected.get("requested_metrics", ()))
    expected_options = expected.get("options")
    valid_hash = (
        isinstance(expected_hash, str)
        and len(expected_hash) == 64
        and all(
            character in "0123456789abcdef" for character in expected_hash
        )
    )
    valid_targets = bool(expected_targets) and all(
        isinstance(chain, str) and chain for chain in expected_targets
    )
    valid_binders = bool(expected_binders) and all(
        isinstance(chain, str) and chain for chain in expected_binders
    )
    valid_metrics = (
        bool(requested)
        and len(set(requested)) == len(requested)
        and all(isinstance(name, str) and name for name in requested)
    )
    if not all(
        (valid_hash, valid_targets, valid_binders, valid_metrics)
    ) or not isinstance(expected_options, Mapping):
        raise _scientific_contract_error(
            "Host Rosetta expected-result contract is missing or invalid",
            "expected_contract",
        )
    return (
        expected_hash,
        expected_targets,
        expected_binders,
        requested,
        expected_options,
    )


def _validate_rosetta_partners(
    value: object,
    expected_targets: tuple,
    expected_binders: tuple,
) -> None:
    partners = _contract_mapping(value, "partners")
    if set(partners) != {"target_chains", "binder_chains"}:
        raise _scientific_contract_error(
            "Rosetta result partners has unexpected fields", "partners"
        )
    actual_targets = _contract_chains(
        partners.get("target_chains"), "partners.target_chains"
    )
    actual_binders = _contract_chains(
        partners.get("binder_chains"), "partners.binder_chains"
    )
    if actual_targets != expected_targets or actual_binders != expected_binders:
        raise _scientific_contract_error(
            "Rosetta result partners do not match the submitted partners",
            "partners",
        )


def _validate_rosetta_metrics(value: object, requested: tuple) -> None:
    metrics = _contract_mapping(value, "metrics")
    if set(metrics) != set(requested) or len(metrics) != len(requested):
        raise _scientific_contract_error(
            "Rosetta result metrics do not exactly match the requested metrics",
            "metrics",
        )
    for name in requested:
        metric = metrics[name]
        if name in ROSETTA_INTEGER_METRICS and type(metric) is not int:
            raise _scientific_contract_error(
                f"Rosetta metric {name} must be an integer", f"metrics.{name}"
            )
        if name not in ROSETTA_INTEGER_METRICS and (
            type(metric) is not float or not math.isfinite(metric)
        ):
            raise _scientific_contract_error(
                f"Rosetta metric {name} must be a finite floating-point number",
                f"metrics.{name}",
            )


def _validate_rosetta_selectors(
    value: object,
    expected_targets: tuple,
    expected_binders: tuple,
) -> None:
    selectors = _contract_mapping(value, "provenance.selectors")
    target = _contract_mapping(
        selectors.get("target"), "provenance.selectors.target"
    )
    binder = _contract_mapping(
        selectors.get("binder"), "provenance.selectors.binder"
    )
    interface = _contract_mapping(
        selectors.get("interface"), "provenance.selectors.interface"
    )
    if _contract_chains(
        target.get("chains"), "provenance.selectors.target.chains"
    ) != expected_targets:
        raise _scientific_contract_error(
            "Rosetta target selector does not match the request",
            "provenance.selectors.target.chains",
        )
    if _contract_chains(
        binder.get("chains"), "provenance.selectors.binder.chains"
    ) != expected_binders:
        raise _scientific_contract_error(
            "Rosetta binder selector does not match the request",
            "provenance.selectors.binder.chains",
        )
    expected_interface = "%s_%s" % (
        "".join(expected_targets),
        "".join(expected_binders),
    )
    if interface.get("expression") != expected_interface:
        raise _scientific_contract_error(
            "Rosetta interface selector does not match the request",
            "provenance.selectors.interface.expression",
        )
    pose_chains = _contract_chains(
        selectors.get("pose_chains"),
        "provenance.selectors.pose_chains",
        allow_blank=True,
    )
    if not set(expected_targets + expected_binders).issubset(pose_chains):
        raise _scientific_contract_error(
            "Rosetta provenance pose_chains omits a requested partner chain",
            "provenance.selectors.pose_chains",
        )


def _validate_rosetta_provenance(
    value: object,
    expected_targets: tuple,
    expected_binders: tuple,
    expected_options: Mapping[str, object],
) -> None:
    provenance = _contract_mapping(value, "provenance")
    if provenance.get("backend") != "pyrosetta":
        raise _scientific_contract_error(
            "Rosetta result provenance.backend must be 'pyrosetta'",
            "provenance.backend",
        )
    for field in ("pyrosetta_version", "rosetta_version", "init_flags"):
        _contract_string(provenance.get(field), f"provenance.{field}")
    for field in ("resolved_options", "cutoffs", "scorefunction"):
        _contract_mapping(provenance.get(field), f"provenance.{field}")
    options = _contract_mapping(provenance.get("options"), "provenance.options")
    if dict(options) != dict(expected_options):
        raise _scientific_contract_error(
            "Rosetta result provenance.options do not match the request",
            "provenance.options",
        )
    _validate_rosetta_selectors(
        provenance.get("selectors"), expected_targets, expected_binders
    )


def _validate_rosetta_result(
    result: object,
    expected: Mapping[str, object],
) -> Dict[str, object]:
    (
        expected_hash,
        expected_targets,
        expected_binders,
        requested,
        expected_options,
    ) = _validated_expected_contract(expected)
    if not isinstance(result, dict):
        raise _scientific_contract_error(
            "Rosetta result must be a JSON object", "result"
        )
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise _scientific_contract_error(
            "Rosetta result contains a non-finite or non-JSON value", "result"
        ) from exc
    if result.get("schema") != ROSETTA_SCHEMA:
        raise _scientific_contract_error(
            f"unexpected Rosetta result schema: {result.get('schema')!r}",
            "schema",
        )

    actual_hash = result.get("input_sha256")
    if actual_hash != expected_hash:
        raise _scientific_contract_error(
            "Rosetta result input_sha256 does not match the submitted structure",
            "input_sha256",
        )
    _validate_rosetta_partners(
        result.get("partners"), expected_targets, expected_binders
    )
    _validate_rosetta_metrics(result.get("metrics"), requested)
    _validate_rosetta_provenance(
        result.get("provenance"),
        expected_targets,
        expected_binders,
        expected_options,
    )
    warnings = result.get("warnings")
    if not isinstance(warnings, list) or not all(
        isinstance(warning, str) for warning in warnings
    ):
        raise _scientific_contract_error(
            "Rosetta result warnings must be an array of strings", "warnings"
        )
    return result


def _finish_rosetta_result(
    result: Dict[str, object],
    expected_contract: Mapping[str, object],
    output_json: str = "",
) -> Dict[str, object]:
    result = _validate_rosetta_result(result, expected_contract)
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
        output_target = _preflight_json_output(str(output_json))
        payload = _rosetta_payload(
            str(selection), target_chains, binder_chains, metrics, parsed_state
        )
        expected_contract = _expected_rosetta_contract(payload)
        result = worker_request(
            "/v1/analyze",
            payload,
            url=url,
            timeout=as_float(timeout, minimum=0.1, name="timeout"),
            expected_schema=ROSETTA_SCHEMA,
        )
        return _finish_rosetta_result(
            result,
            expected_contract,
            output_target,
        )
    except Exception as exc:
        _report_worker_error(exc)
        return _error_result(exc)


_ASYNC_JOBS: Dict[str, Dict[str, object]] = globals().get("_ASYNC_JOBS", {})
_ASYNC_BRIDGE = globals().get("_ASYNC_BRIDGE", None)
_PLIP_GROUP_EPOCHS: Dict[str, int] = globals().get("_PLIP_GROUP_EPOCHS", {})
_PLIP_GROUP_RESERVATIONS: Dict[str, Dict[str, object]] = globals().get(
    "_PLIP_GROUP_RESERVATIONS", {}
)
_WORKER_JOB_HISTORY_LIMIT = 100
_previous_worker_job_history = globals().get("_WORKER_JOB_HISTORY", [])
_WORKER_JOB_HISTORY = (
    _previous_worker_job_history
    if isinstance(_previous_worker_job_history, list)
    else []
)
_WORKER_JOB_HISTORY[:] = [
    entry for entry in _WORKER_JOB_HISTORY if isinstance(entry, dict)
][-_WORKER_JOB_HISTORY_LIMIT:]


def _safe_history_message(value: object, fallback: str) -> str:
    text = str(value).strip()
    pdb_prefixes = ("ATOM ", "HETATM ", "MODEL ", "CONECT ", "ANISOU ")
    if not text or any(
        line.lstrip().upper().startswith(pdb_prefixes)
        for line in text.splitlines()
    ):
        return fallback
    first_line = " ".join(text.splitlines()).strip()
    return first_line[:500] if first_line else fallback


def _history_error(exc: Exception) -> Dict[str, object]:
    action = _worker_error_action(exc)
    if isinstance(exc, WorkerRequestError):
        return {
            "code": exc.code,
            "message": _safe_history_message(exc, "worker request failed"),
            "http_status": exc.http_status,
            "retriable": exc.retriable,
            "action": action,
        }
    if _is_expected_worker_error(exc):
        return {
            "code": "input_error",
            "message": _safe_history_message(exc, "invalid command input"),
            "http_status": None,
            "retriable": False,
            "action": action,
        }
    return {
        "code": "internal_error",
        "message": "internal error; see the console traceback",
        "http_status": None,
        "retriable": False,
        "action": "Inspect the console traceback before retrying.",
        "type": type(exc).__name__,
    }


def _history_error_value(value: object) -> Optional[Dict[str, object]]:
    if value is None:
        return None
    if isinstance(value, Exception):
        return _history_error(value)
    if isinstance(value, Mapping):
        return {
            "code": str(value.get("code", "error")),
            "message": _safe_history_message(
                value.get("message", ""), "worker job did not complete"
            ),
            "http_status": value.get("http_status"),
            "retriable": bool(value.get("retriable", False)),
            "action": _safe_history_message(value.get("action", ""), ""),
        }
    return {
        "code": "error",
        "message": "worker job did not complete",
        "http_status": None,
        "retriable": False,
        "action": "Run pp_worker_jobs and inspect the console output.",
    }


def _plip_result_summary(record) -> Dict[str, object]:
    summary: Dict[str, object] = {}
    root_group = str(getattr(getattr(record, "manifest", None), "root_group", ""))
    if root_group:
        summary["root_group"] = root_group
    result = getattr(record, "result", None)
    raw_summary = getattr(result, "summary", {})
    counts = raw_summary.get("counts_by_type", {}) if isinstance(raw_summary, Mapping) else {}
    if isinstance(counts, Mapping):
        summary["counts_by_type"] = {
            str(name): count
            for name, count in counts.items()
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0
        }
    warnings = getattr(result, "warnings", ())
    if isinstance(warnings, (list, tuple)):
        summary["warnings_count"] = len(warnings)
    return summary


def _rosetta_result_summary(result: object) -> Dict[str, object]:
    if not isinstance(result, Mapping):
        return {}
    metrics = result.get("metrics", {})
    safe_metrics = {}
    if isinstance(metrics, Mapping):
        for name, value in metrics.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, int) or (
                isinstance(value, float) and math.isfinite(value)
            ):
                safe_metrics[str(name)] = value
    warnings = result.get("warnings", ())
    return {
        "metrics": safe_metrics,
        "warnings_count": len(warnings) if isinstance(warnings, (list, tuple)) else 0,
    }


def _remember_terminal_job(
    job_id: str,
    job: Mapping[str, object],
    status: str,
    *,
    result_summary: Optional[Mapping[str, object]] = None,
    error: object = None,
) -> Dict[str, object]:
    entry = {
        "job_id": str(job_id),
        "kind": job.get("kind"),
        "status": str(status),
        "submitted_at": job.get("submitted_at"),
        "finished_at": _utc_timestamp(),
        "preset": job.get("preset"),
        "group": job.get("group"),
        "request_summary": copy.deepcopy(dict(job.get("request_summary", {}) or {})),
        "result_summary": copy.deepcopy(dict(result_summary or {})),
        "output_json": str(job.get("output_json", "") or ""),
        "error": _history_error_value(error),
    }
    _WORKER_JOB_HISTORY.append(entry)
    if len(_WORKER_JOB_HISTORY) > _WORKER_JOB_HISTORY_LIMIT:
        del _WORKER_JOB_HISTORY[:-_WORKER_JOB_HISTORY_LIMIT]
    return entry


def _terminal_job_result(
    job_id: str,
    job: Mapping[str, object],
    status: str,
    *,
    result_summary: Optional[Mapping[str, object]] = None,
    error: object = None,
) -> Dict[str, object]:
    entry = _remember_terminal_job(
        job_id,
        job,
        status,
        result_summary=result_summary,
        error=error,
    )
    response: Dict[str, object] = {"status": status, "job_id": job_id}
    if entry["error"] is not None:
        response["error"] = entry["error"]["message"]
        response["error_detail"] = copy.deepcopy(entry["error"])
    return response


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


def _plip_reservation_group(submission) -> str:
    output_group = str(getattr(submission, "output_group", "") or "").strip()
    requested_group = str(getattr(submission, "group", "") or "").strip()
    if output_group:
        return output_group
    if requested_group:
        return requested_group
    request = getattr(submission, "request", {})
    run_id = request.get("run_id", "") if isinstance(request, Mapping) else ""
    return "__plip_run__%s" % str(run_id)


def _reserve_plip_group(group: str, job_id: str) -> int:
    epoch = int(_PLIP_GROUP_EPOCHS.get(group, 0)) + 1
    _PLIP_GROUP_EPOCHS[group] = epoch
    _PLIP_GROUP_RESERVATIONS[group] = {"epoch": epoch, "job_id": job_id}
    return epoch


def _plip_job_is_current(job_id: str, job: Mapping[str, object]) -> bool:
    group = str(job.get("reservation_group", ""))
    reservation = _PLIP_GROUP_RESERVATIONS.get(group)
    return bool(
        reservation
        and reservation.get("job_id") == job_id
        and reservation.get("epoch") == job.get("reservation_epoch")
    )


def _release_plip_reservation(job_id: str, job: Mapping[str, object]) -> None:
    group = str(job.get("reservation_group", ""))
    reservation = _PLIP_GROUP_RESERVATIONS.get(group)
    if (
        reservation
        and reservation.get("job_id") == job_id
        and reservation.get("epoch") == job.get("reservation_epoch")
    ):
        del _PLIP_GROUP_RESERVATIONS[group]


def _complete_async_job(job_id: str, result, error) -> Dict[str, object]:
    job = _ASYNC_JOBS.pop(job_id, None)
    if job is None:
        return {"status": "unknown", "job_id": job_id}
    is_plip = job.get("kind") == "plip_interaction"
    try:
        if job.get("cancelled"):
            print(
                f"Worker job {job_id} result suppressed after cancel request; "
                "the backend calculation may still have run to completion"
            )
            return _terminal_job_result(job_id, job, "cancelled")
        if error is not None:
            _report_worker_error(
                error,
                context=f"Worker job {job_id}",
                current_exception=False,
            )
            return _terminal_job_result(job_id, job, "failed", error=error)
        if is_plip:
            from pymolplugins.interactions.controller import (
                RemoteInteractionStaleError,
                get_controller,
            )

            if not _plip_job_is_current(job_id, job):
                print(
                    f"Worker job {job_id} result suppressed because a newer "
                    "request owns the output group"
                )
                return _terminal_job_result(
                    job_id,
                    job,
                    "stale",
                    error={
                        "code": "superseded",
                        "message": "a newer request owns the output group",
                        "action": "Run pp_worker_jobs to inspect the latest request.",
                    },
                )
            try:
                record = get_controller(cmd).commit_remote_result(
                    job["submission"], result, expected_current=True
                )
            except RemoteInteractionStaleError as exc:
                print(f"Worker job {job_id} result suppressed as stale: {exc}")
                return _terminal_job_result(
                    job_id,
                    job,
                    "stale",
                    error={
                        "code": "source_changed",
                        "message": str(exc),
                        "action": "Resubmit the interaction job for the current source.",
                    },
                )
            print(
                f"Worker job {job_id} completed: interaction group="
                f"{record.manifest.root_group}"
            )
            summary = _plip_result_summary(record)
        else:
            finished = _finish_rosetta_result(
                result,
                job.get("expected_contract", {}),
                str(job.get("output_json", "")),
            )
            print(f"Worker job {job_id} completed")
            summary = _rosetta_result_summary(finished)
        return _terminal_job_result(
            job_id, job, "completed", result_summary=summary
        )
    except Exception as exc:
        _report_worker_error(exc, context=f"Worker job {job_id}")
        return _terminal_job_result(job_id, job, "failed", error=exc)
    finally:
        if is_plip:
            _release_plip_reservation(job_id, job)


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
    job_id = ""
    job = None
    try:
        parsed_state = as_int(state, minimum=1, name="state")
        output_target = _preflight_json_output(str(output_json))
        source_summary: Dict[str, object] = {}
        payload = _rosetta_payload(
            str(selection),
            target_chains,
            binder_chains,
            metrics,
            parsed_state,
            source_summary,
        )
        expected_contract = _expected_rosetta_contract(payload)
        parsed_timeout = as_float(timeout, minimum=0.1, name="timeout")
        bridge = _bridge()
        job_id = uuid.uuid4().hex[:12]
        job = {
            "kind": "rosetta_metrics",
            "output_json": output_target,
            "expected_contract": expected_contract,
            "preset": None,
            "group": None,
            "request_summary": {
                "source": (
                    source_summary.get("source_object")
                    or source_summary.get("selection")
                ),
                "selection": source_summary.get("selection"),
                "state": parsed_state,
                "target_chains": list(payload["partners"]["target_chains"]),
                "binder_chains": list(payload["partners"]["binder_chains"]),
                "metrics": list(payload["metrics"]),
            },
            "submitted_at": _utc_timestamp(),
            "cancelled": False,
        }
        _ASYNC_JOBS[job_id] = job
        runnable = _WorkerRunnable(
            bridge,
            job_id,
            "/v1/analyze",
            payload,
            str(url),
            parsed_timeout,
            ROSETTA_SCHEMA,
        )
        job["runnable"] = runnable
        QtCore.QThreadPool.globalInstance().start(runnable)
        print(
            f"Worker job {job_id} submitted; cancel suppresses result handling "
            "but cannot interrupt an in-progress PyRosetta C++ call. "
            f"Inspect with pp_worker_jobs; cancel with pp_worker_cancel {job_id}"
        )
        return {"status": "submitted", "job_id": job_id}
    except Exception as exc:
        if job_id and job is not None and _ASYNC_JOBS.get(job_id) is job:
            _ASYNC_JOBS.pop(job_id, None)
            _remember_terminal_job(job_id, job, "failed", error=exc)
        _report_worker_error(exc)
        return _error_result(exc)


def submit_plip_interaction(submission, url: str = "", timeout: float = 120.0):
    """Submit an already-exported PLIP request without calling cmd off-thread."""

    if isinstance(timeout, bool):
        raise ValueError("timeout must be a finite number >= 0.1, not a boolean")
    timeout_value = float(timeout)
    if not math.isfinite(timeout_value) or timeout_value < 0.1:
        raise ValueError("timeout must be a finite number >= 0.1 seconds")
    bridge = _bridge()
    job_id = uuid.uuid4().hex[:12]
    reservation_group = _plip_reservation_group(submission)
    reservation_epoch = _reserve_plip_group(reservation_group, job_id)
    request = submission.request if isinstance(submission.request, Mapping) else {}
    source_contract = request.get("source", {})
    if not isinstance(source_contract, Mapping):
        source_contract = {}
    job = {
        "kind": "plip_interaction",
        "submission": submission,
        "preset": submission.preset,
        "group": reservation_group,
        "request_summary": {
            "run_id": str(request.get("run_id", "")),
            "source": str(
                getattr(submission, "source_selection", "")
                or source_contract.get("object", "")
            ),
            "state": getattr(
                submission,
                "source_state",
                source_contract.get("state"),
            ),
            "preset": str(submission.preset),
        },
        "output_json": str(getattr(submission, "output_json", "") or ""),
        "submitted_at": _utc_timestamp(),
        "reservation_group": reservation_group,
        "reservation_epoch": reservation_epoch,
        "cancelled": False,
    }
    _ASYNC_JOBS[job_id] = job
    try:
        runnable = _WorkerRunnable(
            bridge,
            job_id,
            "/v1/interactions/plip",
            submission.request,
            str(url),
            timeout_value,
            "pymolplugins.interactions/1.0",
        )
        job["runnable"] = runnable
        QtCore.QThreadPool.globalInstance().start(runnable)
    except Exception as exc:
        _ASYNC_JOBS.pop(job_id, None)
        _release_plip_reservation(job_id, job)
        _remember_terminal_job(job_id, job, "failed", error=exc)
        raise
    print(
        f"Worker job {job_id} submitted for {submission.preset}; "
        "cancel suppresses rendering but does not interrupt backend work. "
        f"Inspect with pp_worker_jobs; cancel with pp_worker_cancel {job_id}"
    )
    return {
        "status": "submitted",
        "job_id": job_id,
        "run_id": submission.request["run_id"],
        "preset": submission.preset,
        "requested_group": submission.group,
        "output_group": reservation_group,
    }


def pp_worker_cancel(job_id: str) -> Dict[str, object]:
    value = str(job_id).strip()
    job = _ASYNC_JOBS.get(value)
    if job is None:
        print(
            f"Worker job {value or '(blank)'} is unknown; run pp_worker_jobs "
            "to inspect active jobs and recent history"
        )
        return {
            "status": "unknown",
            "job_id": value,
            "hint": "run pp_worker_jobs",
        }
    job["cancelled"] = True
    if job.get("kind") == "plip_interaction":
        _release_plip_reservation(value, job)
    print(
        f"Worker job {value}: result suppression requested; network/backend work "
        "is not forcibly interrupted"
    )
    return {"status": "cancel_requested", "job_id": value}


def pp_worker_jobs(quiet: object = 0) -> Dict[str, object]:
    active = {
        job_id: {
            "kind": job.get("kind"),
            "status": "cancel_requested" if job.get("cancelled") else "running",
            "cancelled": bool(job.get("cancelled")),
            "preset": job.get("preset"),
            "group": job.get("group"),
            "request_summary": copy.deepcopy(
                dict(job.get("request_summary", {}) or {})
            ),
            "submitted_at": job.get("submitted_at"),
            "output_json": str(job.get("output_json", "") or ""),
        }
        for job_id, job in _ASYNC_JOBS.items()
    }
    result = {
        "active": active,
        "history": copy.deepcopy(list(reversed(_WORKER_JOB_HISTORY))),
    }
    if not as_bool(quiet):
        print(json.dumps(result, indent=2, sort_keys=True))
    return result


extend_command(cmd, "pp_worker_health", pp_worker_health)
extend_command(cmd, "pp_worker_capabilities", pp_worker_capabilities)
extend_command(cmd, "pp_rosetta_metrics", pp_rosetta_metrics)
extend_command(cmd, "pp_rosetta_metrics_async", pp_rosetta_metrics_async)
extend_command(cmd, "pp_worker_cancel", pp_worker_cancel)
extend_command(cmd, "pp_worker_jobs", pp_worker_jobs)
