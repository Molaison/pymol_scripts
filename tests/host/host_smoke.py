"""Windows Host PyMOL 2.x integration smoke harness.

Run this file from the PyMOL console with ``run <path>/host_smoke.py``.  The
harness creates uniquely named in-memory fixtures, exercises the canonical
plugin APIs, and writes a versioned JSON record.  Checks which require a real
mouse, a callback-owned CGO, or an interactive Qt event loop are deliberately
recorded as manual; they are never converted into automatic passes.
"""

from __future__ import annotations

import datetime
import hashlib
import importlib
import importlib.util
import json
import math
import os
import platform
import sys
import time
import traceback
import uuid
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from pymol import cmd
from pymol.cgo import COLOR, SPHERE


SCHEMA = "pymolplugins.host-smoke/1.0"
HARNESS_VERSION = "1.1.0"
PREFIX_ROOT = "pphs"
TRANSLATION = (1.25, -2.0, 0.5)
MATRIX_MODES = (0, 1, 2)


class SmokeFailure(AssertionError):
    """A deterministic smoke assertion with JSON-safe diagnostic details."""

    def __init__(self, message: str, details: Optional[Mapping[str, Any]] = None):
        super().__init__(message)
        self.details = dict(details or {})


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _script_path() -> str:
    for key in ("__script__", "__file__"):
        value = globals().get(key)
        if value:
            return os.path.abspath(str(value))
    raise RuntimeError("PyMOL did not expose the host_smoke.py script path")


SCRIPT_PATH = _script_path()
REPOSITORY_ROOT = os.path.abspath(os.path.join(os.path.dirname(SCRIPT_PATH), "..", ".."))
if REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, REPOSITORY_ROOT)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("%s must be a boolean token" % name)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return repr(value)


def _require(condition: bool, message: str, details: Optional[Mapping[str, Any]] = None) -> None:
    if not condition:
        raise SmokeFailure(message, details)


def _names(enabled_only: bool = False) -> List[str]:
    return list(cmd.get_names("all", int(enabled_only)) or [])


def _setting_float(name: str) -> float:
    try:
        return float(cmd.get_setting_float(name))
    except AttributeError:
        return float(cmd.get_setting_text(name))


def _setting_tuple(name: str) -> Tuple[float, ...]:
    try:
        value = cmd.get_setting_tuple(name)
        # PyMOL's legacy API returns ``(setting_type, (x, y, z))``.
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and isinstance(value[1], (list, tuple))
        ):
            setting_type = int(value[0])
            value = value[1]
            if setting_type == 5:
                # Color settings contain one encoded integer, not an RGB
                # vector. Resolve their text form through the color table.
                rgb = cmd.get_color_tuple(cmd.get_setting_text(name))
                if rgb is None:
                    raise ValueError("Unable to resolve color setting %s" % name)
                value = rgb
    except AttributeError:
        value = cmd.get_setting_text(name).strip("[]() ").split(",")
    return tuple(float(item) for item in value)


def _close(left: Sequence[float], right: Sequence[float], tolerance: float = 1.0e-6) -> bool:
    return len(left) == len(right) and all(
        abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right)
    )


def _pdb_atom(
    record: str,
    serial: int,
    atom_name: str,
    resn: str,
    chain: str,
    resi: int,
    xyz: Sequence[float],
    element: str,
    insertion: str = "",
    charge: str = "",
) -> str:
    x, y, z = (float(value) for value in xyz)
    return (
        f"{record:<6}{serial:>5} {atom_name:<4} {resn:>3} {chain[:1]:1}"
        f"{resi:>4}{insertion[:1]:1}   {x:>8.3f}{y:>8.3f}{z:>8.3f}"
        f"{1.0:>6.2f}{20.0:>6.2f}          {element:>2}{charge:>2}"
    )


def _ligand_complex_pdb() -> str:
    atoms = (
        ("ATOM", 1, "N", "LYS", "A", 1, (-1.5, 0.0, 0.0), "N", ""),
        ("ATOM", 2, "CA", "LYS", "A", 1, (-0.8, 0.0, 0.0), "C", ""),
        ("ATOM", 3, "CE", "LYS", "A", 1, (-0.3, 0.0, 0.0), "C", ""),
        ("ATOM", 4, "NZ", "LYS", "A", 1, (0.0, 0.0, 0.0), "N", "1+"),
        ("ATOM", 5, "N", "ASP", "B", 2, (5.2, 0.0, 0.0), "N", ""),
        ("ATOM", 6, "CG", "ASP", "B", 2, (4.3, 0.0, 0.0), "C", ""),
        ("ATOM", 7, "OD1", "ASP", "B", 2, (3.6, 0.0, 0.0), "O", "1-"),
        ("ATOM", 8, "OD2", "ASP", "B", 2, (4.3, -0.8, 0.0), "O", "1-"),
        ("HETATM", 9, "C1", "LIG", "X", 501, (2.0, 2.5, 0.0), "C", ""),
        ("HETATM", 10, "N1", "LIG", "X", 501, (3.6, 2.5, 0.0), "N", "1+"),
        ("HETATM", 11, "O1", "LIG", "X", 501, (0.0, 2.5, 0.0), "O", "1-"),
        ("HETATM", 12, "C1", "DRG", "", 502, (20.0, 0.0, 0.0), "C", ""),
        ("HETATM", 13, "O1", "DRG", "", 502, (21.2, 0.0, 0.0), "O", ""),
        ("HETATM", 14, "ZN", "ZN", "Z", 601, (10.0, 0.0, 0.0), "ZN", "2+"),
        ("HETATM", 15, "O", "HOH", "W", 1, (0.0, 0.0, 5.0), "O", ""),
    )
    lines = [
        _pdb_atom(*row[:-1], charge=row[-1])
        for row in atoms
    ]
    lines.extend(("CONECT    9   10   11", "CONECT   12   13", "END"))
    return "\n".join(lines) + "\n"


def _oligomer_pdb(order: int, radius: float = 8.0) -> str:
    lines: List[str] = []
    serial = 1
    for subunit in range(order):
        angle = (2.0 * math.pi * subunit) / float(order)
        radial = (math.cos(angle), math.sin(angle), 0.0)
        tangent = (-math.sin(angle), math.cos(angle), 0.0)
        center = (radius * radial[0], radius * radial[1], 0.0)
        chain = chr(ord("A") + subunit)
        # Three non-collinear, equivalently rotated CA positions are required
        # for the paired C2 rigid-fit path as well as centroid-based C3/C6.
        offsets = (
            (1, -0.35, -0.60, -0.30),
            (2, 0.45, 0.15, 0.35),
            (3, -0.10, 0.65, -0.15),
        )
        for resi, radial_offset, tangent_offset, z_offset in offsets:
            xyz = (
                center[0] + radial_offset * radial[0] + tangent_offset * tangent[0],
                center[1] + radial_offset * radial[1] + tangent_offset * tangent[1],
                z_offset,
            )
            lines.append(
                _pdb_atom("ATOM", serial, "CA", "ALA", chain, resi, xyz, "C")
            )
            serial += 1
    lines.append("END")
    return "\n".join(lines) + "\n"


