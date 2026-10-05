"""Single entry point and runtime doctor for the PymolPlugins toolkit."""

import importlib
import inspect
import json
import os
import platform
import sys
import tempfile
import traceback
from typing import Dict, List, Tuple

from pymol import cmd


REPOSITORY_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, REPOSITORY_ROOT)

from pymolplugins.core.parameters import (  # noqa: E402
    as_bool,
    extend_command,
    is_console_command,
)
from pymolplugins.host.help_catalog import (  # noqa: E402
    CATALOG_SCHEMA,
    category_help,
    default_tasks,
    exact_help,
    help_topics,
    search_help,
)
from pymolplugins.host.manifest import (  # noqa: E402
    AUTOLOADED_COMPAT_COMMANDS,
    CANONICAL_COMMANDS,
    CANONICAL_MODULES,
    COMMAND_TARGETS,
    LEGACY_GLOBAL_ALIASES,
    declared_command_names,
)


def _registered_callback(name: str):
    """Return the callback currently occupying one PyMOL command slot."""

    keyword = getattr(cmd, "keyword", None)
    if isinstance(keyword, dict) and name in keyword:
        entry = keyword[name]
        if callable(entry):
            return entry
        if isinstance(entry, (list, tuple)) and entry and callable(entry[0]):
            return entry[0]
    candidate = getattr(cmd, name, None)
    return candidate if callable(candidate) else None


def _registered_names() -> Tuple[str, ...]:
    keyword = getattr(cmd, "keyword", None)
    if not isinstance(keyword, dict):
        return ()
    return tuple(str(name) for name in keyword)


def _unwrapped(function):
    function = getattr(function, "_pymolplugins_literal_target", function)
    try:
        return inspect.unwrap(function)
    except (ValueError, TypeError):
        return function


def _callback_description(function) -> Dict[str, object]:
    if function is None:
        return {"registered": False}
    base = _unwrapped(function)
    return {
        "registered": True,
        "module": str(getattr(base, "__module__", "")),
        "qualname": str(
            getattr(base, "__qualname__", getattr(base, "__name__", type(base).__name__))
        ),
        "console_adapted": is_console_command(function),
    }


def _expected_callback_description(name: str, function) -> Dict[str, object]:
    description = _callback_description(function)
    # Every command loaded by this plugin crosses PyMOL's STRICT parser.  The
    # compatibility names keep their historical call signatures, but they
    # still need the same one-layer quote normalization as canonical names.
    description["console_adapted"] = True
    return description


def _expected_callbacks():
    callbacks = {}
    errors = {}
    for name, (module_name, function_name) in COMMAND_TARGETS.items():
        module = sys.modules.get(module_name)
        if module is None:
            errors[name] = "module is not loaded: %s" % module_name
            continue
        function = getattr(module, function_name, None)
        if not callable(function):
            errors[name] = "callback is unavailable: %s.%s" % (
                module_name,
                function_name,
            )
            continue
        callbacks[name] = function
    return callbacks, errors


def _same_callback(actual, expected) -> bool:
    return actual is not None and _unwrapped(actual) is _unwrapped(expected)


def _command_audit() -> Dict[str, object]:
    expected, expected_errors = _expected_callbacks()
    canonical_names = {
        name for commands in CANONICAL_COMMANDS.values() for name in commands
    }
    compat_names = {
        name
        for commands in AUTOLOADED_COMPAT_COMMANDS.values()
        for name in commands
    }
    declared = canonical_names | compat_names
    canonical_status = {}
    compat_status = {}
    missing = []
    missing_compat = []
    overridden = {}
    foreign = {}

    for name in declared_command_names():
        actual = _registered_callback(name)
        target = expected.get(name)
        if actual is None:
            status = "missing"
            (missing if name in canonical_names else missing_compat).append(name)
        elif target is None:
            status = "expected_callback_unavailable"
        elif _same_callback(actual, target):
            if name in canonical_names and not is_console_command(actual):
                status = "unadapted"
                overridden[name] = {
                    "reason": "canonical callback bypasses the console adapter",
                    "expected": _expected_callback_description(name, target),
                    "actual": _callback_description(actual),
                }
            else:
                status = "ok"
        else:
            status = "overridden"
            detail = {
                "expected": _expected_callback_description(name, target),
                "actual": _callback_description(actual),
            }
            overridden[name] = detail
            module_name = str(detail["actual"].get("module", ""))
            if not (
                module_name == "pymolplugins"
                or module_name.startswith("pymolplugins.")
            ):
                foreign[name] = detail["actual"]
        (canonical_status if name in canonical_names else compat_status)[name] = status

    undeclared = {}
    for name in _registered_names():
        if name in declared or not name.startswith("pp_"):
            continue
        actual = _registered_callback(name)
        description = _callback_description(actual)
        module_name = str(description.get("module", ""))
        if module_name == "pymolplugins" or module_name.startswith("pymolplugins."):
            undeclared[name] = description
        else:
            foreign[name] = description

    return {
        "schema": "pymolplugins.command-audit/1.0",
        "declared_canonical_count": len(canonical_names),
        "declared_compat_count": len(compat_names),
        "declared_surface": {
            "canonical": {
                category: list(commands)
                for category, commands in CANONICAL_COMMANDS.items()
            },
            "compat": {
                category: list(commands)
                for category, commands in AUTOLOADED_COMPAT_COMMANDS.items()
            },
        },
        "canonical_status": canonical_status,
        "compat_status": compat_status,
        "missing_commands": sorted(missing),
        "missing_compat_commands": sorted(missing_compat),
        "overridden_commands": overridden,
        "undeclared_commands": undeclared,
        "foreign_commands": foreign,
        "expected_callback_errors": expected_errors,
    }


