"""Single entry point and runtime doctor for the PymolPlugins toolkit."""

import importlib
import json
import os
import platform
import sys
import tempfile
import traceback
from typing import Dict, List

from pymol import cmd


REPOSITORY_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, REPOSITORY_ROOT)

from pymolplugins.core.parameters import as_bool, extend_command  # noqa: E402
from pymolplugins.host.manifest import (  # noqa: E402
    CANONICAL_COMMANDS,
    CANONICAL_MODULES,
    LEGACY_GLOBAL_ALIASES,
)


def pp_load() -> Dict[str, object]:
    """Import all canonical modules without loading legacy duplicates."""
    loaded: List[str] = []
    errors: Dict[str, str] = {}
    for module_name in CANONICAL_MODULES:
        try:
            importlib.import_module(module_name)
            loaded.append(module_name)
        except Exception as exc:
            errors[module_name] = str(exc)
            print(f"Failed to load {module_name}: {exc}")
            traceback.print_exc()
    result = {
        "status": "ok" if not errors else "partial",
        "repository_root": REPOSITORY_ROOT,
        "loaded": loaded,
        "errors": errors,
    }
    print(
        f"PymolPlugins load: {len(loaded)}/{len(CANONICAL_MODULES)} modules, "
        f"status={result['status']}"
    )
    return result