class HostSmokeSuite:
    """Own uniquely named fixtures and accumulate a versioned smoke record."""

    def __init__(self, output_path: str, keep_fixtures: bool):
        self.started = _utc_now()
        self.prefix = "%s_%s_" % (PREFIX_ROOT, uuid.uuid4().hex[:8])
        self.output_path = output_path
        self.keep_fixtures = keep_fixtures
        self.fixtures: Dict[str, str] = {}
        self.checks: List[Dict[str, Any]] = []
        self.owned_names: Set[str] = set()

    def name(self, role: str) -> str:
        value = self.prefix + role
        self.fixtures[role] = value
        self.owned_names.add(value)
        return value

    def run_check(self, check_id: str, title: str, operation: Callable[[], Any]) -> None:
        started = time.perf_counter()
        record: Dict[str, Any] = {
            "id": check_id,
            "title": title,
            "required": True,
        }
        try:
            record["details"] = _json_safe(operation() or {})
            record["status"] = "pass"
        except SmokeFailure as exc:
            record["status"] = "fail"
            record["error"] = str(exc)
            record["details"] = _json_safe(exc.details)
        except Exception as exc:
            record["status"] = "error"
            record["error"] = "%s: %s" % (type(exc).__name__, exc)
            record["traceback"] = traceback.format_exc()
        record["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        self.checks.append(record)
        print("HOST SMOKE %-5s %s - %s" % (record["status"].upper(), check_id, title))

    def cleanup(self) -> Dict[str, Any]:
        if self.keep_fixtures:
            return {"status": "kept", "prefix": self.prefix}
        errors = []
        existing = [name for name in _names() if name.startswith(self.prefix)]
        non_groups = [name for name in existing if cmd.get_type(name) != "object:group"]
        groups = [name for name in existing if cmd.get_type(name) == "object:group"]
        for name in non_groups + list(reversed(groups)):
            try:
                cmd.delete(name)
            except Exception as exc:
                errors.append("%s: %s" % (name, exc))
        return {
            "status": "pass" if not errors else "error",
            "deleted": existing,
            "errors": errors,
        }

    def result(self, cleanup: Mapping[str, Any]) -> Dict[str, Any]:
        required = [item for item in self.checks if item["required"]]
        automatic_status = "pass" if required and all(
            item["status"] == "pass" for item in required
        ) else "fail"
        if cleanup.get("status") == "error":
            automatic_status = "fail"
        follow_record = next(
            (item for item in self.checks if item["id"] == "HOST-FOLLOW-01"),
            {},
        )
        matrix_modes = dict(
            (follow_record.get("details") or {}).get("matrix_modes", {})
        )
        return {
            "schema": SCHEMA,
            "harness_version": HARNESS_VERSION,
            "started_at_utc": self.started,
            "completed_at_utc": _utc_now(),
            "automatic_status": automatic_status,
            "overall_status": (
                "automatic_pass_manual_pending"
                if automatic_status == "pass"
                else "automatic_fail"
            ),
            "environment": _environment_record(),
            "repository_root": REPOSITORY_ROOT,
            "script_path": SCRIPT_PATH,
            "output_path": self.output_path,
            "fixture_prefix": self.prefix,
            "fixtures": dict(sorted(self.fixtures.items())),
            "checks": self.checks,
            "matrix_mode_records": matrix_modes,
            "manual_checks": _manual_checks(self.fixtures),
            "cleanup": _json_safe(cleanup),
        }


def _environment_record() -> Dict[str, Any]:
    try:
        pymol_version = list(cmd.get_version())
    except Exception as exc:
        pymol_version = ["unavailable", str(exc)]
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "sys_platform": sys.platform,
        "os_name": os.name,
        "pymol_version": _json_safe(pymol_version),
        "cwd": os.getcwd(),
        "cwd_is_unc": os.getcwd().startswith("\\\\"),
    }


def _load_plugin_entry(suite: HostSmokeSuite) -> Dict[str, Any]:
    _require(os.name == "nt" and sys.platform == "win32", "smoke must run in Windows Host PyMOL")
    _require(not os.getcwd().startswith("\\\\"), "PyMOL working directory must be local, not UNC")
    entry_path = os.path.join(REPOSITORY_ROOT, "plugins", "pymol_plugins.py")
    module_name = "_pymolplugins_host_entry_%s" % suite.prefix.strip("_")
    spec = importlib.util.spec_from_file_location(module_name, entry_path)
    _require(spec is not None and spec.loader is not None, "cannot load plugin entry", {"path": entry_path})
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    loaded = module.pp_load()
    check_worker = _env_bool("PP_HOST_SMOKE_CHECK_WORKER", False)
    doctor = module.pp_doctor(check_worker=int(check_worker))
    _require(not loaded.get("errors"), "canonical plugin modules failed to load", loaded)
    _require(not doctor.get("missing_required_api"), "required Host PyMOL APIs are missing", doctor)
    _require(
        not doctor.get("missing_commands"),
        "canonical command manifest is incomplete in Host PyMOL",
        doctor,
    )
    keyword = getattr(cmd, "keyword", {})
    unadapted = {}
    for name in sorted((doctor.get("commands") or {}).keys()):
        entry = keyword.get(name) if isinstance(keyword, dict) else None
        callback = entry[0] if isinstance(entry, (list, tuple)) and entry else None
        if not getattr(callback, "_pymolplugins_console_adapter", False):
            unadapted[name] = {
                "entry_type": type(entry).__name__ if entry is not None else None,
                "callback": repr(callback),
            }
    _require(
        not unadapted,
        "canonical commands bypassed the STRICT-parser console adapter",
        unadapted,
    )
    if str(doctor.get("repository_root", "")).lower().startswith(
        ("\\\\wsl.localhost\\", "\\\\wsl$\\")
    ):
        _require(
            bool(doctor.get("wsl_distro")),
            "doctor did not infer the WSL distro from the UNC repository path",
            doctor,
        )
    if check_worker:
        worker = doctor.get("worker") or {}
        _require(
            worker.get("schema") == "pymolplugins.worker/1.0"
            and worker.get("status") == "ok",
            "Windows Host PyMOL could not reach the configured WSL worker",
            {"worker": worker, "worker_url": doctor.get("worker_url")},
        )
    return {
        "plugin_status": loaded.get("status"),
        "loaded_modules": loaded.get("loaded", []),
        "doctor_schema": doctor.get("schema"),
        "pymol_version": doctor.get("pymol_version"),
        "api": doctor.get("api"),
        "canonical_command_count": len(doctor.get("commands") or {}),
        "console_adapted_command_count": len(doctor.get("commands") or {}),
        "missing_commands": doctor.get("missing_commands"),
        "repository_root_is_unc": doctor.get("repository_root_is_unc"),
        "wsl_distro": doctor.get("wsl_distro"),
        "worker_checked": check_worker,
        "worker": doctor.get("worker") if check_worker else None,
    }


def _load_cgo(name: str, position: Sequence[float], color: Sequence[float]) -> None:
    x, y, z = (float(value) for value in position)
    red, green, blue = (float(value) for value in color)
    cmd.load_cgo([COLOR, red, green, blue, SPHERE, x, y, z, 0.4], name, state=1)


