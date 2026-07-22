"""Lazy, license-respecting PyRosetta metrics backend.

This module never downloads PyRosetta.  It activates only when a licensed
PyRosetta installation is already importable in the worker environment.
"""

import hashlib
import math
import os
import threading
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


SUPPORTED_METRICS = {
    "cms",
    "bunsat_bound_heavy",
    "bunsat_interface_delta",
    "interface_dsasa",
    "interface_dg",
    "shape_complementarity",
    "packstat",
}

REQUEST_FIELDS = frozenset(
    {"schema_version", "structure", "partners", "metrics", "options"}
)
STRUCTURE_FIELDS = frozenset({"format", "text"})
PARTNER_FIELDS = frozenset({"target_chains", "binder_chains"})
OPTION_FIELDS = frozenset(
    {
        "use_rosetta_radii",
        "quick",
        "distance_weight",
        "filtered_area",
        "probe_radius",
        "burial_cutoff",
        "max_hbond_energy",
    }
)
BOOLEAN_OPTIONS = frozenset({"use_rosetta_radii", "quick"})
NUMERIC_OPTIONS = OPTION_FIELDS.difference(BOOLEAN_OPTIONS)

_UNRESOLVED_DEFAULT_NOTE = (
    "The option was not supplied, and this repository does not read a stable "
    "default getter across PyRosetta versions; the installed Rosetta component "
    "default is used."
)


class RosettaBackendError(RuntimeError):
    """Typed backend failure suitable for an HTTP error response."""