def _register_expected(name: str, function) -> None:
    # Compatibility callbacks preserve their historical Python signatures,
    # not PyMOL's retained quote delimiters.  Register every declared command
    # through the shared console adapter so a later ``pp_load`` repair behaves
    # exactly like the module's first import.
    extend_command(cmd, name, function)


def _preexisting_declared_conflicts():
    """Capture occupied declared slots which are not owned by expected modules."""

    expected, _ = _expected_callbacks()
    conflicts = {}
    for name in declared_command_names():
        actual = _registered_callback(name)
        if actual is None:
            continue
        target = expected.get(name)
        if target is not None and _same_callback(actual, target):
            continue
        expected_module, expected_function = COMMAND_TARGETS[name]
        base = _unwrapped(actual)
        actual_module = str(getattr(base, "__module__", ""))
        actual_name = str(getattr(base, "__name__", ""))
        if actual_module != expected_module or actual_name != expected_function:
            conflicts[name] = actual
    return conflicts


def _restore_preexisting_conflicts(conflicts) -> List[str]:
    restored = []
    for name, function in conflicts.items():
        if _registered_callback(name) is not function:
            cmd.extend(name, function)
        restored.append(name)
    return restored


def _repair_declared_commands(repair_overrides: bool):
    expected, _ = _expected_callbacks()
    repaired = []
    repaired_overrides = []
    canonical_names = {
        name for commands in CANONICAL_COMMANDS.values() for name in commands
    }
    for name in declared_command_names():
        target = expected.get(name)
        if target is None:
            continue
        actual = _registered_callback(name)
        if actual is None:
            _register_expected(name, target)
            repaired.append(name)
        elif _same_callback(actual, target):
            if name in canonical_names and not is_console_command(actual):
                _register_expected(name, target)
                repaired.append(name)
        elif repair_overrides:
            _register_expected(name, target)
            repaired.append(name)
            repaired_overrides.append(name)
    return repaired, repaired_overrides