def _create_core_fixtures(suite: HostSmokeSuite) -> None:
    driver = suite.name("driver")
    other = suite.name("other")
    extra = suite.name("root_a_extra")
    cmd.pseudoatom(driver, name="CA", resn="GLY", resi="1", chain="D", elem="C", pos=[0, 0, 0])
    cmd.pseudoatom(other, name="CA", resn="GLY", resi="1", chain="E", elem="C", pos=[4, 0, 0])
    cmd.pseudoatom(extra, name="CA", resn="ALA", resi="1", chain="F", elem="C", pos=[0, 4, 0])

    static_cgo = suite.name("static_cgo")
    overlay = suite.name("unmanaged_overlay")
    _load_cgo(static_cgo, (0.0, 0.0, 1.0), (1.0, 0.2, 0.2))
    _load_cgo(overlay, (0.0, 0.0, 4.0), (0.2, 1.0, 0.2))

    map_name = suite.name("map")
    cmd.map_new(map_name, "gaussian", 1.0, driver, 2.0, state=1, quiet=1)
    measurement = suite.name("measurement")
    cmd.distance(measurement, driver, other, cutoff=100.0, mode=0, quiet=1)

    multistate = suite.name("multistate")
    cmd.create(multistate, driver, 1, 1, quiet=1)
    cmd.create(multistate, driver, 1, 2, quiet=1)
    cmd.translate([0.0, 0.0, 1.0], multistate, state=2, camera=0)


def _create_structure_fixtures(suite: HostSmokeSuite) -> None:
    ligand_complex = suite.name("ligand_complex")
    cmd.read_pdbstr(_ligand_complex_pdb(), ligand_complex)
    style_target = suite.name("style_target")
    cmd.create(style_target, ligand_complex, 1, 1, quiet=1)
    for order in (2, 3, 6):
        name = suite.name("c%d" % order)
        cmd.read_pdbstr(_oligomer_pdb(order), name)


def _create_groups(suite: HostSmokeSuite) -> None:
    nested = suite.name("nested_group")
    root_a = suite.name("root_group_a")
    root_b = suite.name("root_group_b")
    cmd.group(nested, "")
    for role in ("driver", "static_cgo", "map", "measurement", "multistate"):
        cmd.group(nested, suite.fixtures[role])
    cmd.group(root_a, "")
    cmd.group(root_a, nested)
    cmd.group(root_a, suite.fixtures["root_a_extra"])
    cmd.group(root_b, "")
    cmd.group(root_b, suite.fixtures["other"])


def _create_sentinels(suite: HostSmokeSuite) -> None:
    sentinel = suite.name("interaction_sentinel")
    prefix_neighbor = suite.name("interaction_run_neighbor")
    cmd.pseudoatom(sentinel, pos=[30, 0, 0], name="PS1", elem="C")
    cmd.pseudoatom(prefix_neighbor, pos=[31, 0, 0], name="PS1", elem="C")


def _fixture_inventory(suite: HostSmokeSuite) -> Dict[str, Any]:
    _create_core_fixtures(suite)
    _create_structure_fixtures(suite)
    _create_groups(suite)
    _create_sentinels(suite)
    expected_types = {
        "driver": "object:molecule",
        "static_cgo": "object:cgo",
        "map": "object:map",
        "measurement": "object:measurement",
        "multistate": "object:molecule",
        "nested_group": "object:group",
        "root_group_a": "object:group",
        "root_group_b": "object:group",
    }
    actual_types = {role: cmd.get_type(suite.fixtures[role]) for role in expected_types}
    _require(actual_types == expected_types, "fixture object types differ from expectations", actual_types)
    states = int(cmd.count_states(suite.fixtures["multistate"]))
    _require(states == 2, "multistate fixture does not have two states", {"states": states})
    symmetry_chains = {
        str(order): list(cmd.get_chains(suite.fixtures["c%d" % order]))
        for order in (2, 3, 6)
    }
    for order in (2, 3, 6):
        _require(
            len(symmetry_chains[str(order)]) == order,
            "C%d fixture has the wrong chain count" % order,
            symmetry_chains,
        )
    source = suite.fixtures["ligand_complex"]
    ligand = "(%s) and resn LIG and chain X and resi 501" % source
    blank_ligand = "(%s) and resn DRG" % source
    ligand_counts = {
        "explicit_ligand": int(cmd.count_atoms(ligand)),
        "blank_chain_ligand": int(cmd.count_atoms(blank_ligand)),
        "organic_total": int(cmd.count_atoms("(%s) and organic" % source)),
        "solvent": int(cmd.count_atoms("(%s) and solvent" % source)),
        "metals": int(cmd.count_atoms("(%s) and metals" % source)),
    }
    _require(ligand_counts["explicit_ligand"] > 0, "explicit ligand fixture is empty", ligand_counts)
    _require(ligand_counts["blank_chain_ligand"] > 0, "blank-chain ligand fixture is empty", ligand_counts)
    _require(
        ligand_counts["organic_total"] > ligand_counts["explicit_ligand"],
        "ligand edge fixture needs multiple organic residues",
        ligand_counts,
    )
    return {
        "types": actual_types,
        "multistate_states": states,
        "symmetry_chains": symmetry_chains,
        "ligand_counts": ligand_counts,
    }


def _enabled_assertions(
    enabled: Iterable[str], required: Iterable[str], forbidden: Iterable[str]
) -> Dict[str, Any]:
    enabled_set = set(enabled)
    missing = sorted(set(required) - enabled_set)
    unexpected = sorted(set(forbidden) & enabled_set)
    _require(not missing and not unexpected, "enabled-object set is incorrect", {
        "missing": missing,
        "unexpected": unexpected,
        "enabled": sorted(enabled_set),
    })
    return {"enabled": sorted(enabled_set)}


def _navigation_check(suite: HostSmokeSuite) -> Dict[str, Any]:
    navigation = importlib.import_module("pymolplugins.navigation.commands")
    navigation.pp_nav_stop()
    root_a = suite.fixtures["root_group_a"]
    root_b = suite.fixtures["root_group_b"]
    overlay = suite.fixtures["unmanaged_overlay"]
    group_a_leaves = [
        suite.fixtures[role]
        for role in ("driver", "static_cgo", "map", "measurement", "multistate", "root_a_extra")
    ]
    group_b_leaves = [suite.fixtures["other"]]
    for name in group_a_leaves + group_b_leaves + [overlay]:
        cmd.enable(name)
    snapshots: Dict[str, Any] = {}
    try:
        start_command = (
            'pp_nav_start source=groups, entries="%s,%s", batch_size=2, '
            'window_size=1, stride=1, wrap=0, bind_keys=0, zoom_policy=none'
            % (root_a, root_b)
        )
        cmd.do(start_command)
        started = navigation.pp_nav_status(quiet=1)
        expected_entry_ids = ["group:%s" % root_a, "group:%s" % root_b]
        _require(
            started.get("source") == "groups"
            and started.get("count") == 2
            and started.get("position") == 1
            and started.get("current_entry_ids") == expected_entry_ids[:1]
            and started.get("visible_entry_ids") == expected_entry_ids[:1],
            "quoted console entries did not create the expected group session",
            {"command": start_command, "status": started, "expected": expected_entry_ids},
        )
        _require(set(started.get("visible_names", [])) == set(group_a_leaves), "root group was not atomic", started)
        snapshots["console_start"] = {
            "command": start_command,
            "entry_ids": expected_entry_ids,
            "status": _json_safe(started),
        }
        snapshots["root_a"] = _enabled_assertions(_names(True), group_a_leaves + [overlay], group_b_leaves)

        pinned = navigation.pp_nav_pin(root_a)
        _require("error" not in pinned, "group pin failed", pinned)
        advanced = navigation.pp_nav_next()
        _require(advanced.get("position") == 2, "navigation did not advance to root B", advanced)
        snapshots["pinned_a_plus_b"] = _enabled_assertions(
            _names(True), group_a_leaves + group_b_leaves + [overlay], []
        )

        unpinned = navigation.pp_nav_unpin(root_a)
        _require("error" not in unpinned, "group unpin failed", unpinned)
        snapshots["root_b"] = _enabled_assertions(_names(True), group_b_leaves + [overlay], group_a_leaves)
    finally:
        stopped = navigation.pp_nav_stop()
        snapshots["stop"] = _json_safe(stopped)
    _enabled_assertions(_names(True), group_a_leaves + group_b_leaves + [overlay], [])
    return {
        "root_a_leaves": group_a_leaves,
        "root_b_leaves": group_b_leaves,
        "snapshots": snapshots,
    }