class RosettaBackend:
    """Initialize PyRosetta once and evaluate requests serially."""

    def __init__(self, init_flags: Optional[str] = None):
        self.init_flags = init_flags or os.environ.get(
            "PYMOLPLUGINS_PYROSETTA_FLAGS",
            "-mute all -ignore_unrecognized_res false -load_PDB_components false",
        )
        self._pyrosetta = None
        self._import_error = None
        self._initialized = False
        self._lock = threading.Lock()
        try:
            import pyrosetta

            self._pyrosetta = pyrosetta
        except Exception as exc:  # Import can fail on a binary/CPU mismatch too.
            self._import_error = exc

    def health(self) -> Dict[str, object]:
        version = None
        rosetta_version = None
        if self._pyrosetta is not None:
            version = str(getattr(self._pyrosetta, "__version__", "unknown"))
            try:
                rosetta_version = str(
                    self._pyrosetta.rosetta.utility.Version.version()
                )
            except Exception:
                rosetta_version = "unknown"
        return {
            "available": self._pyrosetta is not None,
            "initialized": self._initialized,
            "pyrosetta_version": version,
            "rosetta_version": rosetta_version,
            "init_flags": self.init_flags if self._initialized else None,
            "error": str(self._import_error) if self._import_error else None,
            "license_note": (
                "PyRosetta is separately licensed and is not distributed by this repository."
            ),
        }

    def capabilities(self) -> Dict[str, object]:
        return {
            "backend": "pyrosetta",
            "metrics": sorted(SUPPORTED_METRICS),
            "structure_formats": ["pdb"],
            "single_worker": True,
            "health": self.health(),
        }

    @staticmethod
    def _allowed_mapping(
        value: object,
        name: str,
        allowed: frozenset,
        required: Sequence[str] = (),
    ) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise RosettaBackendError(f"{name} must be an object")
        non_string = [key for key in value if not isinstance(key, str)]
        if non_string:
            raise RosettaBackendError(f"{name} field names must be strings")
        unknown = sorted(set(value).difference(allowed))
        if unknown:
            raise RosettaBackendError(
                f"unknown {name} fields: {', '.join(unknown)}"
            )
        missing = [field for field in required if field not in value]
        if missing:
            raise RosettaBackendError(
                f"missing {name} fields: {', '.join(missing)}"
            )
        return value

    @staticmethod
    def _validate_options(value: object) -> Dict[str, object]:
        options = RosettaBackend._allowed_mapping(
            value, "options", OPTION_FIELDS
        )
        normalized: Dict[str, object] = {}
        for name, raw in options.items():
            if name in BOOLEAN_OPTIONS:
                if not isinstance(raw, bool):
                    raise RosettaBackendError(f"options.{name} must be a boolean")
                normalized[name] = raw
                continue
            if name in NUMERIC_OPTIONS:
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    raise RosettaBackendError(f"options.{name} must be a number")
                numeric = float(raw)
                if not math.isfinite(numeric):
                    raise RosettaBackendError(f"options.{name} must be finite")
                if name == "probe_radius" and numeric <= 0.0:
                    raise RosettaBackendError("options.probe_radius must be > 0")
                if name in {
                    "distance_weight",
                    "filtered_area",
                    "burial_cutoff",
                } and numeric < 0.0:
                    raise RosettaBackendError(f"options.{name} must be >= 0")
                normalized[name] = numeric
        return normalized

    @classmethod
    def _parse_structure(cls, value: object) -> str:
        structure = cls._allowed_mapping(
            value,
            "structure",
            STRUCTURE_FIELDS,
            required=("format", "text"),
        )
        structure_format = structure.get("format")
        if not isinstance(structure_format, str) or structure_format.lower() != "pdb":
            raise RosettaBackendError("only structure.format='pdb' is supported")
        text = structure.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RosettaBackendError("structure.text must contain PDB text")
        return text

    @classmethod
    def _parse_partners(cls, value: object) -> Tuple[List[str], List[str]]:
        partners = cls._allowed_mapping(
            value,
            "partners",
            PARTNER_FIELDS,
            required=("target_chains", "binder_chains"),
        )
        target = cls._chains(partners.get("target_chains"), "target_chains")
        binder = cls._chains(partners.get("binder_chains"), "binder_chains")
        overlap = sorted(set(target).intersection(binder))
        if overlap:
            raise RosettaBackendError(
                "target_chains and binder_chains must be disjoint; overlap: "
                + ", ".join(overlap)
            )
        return target, binder

    @staticmethod
    def _parse_metrics(value: object) -> List[str]:
        if not isinstance(value, list) or not value:
            raise RosettaBackendError("metrics must be a non-empty list")
        if any(not isinstance(item, str) or not item for item in value):
            raise RosettaBackendError("every metrics entry must be a non-empty string")
        requested = list(value)
        if len(set(requested)) != len(requested):
            raise RosettaBackendError("metrics must not contain duplicates")
        unsupported = sorted(set(requested).difference(SUPPORTED_METRICS))
        if unsupported:
            raise RosettaBackendError("unsupported metrics: " + ", ".join(unsupported))
        return requested

    def _parse_request(
        self, request: Mapping[str, object]
    ) -> Tuple[str, List[str], List[str], List[str], Dict[str, object]]:
        request = self._allowed_mapping(
            request,
            "request",
            REQUEST_FIELDS,
            required=("schema_version", "structure", "partners", "metrics"),
        )
        schema_version = request.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != 1
        ):
            raise RosettaBackendError("schema_version must be the integer 1")

        text = self._parse_structure(request.get("structure"))
        target_chains, binder_chains = self._parse_partners(request.get("partners"))
        requested = self._parse_metrics(request.get("metrics"))
        options = self._validate_options(request.get("options", {}))
        return text, target_chains, binder_chains, requested, options

    @staticmethod
    def _integer_count(value: object, name: str, nonnegative: bool) -> int:
        if isinstance(value, bool):
            raise RosettaBackendError(f"{name} returned a boolean, not a count")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise RosettaBackendError(f"{name} returned a non-numeric count") from exc
        if not math.isfinite(numeric) or not numeric.is_integer():
            raise RosettaBackendError(
                f"{name} returned a non-integral count: {value!r}"
            )
        result = int(numeric)
        if nonnegative and result < 0:
            raise RosettaBackendError(f"{name} returned a negative count: {result}")
        return result

    @staticmethod
    def _pose_chains(pose) -> List[str]:
        try:
            pdb_info = pose.pdb_info()
            if pdb_info is None:
                raise RosettaBackendError("imported pose has no PDBInfo")
            chains = []
            for index in range(1, int(pose.total_residue()) + 1):
                raw = pdb_info.chain(index)
                chain = str(raw).strip()
                if chain not in chains:
                    chains.append(chain)
            return chains
        except RosettaBackendError:
            raise
        except Exception as exc:
            raise RosettaBackendError(
                f"failed to inspect chains in imported pose: {exc}"
            ) from exc

    @classmethod
    def _validate_pose_partners(
        cls, pose, target_chains: Sequence[str], binder_chains: Sequence[str]
    ) -> List[str]:
        overlap = sorted(set(target_chains).intersection(binder_chains))
        if overlap:
            raise RosettaBackendError(
                "target_chains and binder_chains must be disjoint; overlap: "
                + ", ".join(overlap)
            )
        pose_chains = cls._pose_chains(pose)
        available = set(pose_chains)
        missing_target = [chain for chain in target_chains if chain not in available]
        missing_binder = [chain for chain in binder_chains if chain not in available]
        if missing_target or missing_binder:
            details = []
            if missing_target:
                details.append("missing target chains: " + ", ".join(missing_target))
            if missing_binder:
                details.append("missing binder chains: " + ", ".join(missing_binder))
            display = [chain if chain else "<blank>" for chain in pose_chains]
            details.append("pose chains: " + (", ".join(display) or "<none>"))
            raise RosettaBackendError("; ".join(details))
        return pose_chains

    @staticmethod
    def _resolved_option(
        options: Mapping[str, object], name: str, service_default: object
    ) -> Dict[str, object]:
        if name in options:
            return {
                "value": options[name],
                "requested_value": options[name],
                "default_value": service_default,
                "effective_value": options[name],
                "source": "request",
                "resolved": True,
            }
        return {
            "value": service_default,
            "requested_value": None,
            "default_value": service_default,
            "effective_value": service_default,
            "source": "service_default",
            "resolved": True,
        }

    @staticmethod
    def _resolved_rosetta_default(
        options: Mapping[str, object], name: str
    ) -> Dict[str, object]:
        if name in options:
            return {
                "value": options[name],
                "requested_value": options[name],
                "default_value": None,
                "effective_value": options[name],
                "source": "request",
                "resolved": True,
                "note": (
                    "The request overrides this option. The installed Rosetta "
                    "default is not queried through an unstable cross-version getter."
                ),
            }
        return {
            "value": None,
            "requested_value": None,
            "default_value": None,
            "effective_value": None,
            "source": "installed_rosetta_default",
            "resolved": False,
            "note": _UNRESOLVED_DEFAULT_NOTE,
        }

    @staticmethod
    def _enforced_option(value: object) -> Dict[str, object]:
        return {
            "value": value,
            "requested_value": None,
            "default_value": value,
            "effective_value": value,
            "source": "service_enforced",
            "resolved": True,
        }

    @classmethod
    def _resolved_options_contract(
        cls, requested: Sequence[str], options: Mapping[str, object]
    ) -> Dict[str, object]:
        resolved: Dict[str, object] = {}
        if "cms" in requested:
            resolved["cms"] = {
                "use_rosetta_radii": cls._resolved_option(
                    options, "use_rosetta_radii", True
                ),
                "quick": cls._resolved_option(options, "quick", False),
            }
        if "bunsat_bound_heavy" in requested:
            resolved["bunsat_bound_heavy"] = {
                "report_all_heavy_atom_unsats": cls._enforced_option(True),
                "ignore_backbone_heavy_unsats": cls._enforced_option(False),
            }
        return resolved

    @classmethod
    def _cutoff_contract(
        cls, requested: Sequence[str], options: Mapping[str, object]
    ) -> Dict[str, object]:
        cutoffs: Dict[str, object] = {}
        if "cms" in requested:
            cutoffs["cms"] = {
                "distance_weight": cls._resolved_rosetta_default(
                    options, "distance_weight"
                ),
                "filtered_area": cls._resolved_rosetta_default(
                    options, "filtered_area"
                ),
            }
        if "bunsat_bound_heavy" in requested:
            cutoffs["bunsat_bound_heavy"] = {
                name: cls._resolved_rosetta_default(options, name)
                for name in ("probe_radius", "burial_cutoff", "max_hbond_energy")
            }
        if "bunsat_interface_delta" in requested:
            cutoffs["bunsat_interface_delta"] = {
                "interface_analyzer_defaults": {
                    "value": None,
                    "requested_value": None,
                    "default_value": None,
                    "effective_value": None,
                    "source": "installed_rosetta_default",
                    "resolved": False,
                    "note": (
                        "No interface-unsat cutoff is explicitly configured, and "
                        "InterfaceAnalyzerMover exposes no stable cross-version "
                        "getter used by this backend."
                    ),
                }
            }
        return cutoffs

    @staticmethod
    def _selector_contract(
        target_chains: Sequence[str],
        binder_chains: Sequence[str],
        pose_chains: Sequence[str],
    ) -> Dict[str, object]:
        interface = "{}_{}".format("".join(target_chains), "".join(binder_chains))
        return {
            "target": {
                "type": "ChainSelector",
                "chains": list(target_chains),
                "expression": ",".join(target_chains),
            },
            "binder": {
                "type": "ChainSelector",
                "chains": list(binder_chains),
                "expression": ",".join(binder_chains),
            },
            "interface": {"type": "DockingPartners", "expression": interface},
            "pose_chains": list(pose_chains),
        }

    @staticmethod
    def _scorefunction_contract() -> Dict[str, object]:
        return {
            "name": None,
            "weights": None,
            "source": "rosetta_component_internal",
            "note": (
                "This backend does not construct or inject one explicit "
                "scorefunction. The selected Rosetta filters/mover use their "
                "installed-build internals, which do not expose one stable "
                "cross-version name/weights contract here."
            ),
        }

    @classmethod
    def _provenance_contract(
        cls,
        target_chains: Sequence[str],
        binder_chains: Sequence[str],
        pose_chains: Sequence[str],
        requested: Sequence[str],
        options: Mapping[str, object],
    ) -> Dict[str, object]:
        return {
            "selectors": cls._selector_contract(
                target_chains, binder_chains, pose_chains
            ),
            "options": dict(options),
            "resolved_options": cls._resolved_options_contract(requested, options),
            "cutoffs": cls._cutoff_contract(requested, options),
            "scorefunction": cls._scorefunction_contract(),
        }

    def _initialize(self):
        if self._pyrosetta is None:
            detail = str(self._import_error) if self._import_error else "not installed"
            raise RosettaBackendError(f"PyRosetta is unavailable: {detail}")
        if not self._initialized:
            try:
                self._pyrosetta.init(self.init_flags)
            except Exception as exc:
                raise RosettaBackendError(
                    f"PyRosetta initialization failed: {exc}"
                ) from exc
            self._initialized = True
        return self._pyrosetta

    @staticmethod
    def _chains(value: object, name: str) -> List[str]:
        if not isinstance(value, list) or not value:
            raise RosettaBackendError(f"partners.{name} must be a non-empty list")
        chains = []
        for raw in value:
            if not isinstance(raw, str):
                raise RosettaBackendError(
                    f"every partners.{name} entry must be a string"
                )
            chain = raw.strip()
            if len(chain) != 1 or not chain.isalnum():
                raise RosettaBackendError(
                    f"PDB chain '{chain}' in partners.{name} must be one alphanumeric character"
                )
            if chain in chains:
                raise RosettaBackendError(
                    f"partners.{name} must not contain duplicate chain '{chain}'"
                )
            chains.append(chain)
        return chains

    def _pose_from_text(self, pyrosetta, text: str):
        try:
            pose = pyrosetta.Pose()
            pyrosetta.rosetta.core.import_pose.pose_from_pdbstring(pose, text)
            if pose.total_residue() == 0:
                raise RosettaBackendError("PDB text produced an empty pose")
            return pose
        except RosettaBackendError:
            raise
        except Exception as exc:
            raise RosettaBackendError(f"failed to import PDB text: {exc}") from exc

    @staticmethod
    def _selector(pyrosetta, chains: Sequence[str]):
        selector_class = pyrosetta.rosetta.core.select.residue_selector.ChainSelector
        return selector_class(",".join(chains))

    def _cms(self, pyrosetta, pose, target_chains, binder_chains, options):
        try:
            filter_class = (
                pyrosetta.rosetta.protocols.simple_filters.ContactMolecularSurfaceFilter
            )
            cms_filter = filter_class()
            cms_filter.selector1(self._selector(pyrosetta, target_chains))
            cms_filter.selector2(self._selector(pyrosetta, binder_chains))
            cms_filter.use_rosetta_radii(options.get("use_rosetta_radii", True))
            cms_filter.quick(options.get("quick", False))
            if "distance_weight" in options:
                cms_filter.distance_weight(float(options["distance_weight"]))
            if "filtered_area" in options:
                cms_filter.filtered_area(float(options["filtered_area"]))
            return float(cms_filter.compute(pose))
        except Exception as exc:
            raise RosettaBackendError(f"ContactMolecularSurface failed: {exc}") from exc

    def _bound_bunsat(self, pyrosetta, pose, options):
        try:
            filter_class = pyrosetta.rosetta.protocols.simple_filters.BuriedUnsatHbondFilter
            bunsat_filter = filter_class()
            bunsat_filter.set_report_all_heavy_atom_unsats(True)
            bunsat_filter.set_ignore_bb_heavy_unsats(False)
            if "probe_radius" in options:
                bunsat_filter.set_probe_radius(float(options["probe_radius"]))
            if "burial_cutoff" in options:
                bunsat_filter.set_burial_cutoff(float(options["burial_cutoff"]))
            if "max_hbond_energy" in options:
                bunsat_filter.set_max_hbond_energy(float(options["max_hbond_energy"]))
            return self._integer_count(
                bunsat_filter.compute(pose),
                "BuriedUnsatHbondFilter",
                nonnegative=True,
            )
        except Exception as exc:
            raise RosettaBackendError(f"bound buried-unsat calculation failed: {exc}") from exc

    @staticmethod
    def _interface_analyzer(pyrosetta, pose, target_chains, binder_chains, requested):
        interface = "{}_{!s}".format("".join(target_chains), "".join(binder_chains))
        mover_class = pyrosetta.rosetta.protocols.analysis.InterfaceAnalyzerMover
        try:
            mover = mover_class(interface)
        except Exception:
            partners_class = pyrosetta.rosetta.core.pose.DockingPartners
            partners = partners_class()
            partners.docking_partners_from_string(interface)
            mover = mover_class(partners)
        mover.set_compute_interface_delta_hbond_unsat(
            "bunsat_interface_delta" in requested
        )
        mover.set_calc_dSASA("interface_dsasa" in requested)
        mover.set_compute_interface_energy("interface_dg" in requested)
        mover.set_compute_interface_sc("shape_complementarity" in requested)
        mover.set_compute_packstat("packstat" in requested)
        working_pose = pose.clone()
        mover.apply(working_pose)
        values = {}
        if "bunsat_interface_delta" in requested:
            values["bunsat_interface_delta"] = RosettaBackend._integer_count(
                mover.get_interface_delta_hbond_unsat(),
                "InterfaceAnalyzerMover interface-delta buried unsats",
                nonnegative=False,
            )
        if "interface_dsasa" in requested:
            values["interface_dsasa"] = float(mover.get_interface_delta_sasa())
        if "interface_dg" in requested:
            values["interface_dg"] = float(mover.get_interface_dG())
        if "shape_complementarity" in requested:
            values["shape_complementarity"] = float(
                mover.get_all_data().sc_value
            )
        if "packstat" in requested:
            values["packstat"] = float(mover.get_interface_packstat())
        return values

    def analyze(self, request: Mapping[str, object]) -> Dict[str, object]:
        (
            text,
            target_chains,
            binder_chains,
            requested,
            options,
        ) = self._parse_request(request)
        with self._lock:
            pyrosetta = self._initialize()
            pose = self._pose_from_text(pyrosetta, text)
            pose_chains = self._validate_pose_partners(
                pose, target_chains, binder_chains
            )
            values: Dict[str, object] = {}
            if "cms" in requested:
                values["cms"] = self._cms(
                    pyrosetta, pose, target_chains, binder_chains, options
                )
            if "bunsat_bound_heavy" in requested:
                values["bunsat_bound_heavy"] = self._bound_bunsat(
                    pyrosetta, pose, options
                )
            interface_metrics = set(requested).intersection(
                {
                    "bunsat_interface_delta",
                    "interface_dsasa",
                    "interface_dg",
                    "shape_complementarity",
                    "packstat",
                }
            )
            if interface_metrics:
                try:
                    values.update(
                        self._interface_analyzer(
                            pyrosetta,
                            pose,
                            target_chains,
                            binder_chains,
                            interface_metrics,
                        )
                    )
                except Exception as exc:
                    if isinstance(exc, RosettaBackendError):
                        raise
                    raise RosettaBackendError(
                        f"InterfaceAnalyzerMover failed: {exc}"
                    ) from exc
            health = self.health()
            contract = self._provenance_contract(
                target_chains,
                binder_chains,
                pose_chains,
                requested,
                options,
            )
            return {
                "schema": "pymolplugins.rosetta_metrics/1.0",
                "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "partners": {
                    "target_chains": target_chains,
                    "binder_chains": binder_chains,
                },
                "metrics": values,
                "provenance": {
                    "backend": "pyrosetta",
                    "pyrosetta_version": health["pyrosetta_version"],
                    "rosetta_version": health["rosetta_version"],
                    "init_flags": self.init_flags,
                    **contract,
                },
                "warnings": [],
            }