def _import_status(module_name: str) -> Dict[str, object]:
    try:
        module = importlib.import_module(module_name)
        return {
            "available": True,
            "version": str(getattr(module, "__version__", "unknown")),
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _worker_health(timeout: float = 2.0) -> Dict[str, object]:
    try:
        remote = importlib.import_module("pymolplugins.remote.commands")
        return remote.worker_request(
            "/v1/health",
            timeout=timeout,
            expected_schema="pymolplugins.worker/1.0",
        )
    except Exception as exc:
        return {"status": "unreachable", "error": str(exc)}


def _command_registered(name: str) -> bool:
    keyword = getattr(cmd, "keyword", None)
    if isinstance(keyword, dict) and name in keyword:
        return True
    return callable(getattr(cmd, name, None))


def _cache_probe() -> Dict[str, object]:
    path = os.path.abspath(
        os.path.expanduser(
            os.environ.get("PYMOLPLUGINS_CACHE_DIR", "~/.cache/pymolplugins")
        )
    )
    try:
        os.makedirs(path, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="doctor_", dir=path):
            pass
        return {"path": path, "writable": True}
    except Exception as exc:
        return {"path": path, "writable": False, "error": str(exc)}


def _wsl_distro_name() -> str:
    configured = os.environ.get("WSL_DISTRO_NAME", "").strip()
    if configured:
        return configured
    if not REPOSITORY_ROOT.startswith("\\\\"):
        return ""
    parts = [part for part in REPOSITORY_ROOT.strip("\\").split("\\") if part]
    if len(parts) >= 2 and parts[0].lower() in {"wsl.localhost", "wsl$"}:
        return parts[1]
    return ""


def pp_capabilities(check_worker=1) -> Dict[str, object]:
    """Return the discoverable local command and optional-backend surface."""
    commands = {
        category: {name: _command_registered(name) for name in names}
        for category, names in CANONICAL_COMMANDS.items()
    }
    result: Dict[str, object] = {
        "schema": "pymolplugins.capabilities/1.0",
        "commands": commands,
        "complete_categories": [
            category
            for category, values in commands.items()
            if all(values.values())
        ],
        "optional_host_modules": {
            "numpy": _import_status("numpy"),
            "rdkit": _import_status("rdkit"),
            "plip": _import_status("plip"),
        },
        "anvil": {
            "configured": bool(os.environ.get("PYMOLPLUGINS_ANVIL_EXECUTABLE", "").strip()),
            "executable": os.environ.get("PYMOLPLUGINS_ANVIL_EXECUTABLE", "").strip(),
        },
        "runtime_boundary": {
            "repository_root": REPOSITORY_ROOT,
            "repository_root_is_unc": REPOSITORY_ROOT.startswith("\\\\"),
            "wsl_distro": _wsl_distro_name(),
        },
    }
    if as_bool(check_worker):
        result["worker"] = _worker_health()
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def pp_doctor(check_worker=1) -> Dict[str, object]:
    """Report Host PyMOL capabilities and the optional WSL worker state."""
    try:
        pymol_version = cmd.get_version()
    except Exception as exc:
        pymol_version = ["unknown", str(exc)]
    required_api = (
        "get_object_ttt",
        "set_object_ttt",
        "get_object_matrix",
        "get_model",
        "load_cgo",
        "group",
        "select_list",
    )
    api = {name: hasattr(cmd, name) for name in required_api}
    root_is_unc = REPOSITORY_ROOT.startswith("\\\\")
    result: Dict[str, object] = {
        "schema": "pymolplugins.doctor/1.0",
        "python": sys.version,
        "platform": platform.platform(),
        "os_name": os.name,
        "pymol_version": list(pymol_version) if isinstance(pymol_version, tuple) else pymol_version,
        "repository_root": REPOSITORY_ROOT,
        "repository_root_is_unc": root_is_unc,
        "api": api,
        "missing_required_api": [name for name, available in api.items() if not available],
        "optional_host_modules": {
            "numpy": _import_status("numpy"),
            "rdkit": _import_status("rdkit"),
            "plip": _import_status("plip"),
        },
        "worker_url": os.environ.get(
            "PYMOLPLUGINS_WORKER_URL", "http://127.0.0.1:8765"
        ),
        "wsl_distro": _wsl_distro_name(),
        "cache": _cache_probe(),
        "anvil": {
            "executable": os.environ.get("PYMOLPLUGINS_ANVIL_EXECUTABLE", "").strip(),
            "exists": os.path.isfile(
                os.path.expanduser(
                    os.environ.get("PYMOLPLUGINS_ANVIL_EXECUTABLE", "")
                )
            )
            if os.environ.get("PYMOLPLUGINS_ANVIL_EXECUTABLE", "").strip()
            else False,
        },
        "commands": {
            name: _command_registered(name)
            for names in CANONICAL_COMMANDS.values()
            for name in names
        },
        "legacy_global_aliases_present": [
            name for name in LEGACY_GLOBAL_ALIASES if _command_registered(name)
        ],
    }
    result["missing_commands"] = [
        name for name, available in result["commands"].items() if not available
    ]
    if as_bool(check_worker):
        result["worker"] = _worker_health()
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def pp_help(category="") -> Dict[str, object]:
    """Print the canonical command inventory, optionally for one category."""
    requested = str(category).strip().lower()
    if requested and requested not in CANONICAL_COMMANDS:
        raise ValueError(
            "unknown command category %r; choose from %s"
            % (requested, ", ".join(sorted(CANONICAL_COMMANDS)))
        )
    selected = (
        {requested: CANONICAL_COMMANDS[requested]}
        if requested
        else CANONICAL_COMMANDS
    )
    result = {
        "schema": "pymolplugins.command-help/1.0",
        "categories": {
            name: list(commands) for name, commands in selected.items()
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def show_structure_annotator_from_toolkit():
    """Load the optional annotation UI only when explicitly requested."""
    module = importlib.import_module("pymolplugins.annotation.plugin")
    return module.show_structure_annotator_panel()


def __init_plugin__(app=None):
    from pymol.plugins import addmenuitemqt

    addmenuitemqt("PymolPlugins: Load Toolkit", pp_load)
    addmenuitemqt("PymolPlugins: Doctor", pp_doctor)
    addmenuitemqt("PymolPlugins: Capabilities", pp_capabilities)
    addmenuitemqt("PymolPlugins: Command Help", pp_help)
    addmenuitemqt("PymolPlugins: Structure Annotator", show_structure_annotator_from_toolkit)


extend_command(cmd, "pp_load", pp_load)
extend_command(cmd, "pp_doctor", pp_doctor)
extend_command(cmd, "pp_capabilities", pp_capabilities)
extend_command(cmd, "pp_help", pp_help)

# A plugin-manager load should make canonical commands immediately available.
pp_load()