def _target_iteration_check(suite: HostSmokeSuite) -> Dict[str, Any]:
    targets = importlib.import_module("pymolplugins.targets.commands")
    driver = suite.fixtures["driver"]
    fuzzy_query = driver.replace("driver", "drivr")
    fuzzy = targets.pp_target_resolve(
        fuzzy_query,
        match="fuzzy",
        kinds="object",
        fuzzy_cutoff=0.7,
        limit=1,
        group_mode="atomic",
        quiet=1,
    )
    fuzzy_names = [value["name"] for value in fuzzy.get("matches", [])]
    _require(fuzzy_names == [driver], "fuzzy object target resolved incorrectly", fuzzy)

    root = suite.fixtures["root_group_a"]
    overlay = suite.fixtures["unmanaged_overlay"]
    leaves = [
        suite.fixtures[role]
        for role in (
            "driver",
            "static_cgo",
            "map",
            "measurement",
            "multistate",
            "root_a_extra",
        )
    ]
    for name in leaves + [overlay]:
        cmd.enable(name)
    preview = targets.pp_foreach(
        root,
        "disable {target}",
        match="exact",
        group_mode="leaves",
        commit=0,
        quiet=1,
    )
    _require(preview.get("status") == "preview", "target iteration preview failed", preview)
    _require(
        {item["target"] for item in preview.get("commands", [])} == set(leaves),
        "group leaf expansion omitted a non-molecule object",
        preview,
    )
    _require(set(leaves + [overlay]).issubset(set(_names(True))), "preview mutated the scene", preview)

    names_before_commit = set(_names())
    console_command = (
        'pp_foreach %s, "disable {target}", match=exact, '
        "group_mode=leaves, commit=1, quiet=1" % root
    )
    cmd.do(console_command)
    enabled = set(_names(True))
    _require(
        not (set(leaves) & enabled),
        "quoted console foreach did not disable every recursive group leaf",
        {"command": console_command, "enabled": sorted(enabled)},
    )
    _require(
        overlay in enabled,
        "quoted console foreach changed an unrelated object",
        {"command": console_command, "enabled": sorted(enabled)},
    )
    _require(
        set(_names()) == names_before_commit
        and not any(name.startswith("ppfe_ack_") for name in _names()),
        "quoted console foreach left a parser completion marker",
        {"before": sorted(names_before_commit), "after": sorted(_names())},
    )
    for name in leaves:
        cmd.enable(name)

    quoted_glob = suite.prefix + "root_a_*"
    quoted_glob_command = (
        'pp_foreach "%s", "disable {target}", match=glob, kinds=object, '
        "commit=1, quiet=1" % quoted_glob
    )
    cmd.do(quoted_glob_command)
    enabled = set(_names(True))
    _require(
        suite.fixtures["root_a_extra"] not in enabled
        and set(leaves).difference({suite.fixtures["root_a_extra"]}).issubset(enabled)
        and overlay in enabled,
        "quoted glob target did not resolve to the expected object",
        {"command": quoted_glob_command, "enabled": sorted(enabled)},
    )
    _require(
        set(_names()) == names_before_commit
        and not any(name.startswith("ppfe_ack_") for name in _names()),
        "quoted glob foreach left a parser completion marker",
        {"before": sorted(names_before_commit), "after": sorted(_names())},
    )
    cmd.enable(suite.fixtures["root_a_extra"])

    names_before_errors = set(_names())
    parser_error = targets.pp_foreach(
        root,
        "pp_foreach_command_does_not_exist {target}",
        match="exact",
        group_mode="leaves",
        commit=1,
        on_error="stop",
        quiet=1,
    )
    _require(
        parser_error.get("status") == "partial_error"
        and not parser_error.get("executed_targets")
        and len(parser_error.get("errors", [])) == 1,
        "target iteration did not stop on a swallowed parser error",
        parser_error,
    )
    command_errors = targets.pp_foreach(
        root,
        "color pp_foreach_color_does_not_exist, {target}",
        match="exact",
        group_mode="leaves",
        commit=1,
        on_error="continue",
        quiet=1,
    )
    _require(
        command_errors.get("status") == "partial_error"
        and not command_errors.get("executed_targets")
        and len(command_errors.get("errors", [])) == len(leaves),
        "target iteration did not continue across swallowed CmdException errors",
        command_errors,
    )
    _require(
        set(_names()) == names_before_errors,
        "target iteration left parser completion markers in the scene",
        {
            "before": sorted(names_before_errors),
            "after": sorted(_names()),
        },
    )
    return {
        "fuzzy_query": fuzzy_query,
        "fuzzy_match": fuzzy.get("matches", []),
        "group": root,
        "expanded_targets": preview.get("expanded_targets", preview.get("commands", [])),
        "command_count": len(leaves),
        "console_commands": {
            "quoted_group": console_command,
            "quoted_glob": quoted_glob_command,
        },
        "parser_error_policy": {
            "stop_error_count": len(parser_error.get("errors", [])),
            "continue_error_count": len(command_errors.get("errors", [])),
            "completion_check": "same-line parser sentinel",
        },
    }


def _set_homogeneous_ttt(name: str, matrix: Sequence[float], math3d: Any) -> Tuple[float, ...]:
    cmd.set_object_ttt(name, list(math3d.homogeneous_to_ttt(matrix)), quiet=1)
    value = cmd.get_object_ttt(name, quiet=1)
    _require(value is not None, "Host did not retain an explicit object TTT", {"name": name})
    return tuple(float(item) for item in value)


def _current_ttt(name: str) -> Tuple[float, ...]:
    value = cmd.get_object_ttt(name, quiet=1)
    return tuple(float(item) for item in (value or ()))