def pp_load(repair=0) -> Dict[str, object]:
    """Import modules, restore missing callbacks, and verify command ownership.

    Modules are never reloaded: navigation, alignment, style, and worker modules
    hold live session state.  Missing declared callbacks are safe to restore from
    their already-imported functions.  An occupied command slot is left intact
    unless the caller explicitly passes ``repair=1``.
    """

    loaded: List[str] = []
    errors: Dict[str, str] = {}
    try:
        repair_value = as_bool(repair)
    except Exception as exc:
        return {
            "status": "partial",
            "repository_root": REPOSITORY_ROOT,
            "loaded": loaded,
            "errors": {"repair": str(exc)},
            "repaired_commands": [],
            "repaired_overrides": [],
        }
    preexisting_conflicts = _preexisting_declared_conflicts()
    for module_name in CANONICAL_MODULES:
        try:
            importlib.import_module(module_name)
            loaded.append(module_name)
        except Exception as exc:
            errors[module_name] = str(exc)
            print(f"Failed to load {module_name}: {exc}")
            traceback.print_exc()

    preserved_conflicts = (
        []
        if repair_value
        else _restore_preexisting_conflicts(preexisting_conflicts)
    )
    repaired_commands, repaired_overrides = _repair_declared_commands(repair_value)
    audit = _command_audit()
    blocking_command_issues = bool(
        audit["missing_commands"]
        or audit["missing_compat_commands"]
        or audit["overridden_commands"]
        or audit["undeclared_commands"]
        or audit["expected_callback_errors"]
    )
    result = {
        "status": "ok" if not errors and not blocking_command_issues else "partial",
        "repository_root": REPOSITORY_ROOT,
        "loaded": loaded,
        "errors": errors,
        "repaired_commands": repaired_commands,
        "repaired_overrides": repaired_overrides,
        "preserved_conflicts": preserved_conflicts,
        "missing_commands": audit["missing_commands"],
        "missing_compat_commands": audit["missing_compat_commands"],
        "overridden_commands": audit["overridden_commands"],
        "undeclared_commands": audit["undeclared_commands"],
        "foreign_commands": audit["foreign_commands"],
        "expected_callback_errors": audit["expected_callback_errors"],
        "command_audit": audit,
    }
    print(
        f"PymolPlugins load: {len(loaded)}/{len(CANONICAL_MODULES)} modules, "
        f"commands={audit['declared_canonical_count']}+"
        f"{audit['declared_compat_count']} compat, "
        f"status={result['status']}"
    )
    if audit["overridden_commands"] and not repair_value:
        print(
            "Command conflicts were not overwritten: %s. "
            "Inspect pp_doctor check_worker=0; explicitly run pp_load repair=1 "
            "only when restoring toolkit ownership is intended."
            % ", ".join(sorted(audit["overridden_commands"]))
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
    audit = _command_audit()
    commands = {
        category: {
            name: audit["canonical_status"].get(name) == "ok" for name in names
        }
        for category, names in CANONICAL_COMMANDS.items()
    }
    compat_commands = {
        category: {
            name: audit["compat_status"].get(name) == "ok" for name in names
        }
        for category, names in AUTOLOADED_COMPAT_COMMANDS.items()
    }
    result: Dict[str, object] = {
        "schema": "pymolplugins.capabilities/1.0",
        "commands": commands,
        "compat_commands": compat_commands,
        "complete_categories": [
            category
            for category, values in commands.items()
            if all(values.values())
        ],
        "missing_commands": audit["missing_commands"],
        "missing_compat_commands": audit["missing_compat_commands"],
        "overridden_commands": audit["overridden_commands"],
        "undeclared_commands": audit["undeclared_commands"],
        "foreign_commands": audit["foreign_commands"],
        "expected_callback_errors": audit["expected_callback_errors"],
        "command_audit": audit,
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
    audit = _command_audit()
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
            name: audit["canonical_status"].get(name) == "ok"
            for names in CANONICAL_COMMANDS.values()
            for name in names
        },
        "compat_commands": {
            name: audit["compat_status"].get(name) == "ok"
            for names in AUTOLOADED_COMPAT_COMMANDS.values()
            for name in names
        },
        "command_audit": audit,
        "overridden_commands": audit["overridden_commands"],
        "undeclared_commands": audit["undeclared_commands"],
        "foreign_commands": audit["foreign_commands"],
        "expected_callback_errors": audit["expected_callback_errors"],
        "missing_compat_commands": audit["missing_compat_commands"],
        "legacy_global_aliases_present": [
            name for name in LEGACY_GLOBAL_ALIASES if _command_registered(name)
        ],
    }
    # Backward-compatible field: missing means absent, while an occupied but
    # foreign callback is reported separately under overridden_commands.
    result["missing_commands"] = audit["missing_commands"]
    if as_bool(check_worker):
        result["worker"] = _worker_health()
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def _help_categories(selected=""):
    if selected:
        return {selected: list(CANONICAL_COMMANDS[selected])}
    return {
        name: list(commands) for name, commands in CANONICAL_COMMANDS.items()
    }


def _help_compat_categories(selected=""):
    if selected:
        return {selected: list(AUTOLOADED_COMPAT_COMMANDS[selected])}
    return {
        name: list(commands)
        for name, commands in AUTOLOADED_COMPAT_COMMANDS.items()
    }


def _print_help_result(result: Dict[str, object]) -> None:
    mode = result.get("mode")
    if mode == "tasks":
        print("PymolPlugins common tasks (run pp_help TOPIC for detail):")
        for task in result.get("tasks", []):
            print("  %-18s %s" % (task["name"], task["example"]))
        return
    if mode == "category":
        match = result["matches"][0]
        print("%s: %s" % (match["name"], match["purpose"]))
        for command in match["commands"]:
            print("  %s" % command)
        print("Example: %s" % match["example"])
        print("Changes scene: %s" % ("yes" if match["mutates"] else "no"))
        if match["undo"]:
            print("Undo: %s" % match["undo"])
        for command in match["next"]:
            print("Next: %s" % command)
        return
    matches = result.get("matches", [])
    if not matches:
        print(
            "No help match for %r. Try pp_help, pp_help navigation, "
            "pp_help surface, or pp_help publication." % result.get("query", "")
        )
        return
    print("PymolPlugins help for %r:" % result.get("query", ""))
    for match in matches:
        print("  %s [%s] - %s" % (match["name"], match["kind"], match["purpose"]))
        print("    Usage: %s" % match["minimal_usage"])
        print("    Example: %s" % match["example"])
        print("    Changes scene: %s" % ("yes" if match["mutates"] else "no"))
        if match["undo"]:
            print("    Undo: %s" % match["undo"])
        for command in match["next"]:
            print("    Next: %s" % command)


def pp_help(category="") -> Dict[str, object]:
    """Show task-first help or resolve an exact/fuzzy category, command, or preset.

    The historical ``categories`` mapping and schema identifier remain present
    for scripts which consumed the original inventory-only result.
    """

    requested = str(category).strip().lower()
    result: Dict[str, object] = {
        # Kept stable as an additive schema evolution boundary.
        "schema": "pymolplugins.command-help/1.0",
        "catalog_schema": CATALOG_SCHEMA,
        "query": requested,
        "status": "ok",
        "categories": _help_categories(),
        "compat_categories": _help_compat_categories(),
        "tasks": [],
        "matches": [],
    }
    if not requested:
        result["mode"] = "tasks"
        result["tasks"] = default_tasks()
    elif requested in CANONICAL_COMMANDS or requested in AUTOLOADED_COMPAT_COMMANDS:
        result["mode"] = "category"
        if requested in CANONICAL_COMMANDS:
            result["categories"] = _help_categories(requested)
        else:
            result["compat_categories"] = _help_compat_categories(requested)
        result["matches"] = [category_help(requested)]
    else:
        exact = exact_help(requested)
        if exact is not None:
            result["mode"] = exact["kind"]
            result["matches"] = [exact]
            match_category = exact.get("category", "")
            if match_category in CANONICAL_COMMANDS:
                result["categories"] = _help_categories(match_category)
            elif match_category in AUTOLOADED_COMPAT_COMMANDS:
                result["compat_categories"] = _help_compat_categories(match_category)
        else:
            result["mode"] = "search"
            result["matches"] = search_help(requested)
            if not result["matches"]:
                result["status"] = "no_match"
    _print_help_result(result)
    return result


def show_structure_annotator_from_toolkit():
    """Load the optional annotation UI only when explicitly requested."""
    module = importlib.import_module("pymolplugins.annotation.plugin")
    return module.show_structure_annotator_panel()


def show_batch_processing_from_toolkit():
    """Open file/CSV pair processing without mutating the existing scene."""
    module = importlib.import_module("pymolplugins.targets.batch_widgets")
    return module.show_batch_dialog()


def __init_plugin__(app=None):
    from pymol.plugins import addmenuitemqt

    addmenuitemqt("PymolPlugins: Load Toolkit", pp_load)
    addmenuitemqt("PymolPlugins: Doctor", pp_doctor)
    addmenuitemqt("PymolPlugins: Capabilities", pp_capabilities)
    addmenuitemqt("PymolPlugins: Command Help", pp_help)
    addmenuitemqt("PymolPlugins: Structure Annotator", show_structure_annotator_from_toolkit)
    addmenuitemqt("PymolPlugins: Batch Processing", show_batch_processing_from_toolkit)


def _register_host_command(name, function) -> None:
    actual = _registered_callback(name)
    actual_module = str(_callback_description(actual).get("module", ""))
    if actual is None or _same_callback(actual, function) or actual_module == __name__:
        extend_command(cmd, name, function)


_register_host_command("pp_load", pp_load)
_register_host_command("pp_doctor", pp_doctor)
_register_host_command("pp_capabilities", pp_capabilities)
_register_host_command("pp_help", pp_help)

try:
    cmd.auto_arg[0]["pp_help"] = [
        lambda: cmd.Shortcut(help_topics()),
        "task_command_or_preset",
        "",
    ]
except (AttributeError, KeyError, TypeError):
    # Headless/fake command APIs and some older Host builds expose no completion.
    pass

# A plugin-manager load should make canonical commands immediately available.
pp_load()