def _alignment_commit_undo(
    alignment: Any,
    preview: Mapping[str, Any],
    payload: str,
    baseline: Sequence[float],
) -> Dict[str, Any]:
    _require(
        preview.get("schema") == "pymolplugins.alignment/1.0"
        and not preview.get("committed")
        and float(preview.get("rmsd_after", 1.0)) <= 1.0e-5,
        "alignment preview failed",
        preview,
    )
    _require(_close(_current_ttt(payload), baseline), "alignment preview mutated its payload", preview)
    committed = alignment.pp_align_commit(preview.get("id", ""))
    _require(
        committed.get("committed") and not _close(_current_ttt(payload), baseline),
        "alignment commit did not move its payload",
        committed,
    )
    undone = alignment.pp_align_undo(preview.get("id", ""))
    _require(
        undone.get("schema") == "pymolplugins.alignment/1.0"
        and _close(_current_ttt(payload), baseline),
        "alignment undo did not restore its payload",
        undone,
    )
    return {
        "id": preview.get("id"),
        "kind": preview.get("kind"),
        "point_count": preview.get("point_count"),
        "rmsd_before": preview.get("rmsd_before"),
        "rmsd_after": preview.get("rmsd_after"),
    }


def _sequence_alignment_check(suite: HostSmokeSuite) -> Dict[str, Any]:
    sequence = importlib.import_module("pymolplugins.alignment.sequence")
    alignment = importlib.import_module("pymolplugins.alignment.rigid")
    math3d = importlib.import_module("pymolplugins.core.math3d")
    source = suite.fixtures["ligand_complex"]
    selection_name = suite.name("sequence_selection")
    selected = sequence.pp_select_sequence(
        source, "K", selection_name=selection_name, chain="A", state=1, visualize=0
    )
    _require(
        selected.get("ok") and selected.get("match_count") == 1
        and selected.get("selected_atom_count") == 4
        and cmd.count_atoms(selection_name) == 4,
        "Host sequence selection did not preserve exact residue atom identity",
        selected,
    )

    target = suite.fixtures["c3"]
    mobile = suite.name("alignment_mobile")
    cmd.create(mobile, target, 1, 1, quiet=1)
    baseline = _set_homogeneous_ttt(mobile, _translation_h(), math3d)
    anchor = alignment.pp_align_anchor(
        "%s and name CA" % mobile,
        "%s and name CA" % target,
        move=mobile,
        match="identifier",
        state=1,
        commit=0,
    )
    anchor_summary = _alignment_commit_undo(alignment, anchor, mobile, baseline)

    motif = alignment.pp_align_motif(
        mobile, target, "AAA", mobile_chain="A", target_chain="A",
        atoms="CA", move=mobile, occurrence="unique", state=1, commit=0,
    )
    motif_summary = _alignment_commit_undo(alignment, motif, mobile, baseline)

    key_target = suite.name("ligand_key_target")
    key_mobile = suite.name("ligand_key_mobile")
    payload = suite.name("ligand_payload")
    key_atoms = (
        ("C1", "C", (0.0, 0.0, 0.0)),
        ("C2", "C", (1.5, 0.0, 0.0)),
        ("N1", "N", (0.2, 1.3, 0.0)),
        ("O1", "O", (0.1, 0.2, 1.1)),
    )
    for atom_name, element, position in key_atoms:
        cmd.pseudoatom(
            key_target, name=atom_name, resn="KEY", resi="1", chain="K",
            elem=element, pos=list(position), state=1,
        )
    cmd.create(key_mobile, key_target, 1, 1, quiet=1)
    cmd.create(payload, target, 1, 1, quiet=1)
    key_baseline = _set_homogeneous_ttt(key_mobile, _translation_h(), math3d)
    payload_baseline = _set_homogeneous_ttt(payload, _translation_h(), math3d)
    ligand = alignment.pp_align_ligand(
        key_mobile, key_target, mobile_ligand="all", target_ligand="all",
        match="name", move=payload, state=1, commit=0,
    )
    ligand_summary = _alignment_commit_undo(
        alignment, ligand, payload, payload_baseline
    )
    _require(
        _close(_current_ttt(key_mobile), key_baseline),
        "detached ligand key object was moved with the payload",
        ligand,
    )
    return {
        "sequence": {
            "selection": selection_name,
            "match_count": selected.get("match_count"),
            "selected_atom_count": selected.get("selected_atom_count"),
        },
        "anchor": anchor_summary,
        "motif": motif_summary,
        "ligand_key_payload": ligand_summary,
        "detached_key_preserved": True,
    }


def _orientation_cycle(
    geometry: Any,
    operation: Callable[..., Mapping[str, Any]],
    object_name: str,
    baseline: Sequence[float],
    parameters: Mapping[str, Any],
) -> Dict[str, Any]:
    preview = operation(commit=0, **parameters)
    _require(
        preview.get("schema") == "pymolplugins.orientation/1.0"
        and not preview.get("committed")
        and _close(_current_ttt(object_name), baseline),
        "orientation preview failed or mutated its object",
        preview,
    )
    committed = operation(commit=1, **parameters)
    _require(
        committed.get("committed") and not _close(_current_ttt(object_name), baseline),
        "orientation commit did not change the object TTT",
        committed,
    )
    undone = geometry.pp_orientation_undo()
    _require(
        undone.get("status") == "restored" and _close(_current_ttt(object_name), baseline),
        "orientation undo did not restore the object TTT",
        undone,
    )
    return {
        "axis_direction": committed.get("axis", {}).get("direction"),
        "move_objects": committed.get("move_objects"),
        "undo": undone,
    }


def _orientation_commit_check(suite: HostSmokeSuite) -> Dict[str, Any]:
    geometry = importlib.import_module("pymolplugins.geometry.commands")
    math3d = importlib.import_module("pymolplugins.core.math3d")
    pca_object = suite.name("pca_orientation")
    for resi, position in enumerate(((-6.0, 0.0, 0.0), (0.0, 0.2, 0.1), (6.0, -0.1, 0.2)), start=1):
        cmd.pseudoatom(
            pca_object, name="CA", resn="ALA", resi=str(resi), chain="P",
            elem="C", pos=list(position), state=1,
        )
    pca_baseline = _set_homogeneous_ttt(pca_object, _identity_h(), math3d)
    pca = _orientation_cycle(
        geometry,
        geometry.pp_orient_pca,
        pca_object,
        pca_baseline,
        {
            "selection": pca_object,
            "move": pca_object,
            "target_axis": "z",
            "atoms": "ca",
            "axis": "long",
            "center": 1,
            "min_confidence": 0.0,
            "force": 1,
            "state": 1,
        },
    )

    cyclic_object = suite.name("cyclic_orientation")
    cmd.create(cyclic_object, suite.fixtures["c3"], 1, 1, quiet=1)
    cyclic_baseline = _set_homogeneous_ttt(cyclic_object, _identity_h(), math3d)
    cyclic = _orientation_cycle(
        geometry,
        geometry.pp_orient_cyclic,
        cyclic_object,
        cyclic_baseline,
        {
            "selection": cyclic_object,
            "move": cyclic_object,
            "target_axis": "x",
            "center": 1,
            "min_confidence": 0.0,
            "force": 1,
            "state": 1,
        },
    )
    return {"pca": pca, "cyclic": cyclic}


def _cyclic_geometry_check(suite: HostSmokeSuite) -> Dict[str, Any]:
    orientation = importlib.import_module("pymolplugins.geometry.commands")
    results: Dict[str, Any] = {}
    for order in (2, 3, 6):
        selection = suite.fixtures["c%d" % order]
        axis_name = suite.prefix + "axis_c%d" % order
        result = orientation.pp_axis_cyclic(
            selection=selection,
            subunits="",
            order=order,
            name=axis_name,
            state=1,
            draw=0,
        )
        _require(result.get("schema") == "pymolplugins.axis/1.0", "C%d axis failed" % order, result)
        diagnostics = result.get("diagnostics", {})
        _require(int(diagnostics.get("order", 0)) == order, "C%d order was not recovered" % order, result)
        _require(_close(result.get("direction", ()), (0.0, 0.0, 1.0), tolerance=1.0e-4), "C%d axis is not z-aligned" % order, result)
        results[str(order)] = result
    vector_name = suite.prefix + "axis_vector"
    vector_command = (
        'pp_axis_vector vector="0,0,-2", center="1,2,3", name=%s, draw=0'
        % vector_name
    )
    cmd.do(vector_command)
    vector_record = orientation._AXES.get(vector_name)
    vector = vector_record.to_dict() if vector_record is not None else {}
    _require(
        vector.get("schema") == "pymolplugins.axis/1.0"
        and _close(vector.get("direction", ()), (0.0, 0.0, 1.0))
        and _close(vector.get("center", ()), (1.0, 2.0, 3.0)),
        "quoted console vector axis failed",
        {"command": vector_command, "result": vector},
    )
    two_points = orientation.pp_axis_two_points(
        points="0,0,-5,0,0,5",
        name=suite.prefix + "axis_two_points",
        draw=0,
    )
    _require(
        two_points.get("schema") == "pymolplugins.axis/1.0"
        and _close(two_points.get("direction", ()), (0.0, 0.0, 1.0))
        and _close(two_points.get("center", ()), (0.0, 0.0, 0.0)),
        "two-point axis failed",
        two_points,
    )
    mass_pca = orientation.pp_axis_pca(
        selection=suite.fixtures["c6"],
        atoms="heavy",
        weighting="mass",
        name=suite.prefix + "axis_mass_pca",
        draw=0,
    )
    _require(
        mass_pca.get("schema") == "pymolplugins.axis/1.0"
        and mass_pca.get("diagnostics", {}).get("weighting") == "mass",
        "mass-weighted PCA failed",
        mass_pca,
    )
    membrane_name = suite.prefix + "membrane_vector"
    membrane = orientation.pp_membrane_slab(
        selection=suite.fixtures["c6"],
        axis_source="vector",
        axis="0,0,1",
        thickness=30.0,
        name=membrane_name,
    )
    planes = membrane.get("plane_centers", ())
    separation = (
        math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(planes[0], planes[1])))
        if len(planes) == 2
        else -1.0
    )
    _require(
        membrane.get("schema") == "pymolplugins.membrane/1.0"
        and abs(separation - 30.0) <= 1.0e-5
        and cmd.get_type(membrane_name + "__planes") == "object:cgo",
        "vector membrane slab failed",
        {"result": membrane, "separation": separation},
    )
    return {
        "cyclic_axes": results,
        "vector_axis": {"command": vector_command, "result": vector},
        "two_point_axis": two_points,
        "mass_weighted_pca": mass_pca,
        "vector_membrane": membrane,
        "draw_requested": True,
    }


def _identity_h() -> Tuple[float, ...]:
    return (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def _translation_h() -> Tuple[float, ...]:
    tx, ty, tz = TRANSLATION
    return (
        1.0, 0.0, 0.0, tx,
        0.0, 1.0, 0.0, ty,
        0.0, 0.0, 1.0, tz,
        0.0, 0.0, 0.0, 1.0,
    )


def _matrix_summary(name: str, math3d: Any) -> Dict[str, Any]:
    raw_ttt = cmd.get_object_ttt(name, quiet=1)
    homogeneous = math3d.ttt_to_homogeneous(raw_ttt)
    try:
        object_matrix = cmd.get_object_matrix(name, state=1)
    except TypeError:
        object_matrix = cmd.get_object_matrix(name, 1)
    except Exception as exc:
        object_matrix = {"error": str(exc)}
    return {
        "type": cmd.get_type(name),
        "ttt_is_none": raw_ttt is None,
        "homogeneous": list(homogeneous),
        "translation": [homogeneous[3], homogeneous[7], homogeneous[11]],
        "object_matrix": _json_safe(object_matrix),
    }


def _one_follow_mode(
    suite: HostSmokeSuite, mode: int, follow: Any, math3d: Any
) -> Dict[str, Any]:
    driver = suite.fixtures["driver"]
    followers = [
        suite.fixtures[role]
        for role in ("static_cgo", "map", "measurement", "multistate", "root_a_extra")
    ]
    identity_ttt = list(math3d.homogeneous_to_ttt(_identity_h()))
    for name in [driver] + followers:
        cmd.set_object_ttt(name, identity_ttt, quiet=1)
    follow.pp_follow_stop()
    cmd.set("matrix_mode", mode)
    arm = follow.pp_follow_arm(
        driver,
        group=suite.fixtures["root_group_a"],
        cgo_policy="static",
        allow_external_changes=0,
    )
    _require(arm.get("status") == "armed", "follow arm failed", arm)
    _require(set(arm.get("followers", [])) == set(followers), "follow did not resolve every group leaf", {
        "expected": followers,
        "actual": arm.get("followers", []),
        "arm": arm,
    })
    cmd.set_object_ttt(
        driver,
        list(math3d.homogeneous_to_ttt(_translation_h())),
        quiet=1,
    )
    applied = follow.pp_follow_apply()
    _require(applied.get("status") == "applied", "follow apply failed", applied)
    after_apply = {name: _matrix_summary(name, math3d) for name in [driver] + followers}
    for name, summary in after_apply.items():
        _require(_close(summary["translation"], TRANSLATION), "follow translation mismatch", {
            "object": name,
            "mode": mode,
            "summary": summary,
        })
    undo = follow.pp_follow_undo()
    _require(undo.get("status") == "restored", "follow undo failed", undo)
    after_undo = {name: _matrix_summary(name, math3d) for name in [driver] + followers}
    for name, summary in after_undo.items():
        _require(_close(summary["translation"], (0.0, 0.0, 0.0)), "follow undo mismatch", {
            "object": name,
            "mode": mode,
            "summary": summary,
        })
    return {
        "mode": mode,
        "arm": arm,
        "apply": applied,
        "undo": undo,
        "after_apply": after_apply,
        "after_undo": after_undo,
        "static_cgo_verified": suite.fixtures["static_cgo"] in followers,
        "movement_source": "programmatic set_object_ttt (not mouse)",
    }


def _follow_check(suite: HostSmokeSuite) -> Dict[str, Any]:
    follow = importlib.import_module("pymolplugins.follow.commands")
    math3d = importlib.import_module("utilities.math3d")
    original_mode = cmd.get_setting_text("matrix_mode")
    modes: Dict[str, Any] = {}
    failures = []
    try:
        for mode in MATRIX_MODES:
            try:
                modes[str(mode)] = _one_follow_mode(suite, mode, follow, math3d)
                modes[str(mode)]["status"] = "pass"
            except Exception as exc:
                modes[str(mode)] = {
                    "status": "fail",
                    "error": "%s: %s" % (type(exc).__name__, exc),
                    "details": _json_safe(getattr(exc, "details", {})),
                    "traceback": traceback.format_exc(),
                }
                failures.append(mode)
            finally:
                follow.pp_follow_stop()
    finally:
        cmd.set("matrix_mode", original_mode)
    if failures:
        raise SmokeFailure("follow failed for matrix_mode values %s" % failures, {"matrix_modes": modes})
    return {"matrix_modes": modes}


def _atom_state(selection: str) -> List[List[Any]]:
    rows: List[Tuple[Any, ...]] = []
    try:
        cmd.iterate(
            selection,
            "_rows.append((model, index, color, reps))",
            space={"_rows": rows},
        )
    except Exception:
        rows = []
        cmd.iterate(
            selection,
            "_rows.append((model, index, color))",
            space={"_rows": rows},
        )
    return [list(row) for row in sorted(rows)]


def _style_check(suite: HostSmokeSuite) -> Dict[str, Any]:
    styles = importlib.import_module("pymolplugins.styles.registry")
    target = suite.fixtures["style_target"]
    user_ambient = _setting_float("ambient")
    user_background = _setting_tuple("bg_rgb")
    snapshot_id = None
    try:
        cmd.set("ambient", 0.37)
        cmd.set("bg_rgb", [0.11, 0.22, 0.33])
        cmd.hide("everything", target)
        cmd.show("sticks", target)
        cmd.color("magenta", target)
        baseline = {
            "ambient": _setting_float("ambient"),
            "bg_rgb": list(_setting_tuple("bg_rgb")),
            "atoms": _atom_state(target),
        }
        snapshot_id = styles.pp_style_apply(
            "minimal_light+macaron_default", target, snapshot=1, render=0
        )
        _require(snapshot_id is not None, "style apply did not create a snapshot")
        palette = styles._REGISTRY.resolve("macaron_default")[0].colors
        registered_colors = {
            name: list(cmd.get_color_tuple(name) or ()) for name in palette
        }
        _require(
            all(len(rgb) == 3 for rgb in registered_colors.values()),
            "style custom colors were not registered",
            {"registered_colors": registered_colors},
        )
        applied = {
            "ambient": _setting_float("ambient"),
            "bg_rgb": list(_setting_tuple("bg_rgb")),
            "atoms": _atom_state(target),
        }
        reverted_id = styles.pp_style_revert(snapshot_id)
        snapshot_id = None
        reverted = {
            "ambient": _setting_float("ambient"),
            "bg_rgb": list(_setting_tuple("bg_rgb")),
            "atoms": _atom_state(target),
        }
        _require(reverted_id is not None, "style revert returned no snapshot")
        _require(applied != baseline, "style apply produced no observable change", {"baseline": baseline, "applied": applied})
        _require(
            [row[2] for row in applied["atoms"]]
            != [row[2] for row in baseline["atoms"]],
            "style palette did not change atom colors",
            {"baseline": baseline, "applied": applied},
        )
        _require(_close((reverted["ambient"],), (baseline["ambient"],)), "ambient was not restored", {"baseline": baseline, "reverted": reverted})
        _require(_close(reverted["bg_rgb"], baseline["bg_rgb"]), "background was not restored", {"baseline": baseline, "reverted": reverted})
        _require(reverted["atoms"] == baseline["atoms"], "atom colors/representations were not restored", {"baseline": baseline, "reverted": reverted})
        return {
            "preset": "minimal_light+macaron_default",
            "snapshot_id": reverted_id,
            "baseline": baseline,
            "applied": applied,
            "reverted": reverted,
            "registered_colors": registered_colors,
            "render_requested": False,
        }
    finally:
        if snapshot_id is not None:
            try:
                styles.pp_style_revert(snapshot_id)
            except Exception:
                traceback.print_exc()
        cmd.set("ambient", user_ambient)
        cmd.set("bg_rgb", list(user_background))


def _assert_exact_clear(
    controller: Any,
    record: Any,
    sentinels: Sequence[str],
    source: str,
) -> Dict[str, Any]:
    manifest = record.manifest
    created = list(manifest.created_names)
    before = set(_names())
    _require(set(created).issubset(before), "manifest outputs were not all created", {
        "created": created,
        "missing": sorted(set(created) - before),
    })
    cleared = controller.clear(manifest.root_group)
    after = set(_names())
    _require(not (set(created) & after), "exact clear left manifest outputs behind", {
        "remaining": sorted(set(created) & after),
        "manifest": cleared.to_dict(),
    })
    _require(source in after, "interaction clear deleted its source object")
    _require(set(sentinels).issubset(after), "interaction clear deleted unowned sentinel names", {
        "sentinels": sentinels,
        "remaining": sorted(after),
    })
    return {"manifest": cleared.to_dict(), "created_names": created}


def _interaction_check(suite: HostSmokeSuite) -> Dict[str, Any]:
    interactions = importlib.import_module("pymolplugins.interactions.controller")
    controller = interactions.get_controller()
    source = suite.fixtures["ligand_complex"]
    sentinels = [
        suite.fixtures["interaction_sentinel"],
        suite.fixtures["interaction_run_neighbor"],
    ]
    interchain_group = suite.name("interaction_run_interchain")
    ligand_group = suite.name("interaction_run_ligand")

    interchain = controller.run(
        "interchain_basic",
        source,
        partner1="chain A",
        partner2="chain B",
        state=1,
        group=interchain_group,
        replace=False,
    )
    interchain_count = len(interchain.result.interactions)
    _require(interchain_count > 0, "deterministic inter-chain fixture produced no interactions", interchain.result.to_dict())
    interchain_clear = _assert_exact_clear(controller, interchain, sentinels, source)

    ligand_selection = "resn LIG and chain X and resi 501"
    ligand = controller.run(
        "ligand_site",
        source,
        partner2=ligand_selection,
        state=1,
        group=ligand_group,
        replace=False,
        parameters={"surface": False, "labels": False},
    )
    expected_ligand_atoms = int(cmd.count_atoms("(%s) and (%s)" % (source, ligand_selection)))
    ligand_partner = next(partner for partner in ligand.result.partners if partner.role == "ligand")
    all_organic = int(cmd.count_atoms("(%s) and organic" % source))
    _require(ligand_partner.atom_count == expected_ligand_atoms, "ligand selection escaped its explicit boundary", {
        "expected": expected_ligand_atoms,
        "actual": ligand_partner.atom_count,
        "selection": ligand_partner.selection,
    })
    _require(ligand_partner.atom_count < all_organic, "ligand edge fixture did not exclude the second organic residue", {
        "ligand": ligand_partner.atom_count,
        "all_organic": all_organic,
    })
    ligand_clear = _assert_exact_clear(controller, ligand, sentinels, source)
    return {
        "interchain_interaction_count": interchain_count,
        "interchain_summary": interchain.result.to_dict()["summary"],
        "interchain_clear": interchain_clear,
        "ligand_partner_atom_count": ligand_partner.atom_count,
        "all_organic_atom_count": all_organic,
        "ligand_summary": ligand.result.to_dict()["summary"],
        "ligand_clear": ligand_clear,
    }


def _plip_export_check(suite: HostSmokeSuite) -> Dict[str, Any]:
    adapter = importlib.import_module("pymolplugins.interactions.plip_protocol")
    source = suite.fixtures["ligand_complex"]
    setting_names = (
        "pdb_conect_all",
        "pdb_conect_nodup",
        "pdb_retain_ids",
        "pdb_use_ter_records",
    )
    before = {name: cmd.get_setting_text(name) for name in setting_names}
    names_before = set(_names())
    request = adapter.build_plip_request_from_pymol(
        cmd, source, state=1, preset="plip_overview", run_id="host-export-check"
    )
    after = {name: cmd.get_setting_text(name) for name in setting_names}
    _require(after == before, "PLIP export settings were not restored", {
        "before": before,
        "after": after,
    })
    _require(set(_names()) == names_before, "PLIP export created or deleted PyMOL names")

    atom_map = list(request["atom_map"])
    expected_count = int(cmd.count_atoms(source, state=1))
    _require(len(atom_map) == expected_count, "PLIP atom map count mismatch", {
        "expected": expected_count,
        "actual": len(atom_map),
    })
    serials = [int(entry["export_serial"]) for entry in atom_map]
    identities = [
        (str(entry["object"]), int(entry["source_index"])) for entry in atom_map
    ]
    _require(len(serials) == len(set(serials)), "PLIP export serials are not unique")
    _require(len(identities) == len(set(identities)), "PLIP source identities are not unique")
    _require(
        any(right - left > 1 for left, right in zip(serials, serials[1:])),
        "multi-chain fixture did not record a TER serial gap",
        {"serials": serials},
    )
    ligand_rows = [
        entry
        for entry in atom_map
        if entry["residue"]["resn"] == "LIG"
        and entry["residue"]["chain"] == "X"
        and entry["residue"]["resi"] == "501"
    ]
    blank_chain_rows = [
        entry
        for entry in atom_map
        if entry["residue"]["resn"] == "DRG"
        and entry["residue"]["chain"] == ""
        and entry["residue"]["resi"] == "502"
    ]
    _require(len(ligand_rows) == 3, "explicit ligand identity was not preserved")
    _require(len(blank_chain_rows) == 2, "blank-chain ligand identity was not preserved")
    text = str(request["structure"]["text"])
    return {
        "atom_count": len(atom_map),
        "export_serials": serials,
        "ter_gap_count": sum(
            1 for left, right in zip(serials, serials[1:]) if right - left > 1
        ),
        "explicit_ligand_atoms": len(ligand_rows),
        "blank_chain_ligand_atoms": len(blank_chain_rows),
        "source_sha256": request["source"]["sha256"],
        "pdb_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "settings_restored": True,
        "scene_names_unchanged": True,
    }


def _manual_checks(fixtures: Mapping[str, str]) -> List[Dict[str, Any]]:
    driver = fixtures.get("driver", "<driver fixture unavailable>")
    root = fixtures.get("root_group_a", "<group fixture unavailable>")
    return [
        {
            "id": "HOST-MANUAL-MOUSE-01",
            "status": "manual_not_run",
            "reason": "Programmatic set_object_ttt cannot validate PyMOL mouse-object movement.",
            "prerequisite": "Re-run with PP_HOST_SMOKE_KEEP_FIXTURES=1.",
            "procedure": [
                "pp_follow_arm %s, group=%s" % (driver, root),
                "Move the driver with the real PyMOL mouse object-manipulation mode.",
                "Run pp_follow_apply and visually/numerically verify every sibling follows.",
                "Run pp_follow_undo and verify the baseline is restored.",
            ],
        },
        {
            "id": "HOST-MANUAL-CALLBACK-CGO-01",
            "status": "manual_not_run",
            "reason": "A static CGO cannot represent a callback-owned viewport overlay.",
            "procedure": [
                "Load visualization/overlays/axes_overlay.py or another callback CGO.",
                "Register it as callback-owned, change the view, and verify skip/rebuild policy.",
                "Confirm no timer/view callback overwrites a supposedly applied TTT silently.",
            ],
        },
        {
            "id": "HOST-MANUAL-QT-01",
            "status": "manual_not_run",
            "reason": "Headless batch mode cannot validate interactive Qt timeout/cancel behavior.",
            "procedure": [
                "Open the Host PyMOL Qt interaction/remote dialog.",
                "Exercise a successful request, timeout, and user cancellation.",
                "Verify the UI remains responsive and no partial output names survive failure.",
            ],
        },
    ]


def _default_output_path() -> str:
    configured = os.environ.get("PP_HOST_SMOKE_OUTPUT", "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.abspath("pymolplugins_host_smoke_%s.json" % stamp)


def _write_record(path: str, record: Mapping[str, Any]) -> str:
    directory = os.path.dirname(path) or os.getcwd()
    os.makedirs(directory, exist_ok=True)
    temporary = "%s.tmp.%s" % (path, uuid.uuid4().hex[:8])
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(_json_safe(record), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def pp_host_smoke(output_path: str = "", keep_fixtures: Any = "") -> Dict[str, Any]:
    """Run automatic Host checks and write the versioned JSON record."""
    output = os.path.abspath(os.path.expanduser(str(output_path or _default_output_path())))
    keep = _env_bool("PP_HOST_SMOKE_KEEP_FIXTURES", False) if keep_fixtures in ("", None) else _env_bool_value(keep_fixtures)
    suite = HostSmokeSuite(output, keep)
    try:
        suite.run_check("HOST-LOAD-01", "plugin load and pp_doctor", lambda: _load_plugin_entry(suite))
        suite.run_check("HOST-FIXTURE-01", "nested/multitype and scientific fixtures", lambda: _fixture_inventory(suite))
        suite.run_check("HOST-NAV-01", "atomic nested-group navigation", lambda: _navigation_check(suite))
        suite.run_check("HOST-TARGETS-01", "object/group fuzzy resolution and foreach", lambda: _target_iteration_check(suite))
        suite.run_check("HOST-ALIGNMENT-01", "sequence plus anchor/motif/ligand alignment", lambda: _sequence_alignment_check(suite))
        suite.run_check("HOST-ORIENTATION-01", "PCA/cyclic orientation commit and undo", lambda: _orientation_commit_check(suite))
        suite.run_check("HOST-GEOMETRY-01", "cyclic/PCA/vector axes and membrane slab", lambda: _cyclic_geometry_check(suite))
        suite.run_check("HOST-FOLLOW-01", "follow apply/undo for matrix_mode 0/1/2", lambda: _follow_check(suite))
        suite.run_check("HOST-STYLE-01", "style apply/revert snapshot", lambda: _style_check(suite))
        suite.run_check("HOST-INTERACTION-01", "local interactions and exact manifest clear", lambda: _interaction_check(suite))
        suite.run_check("HOST-PLIP-EXPORT-01", "PLIP PDB atom identity and setting restoration", lambda: _plip_export_check(suite))
    finally:
        cleanup = suite.cleanup()
    record = suite.result(cleanup)
    written = _write_record(output, record)
    print("HOST SMOKE automatic_status=%s" % record["automatic_status"])
    print("HOST SMOKE manual checks remain pending by design")
    print("HOST SMOKE record: %s" % written)
    return record


def _env_bool_value(value: Any) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("keep_fixtures must be a boolean token")


cmd.extend("pp_host_smoke", pp_host_smoke)

if _env_bool("PP_HOST_SMOKE_AUTORUN", True):
    pp_host_smoke()
