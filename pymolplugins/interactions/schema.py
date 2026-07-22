"""Versioned, PyMOL-independent interaction result and output schemas.

The classes in this module deliberately contain no PyMOL imports.  Backends can
therefore produce/validate JSON in WSL, a test runner, or a PyMOL process using
the same data contract.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


INTERACTION_SCHEMA = "pymolplugins.interactions/1.0"
MANIFEST_SCHEMA = "pymolplugins.interactions.manifest/1.0"


class SchemaError(ValueError):
    """Raised when public interaction data does not satisfy its schema."""


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaError("%s must be an object" % field_name)
    return value


def _sequence(value: Any, field_name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SchemaError("%s must be an array" % field_name)
    return value


def _text(value: Any, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SchemaError("%s must be a string" % field_name)
    if not allow_empty and not value:
        raise SchemaError("%s must not be empty" % field_name)
    return value


def _optional_int(value: Any, field_name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError("%s must be an integer or null" % field_name)
    return value


def _json_value(value: Any, field_name: str) -> Any:
    """Return a detached JSON-compatible value or raise a useful error."""

    try:
        return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise SchemaError("%s must be JSON serializable: %s" % (field_name, exc))


@dataclass(frozen=True)
class BackendInfo:
    name: str
    version: str

    def validate(self) -> None:
        _text(self.name, "run.backend.name")
        _text(self.version, "run.backend.version")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {"name": self.name, "version": self.version}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BackendInfo":
        data = _mapping(data, "run.backend")
        value = cls(
            name=_text(data.get("name"), "run.backend.name"),
            version=_text(data.get("version"), "run.backend.version"),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class SourceInfo:
    object: str
    state: int
    sha256: str

    def validate(self) -> None:
        _text(self.object, "run.source.object")
        if isinstance(self.state, bool) or not isinstance(self.state, int) or self.state < 1:
            raise SchemaError("run.source.state must be an integer >= 1")
        digest = _text(self.sha256, "run.source.sha256")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise SchemaError("run.source.sha256 must be a lowercase SHA-256 digest")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {"object": self.object, "state": self.state, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceInfo":
        data = _mapping(data, "run.source")
        state = data.get("state")
        if isinstance(state, bool) or not isinstance(state, int):
            raise SchemaError("run.source.state must be an integer")
        value = cls(
            object=_text(data.get("object"), "run.source.object"),
            state=state,
            sha256=_text(data.get("sha256"), "run.source.sha256"),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class RunInfo:
    id: str
    backend: BackendInfo
    source: SourceInfo
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        _text(self.id, "run.id")
        self.backend.validate()
        self.source.validate()
        _json_value(dict(self.parameters), "run.parameters")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "id": self.id,
            "backend": self.backend.to_dict(),
            "source": self.source.to_dict(),
            "parameters": _json_value(dict(self.parameters), "run.parameters"),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunInfo":
        data = _mapping(data, "run")
        parameters = _mapping(data.get("parameters", {}), "run.parameters")
        value = cls(
            id=_text(data.get("id"), "run.id"),
            backend=BackendInfo.from_dict(data.get("backend")),
            source=SourceInfo.from_dict(data.get("source")),
            parameters=_json_value(dict(parameters), "run.parameters"),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class Partner:
    role: str
    selection: str
    atom_count: int

    def validate(self) -> None:
        _text(self.role, "partners[].role")
        _text(self.selection, "partners[].selection")
        if isinstance(self.atom_count, bool) or not isinstance(self.atom_count, int):
            raise SchemaError("partners[].atom_count must be an integer")
        if self.atom_count < 0:
            raise SchemaError("partners[].atom_count must be >= 0")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "role": self.role,
            "selection": self.selection,
            "atom_count": self.atom_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Partner":
        data = _mapping(data, "partners[]")
        atom_count = data.get("atom_count")
        if isinstance(atom_count, bool) or not isinstance(atom_count, int):
            raise SchemaError("partners[].atom_count must be an integer")
        value = cls(
            role=_text(data.get("role"), "partners[].role"),
            selection=_text(data.get("selection"), "partners[].selection"),
            atom_count=atom_count,
        )
        value.validate()
        return value


@dataclass(frozen=True)
class AtomRef:
    source_index: int
    export_serial: Optional[int]
    name: str
    element: str
    alt: str = ""
    object: str = ""

    def validate(self) -> None:
        if (
            isinstance(self.source_index, bool)
            or not isinstance(self.source_index, int)
            or self.source_index < 1
        ):
            raise SchemaError("participants[].atom.source_index must be an integer >= 1")
        _optional_int(self.export_serial, "participants[].atom.export_serial")
        _text(self.name, "participants[].atom.name")
        _text(self.element, "participants[].atom.element", allow_empty=True)
        _text(self.alt, "participants[].atom.alt", allow_empty=True)
        _text(self.object, "participants[].atom.object", allow_empty=True)

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "source_index": self.source_index,
            "export_serial": self.export_serial,
            "name": self.name,
            "element": self.element,
            "alt": self.alt,
            "object": self.object,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AtomRef":
        data = _mapping(data, "participants[].atom")
        source_index = data.get("source_index")
        if isinstance(source_index, bool) or not isinstance(source_index, int):
            raise SchemaError("participants[].atom.source_index must be an integer")
        value = cls(
            source_index=source_index,
            export_serial=_optional_int(
                data.get("export_serial"), "participants[].atom.export_serial"
            ),
            name=_text(data.get("name"), "participants[].atom.name"),
            element=_text(
                data.get("element", ""),
                "participants[].atom.element",
                allow_empty=True,
            ),
            alt=_text(data.get("alt", ""), "participants[].atom.alt", allow_empty=True),
            object=_text(
                data.get("object", ""),
                "participants[].atom.object",
                allow_empty=True,
            ),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class ResidueRef:
    resn: str
    chain: str
    segi: str
    resi: str
    insertion: str = ""

    def validate(self) -> None:
        _text(self.resn, "participants[].residue.resn", allow_empty=True)
        _text(self.chain, "participants[].residue.chain", allow_empty=True)
        _text(self.segi, "participants[].residue.segi", allow_empty=True)
        # resi is intentionally never coerced to an integer.
        _text(self.resi, "participants[].residue.resi", allow_empty=True)
        _text(self.insertion, "participants[].residue.insertion", allow_empty=True)

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "resn": self.resn,
            "chain": self.chain,
            "segi": self.segi,
            "resi": self.resi,
            "insertion": self.insertion,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResidueRef":
        data = _mapping(data, "participants[].residue")
        value = cls(
            resn=_text(data.get("resn", ""), "participants[].residue.resn", True),
            chain=_text(data.get("chain", ""), "participants[].residue.chain", True),
            segi=_text(data.get("segi", ""), "participants[].residue.segi", True),
            resi=_text(data.get("resi", ""), "participants[].residue.resi", True),
            insertion=_text(
                data.get("insertion", ""),
                "participants[].residue.insertion",
                True,
            ),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class Participant:
    role: str
    atom: AtomRef
    residue: ResidueRef

    def validate(self) -> None:
        _text(self.role, "participants[].role")
        self.atom.validate()
        self.residue.validate()

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "role": self.role,
            "atom": self.atom.to_dict(),
            "residue": self.residue.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Participant":
        data = _mapping(data, "participants[]")
        value = cls(
            role=_text(data.get("role"), "participants[].role"),
            atom=AtomRef.from_dict(data.get("atom")),
            residue=ResidueRef.from_dict(data.get("residue")),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class Geometry:
    distance_angstrom: Optional[float] = None
    angles_degree: Tuple[float, ...] = ()

    def validate(self) -> None:
        if self.distance_angstrom is not None:
            if isinstance(self.distance_angstrom, bool) or not isinstance(
                self.distance_angstrom, (int, float)
            ):
                raise SchemaError("geometry.distance_angstrom must be numeric or null")
            if not math.isfinite(self.distance_angstrom) or self.distance_angstrom < 0:
                raise SchemaError("geometry.distance_angstrom must be finite and >= 0")
        for angle in self.angles_degree:
            if isinstance(angle, bool) or not isinstance(angle, (int, float)):
                raise SchemaError("geometry.angles_degree values must be numeric")
            if not math.isfinite(angle):
                raise SchemaError("geometry.angles_degree values must be finite")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "distance_angstrom": (
                None
                if self.distance_angstrom is None
                else float(self.distance_angstrom)
            ),
            "angles_degree": [float(value) for value in self.angles_degree],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Geometry":
        data = _mapping(data, "geometry")
        distance = data.get("distance_angstrom")
        if distance is not None and (
            isinstance(distance, bool) or not isinstance(distance, (int, float))
        ):
            raise SchemaError("geometry.distance_angstrom must be numeric or null")
        angles = _sequence(data.get("angles_degree", []), "geometry.angles_degree")
        for angle in angles:
            if isinstance(angle, bool) or not isinstance(angle, (int, float)):
                raise SchemaError("geometry.angles_degree values must be numeric")
        value = cls(
            distance_angstrom=None if distance is None else float(distance),
            angles_degree=tuple(float(angle) for angle in angles),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class Interaction:
    id: str
    type: str
    subtype: str
    participants: Tuple[Participant, ...]
    geometry: Geometry = field(default_factory=Geometry)

    def validate(self) -> None:
        _text(self.id, "interactions[].id")
        _text(self.type, "interactions[].type")
        _text(self.subtype, "interactions[].subtype", allow_empty=True)
        if len(self.participants) < 2:
            raise SchemaError("interactions[].participants must contain at least two atoms")
        for participant in self.participants:
            participant.validate()
        self.geometry.validate()

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "id": self.id,
            "type": self.type,
            "subtype": self.subtype,
            "participants": [value.to_dict() for value in self.participants],
            "geometry": self.geometry.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Interaction":
        data = _mapping(data, "interactions[]")
        participants = _sequence(data.get("participants"), "interactions[].participants")
        value = cls(
            id=_text(data.get("id"), "interactions[].id"),
            type=_text(data.get("type"), "interactions[].type"),
            subtype=_text(
                data.get("subtype", ""), "interactions[].subtype", allow_empty=True
            ),
            participants=tuple(Participant.from_dict(item) for item in participants),
            geometry=Geometry.from_dict(data.get("geometry", {})),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class InteractionSite:
    site_id: str
    interactions: Tuple[Interaction, ...] = ()

    def validate(self) -> None:
        _text(self.site_id, "sites[].site_id")
        for interaction in self.interactions:
            interaction.validate()

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "site_id": self.site_id,
            "interactions": [value.to_dict() for value in self.interactions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InteractionSite":
        data = _mapping(data, "sites[]")
        interactions = _sequence(data.get("interactions", []), "sites[].interactions")
        value = cls(
            site_id=_text(data.get("site_id"), "sites[].site_id"),
            interactions=tuple(Interaction.from_dict(item) for item in interactions),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class InteractionResult:
    schema: str
    run: RunInfo
    partners: Tuple[Partner, ...]
    sites: Tuple[InteractionSite, ...]
    summary: Mapping[str, Mapping[str, int]]
    warnings: Tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        run: RunInfo,
        partners: Iterable[Partner],
        sites: Iterable[InteractionSite],
        warnings: Iterable[str] = (),
        provenance: Optional[Mapping[str, Any]] = None,
        raw: Optional[Mapping[str, Any]] = None,
    ) -> "InteractionResult":
        site_values = tuple(sites)
        counts = Counter(
            interaction.type
            for site in site_values
            for interaction in site.interactions
        )
        value = cls(
            schema=INTERACTION_SCHEMA,
            run=run,
            partners=tuple(partners),
            sites=site_values,
            summary={"counts_by_type": dict(sorted(counts.items()))},
            warnings=tuple(warnings),
            provenance=_json_value(dict(provenance or {}), "provenance"),
            raw=_json_value(dict(raw or {}), "raw"),
        )
        value.validate()
        return value

    @property
    def interactions(self) -> Tuple[Interaction, ...]:
        return tuple(
            interaction
            for site in self.sites
            for interaction in site.interactions
        )

    def validate(self) -> None:
        if self.schema != INTERACTION_SCHEMA:
            raise SchemaError(
                "schema must be %r, got %r" % (INTERACTION_SCHEMA, self.schema)
            )
        self.run.validate()
        for partner in self.partners:
            partner.validate()
        partner_roles = [partner.role for partner in self.partners]
        if len(partner_roles) != len(set(partner_roles)):
            raise SchemaError("partners[].role values must be unique")
        for site in self.sites:
            site.validate()
        site_ids = [site.site_id for site in self.sites]
        if len(site_ids) != len(set(site_ids)):
            raise SchemaError("sites[].site_id values must be unique")
        interaction_ids = [value.id for value in self.interactions]
        if len(interaction_ids) != len(set(interaction_ids)):
            raise SchemaError("interactions[].id values must be unique")
        expected = dict(sorted(Counter(value.type for value in self.interactions).items()))
        summary = _mapping(self.summary, "summary")
        counts = _mapping(summary.get("counts_by_type"), "summary.counts_by_type")
        actual: Dict[str, int] = {}
        for key, count in counts.items():
            _text(key, "summary.counts_by_type key")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise SchemaError("summary.counts_by_type values must be integers >= 0")
            actual[key] = count
        if actual != expected:
            raise SchemaError(
                "summary.counts_by_type does not match interactions: expected %r"
                % expected
            )
        for warning in self.warnings:
            _text(warning, "warnings[]", allow_empty=True)
        _json_value(dict(_mapping(self.provenance, "provenance")), "provenance")
        _json_value(dict(_mapping(self.raw, "raw")), "raw")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        payload = {
            "schema": self.schema,
            "run": self.run.to_dict(),
            "partners": [value.to_dict() for value in self.partners],
            "sites": [value.to_dict() for value in self.sites],
            "summary": {
                "counts_by_type": dict(self.summary["counts_by_type"]),
            },
            "warnings": list(self.warnings),
        }
        if self.provenance:
            payload["provenance"] = _json_value(
                dict(_mapping(self.provenance, "provenance")), "provenance"
            )
        if self.raw:
            payload["raw"] = _json_value(dict(_mapping(self.raw, "raw")), "raw")
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InteractionResult":
        data = _mapping(data, "InteractionResult")
        partners = _sequence(data.get("partners", []), "partners")
        sites = _sequence(data.get("sites", []), "sites")
        warnings = _sequence(data.get("warnings", []), "warnings")
        summary_data = _mapping(data.get("summary"), "summary")
        counts = _mapping(summary_data.get("counts_by_type"), "summary.counts_by_type")
        value = cls(
            schema=_text(data.get("schema"), "schema"),
            run=RunInfo.from_dict(data.get("run")),
            partners=tuple(Partner.from_dict(item) for item in partners),
            sites=tuple(InteractionSite.from_dict(item) for item in sites),
            summary={"counts_by_type": dict(counts)},
            warnings=tuple(_text(item, "warnings[]", True) for item in warnings),
            provenance=_json_value(
                dict(_mapping(data.get("provenance", {}), "provenance")),
                "provenance",
            ),
            raw=_json_value(
                dict(_mapping(data.get("raw", {}), "raw")), "raw"
            ),
        )
        value.validate()
        return value

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, payload: str) -> "InteractionResult":
        try:
            data = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise SchemaError("invalid InteractionResult JSON: %s" % exc)
        return cls.from_dict(data)


@dataclass(frozen=True)
class ManifestEntry:
    name: str
    kind: str
    role: str
    interaction_ids: Tuple[str, ...] = ()

    def validate(self) -> None:
        _text(self.name, "manifest.created[].name")
        if self.kind not in {"selection", "object", "measurement", "group"}:
            raise SchemaError("unsupported manifest entry kind %r" % self.kind)
        _text(self.role, "manifest.created[].role")
        for interaction_id in self.interaction_ids:
            _text(interaction_id, "manifest.created[].interaction_ids[]")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "name": self.name,
            "kind": self.kind,
            "role": self.role,
            "interaction_ids": list(self.interaction_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ManifestEntry":
        data = _mapping(data, "manifest.created[]")
        interaction_ids = _sequence(
            data.get("interaction_ids", []), "manifest.created[].interaction_ids"
        )
        value = cls(
            name=_text(data.get("name"), "manifest.created[].name"),
            kind=_text(data.get("kind"), "manifest.created[].kind"),
            role=_text(data.get("role"), "manifest.created[].role"),
            interaction_ids=tuple(
                _text(item, "manifest.created[].interaction_ids[]")
                for item in interaction_ids
            ),
        )
        value.validate()
        return value


@dataclass(frozen=True)
class OutputManifest:
    schema: str
    run_id: str
    root_group: str
    source: str
    preset: str
    created: Tuple[ManifestEntry, ...]
    source_objects: Tuple[str, ...] = ()
    input_hash: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)
    stale_fingerprint: str = ""

    @classmethod
    def create(
        cls,
        run_id: str,
        root_group: str,
        source: str,
        preset: str,
        created: Iterable[ManifestEntry],
        source_objects: Iterable[str] = (),
        input_hash: str = "",
        parameters: Optional[Mapping[str, Any]] = None,
        stale_fingerprint: str = "",
    ) -> "OutputManifest":
        resolved_sources = tuple(str(value) for value in source_objects) or (str(source),)
        value = cls(
            schema=MANIFEST_SCHEMA,
            run_id=run_id,
            root_group=root_group,
            source=source,
            preset=preset,
            created=tuple(created),
            source_objects=resolved_sources,
            input_hash=str(input_hash),
            parameters=dict(parameters or {}),
            stale_fingerprint=str(stale_fingerprint),
        )
        value.validate()
        return value

    @property
    def created_names(self) -> Tuple[str, ...]:
        return tuple(entry.name for entry in self.created)

    @property
    def delete_order(self) -> Tuple[str, ...]:
        # All entries are unique, so reverse creation order is both exact and safe.
        return tuple(reversed(self.created_names))

    def validate(self) -> None:
        if self.schema != MANIFEST_SCHEMA:
            raise SchemaError("manifest schema must be %r" % MANIFEST_SCHEMA)
        _text(self.run_id, "manifest.run_id")
        _text(self.root_group, "manifest.root_group")
        _text(self.source, "manifest.source")
        _text(self.preset, "manifest.preset")
        for source_object in self.source_objects:
            _text(source_object, "manifest.source_objects[]")
        if len(self.source_objects) != len(set(self.source_objects)):
            raise SchemaError("manifest.source_objects must be unique")
        for label, digest in (
            ("manifest.input_hash", self.input_hash),
            ("manifest.stale_fingerprint", self.stale_fingerprint),
        ):
            if digest and (
                len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise SchemaError(f"{label} must be an empty or lowercase SHA-256 digest")
        _json_value(dict(self.parameters), "manifest.parameters")
        for entry in self.created:
            entry.validate()
        names = self.created_names
        if len(names) != len(set(names)):
            raise SchemaError("manifest.created names must be unique")
        if self.root_group not in names:
            raise SchemaError("manifest.root_group must occur in manifest.created")
        if self.source in names:
            raise SchemaError("source object must never be an output manifest entry")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "root_group": self.root_group,
            "source": self.source,
            "preset": self.preset,
            "source_objects": list(self.source_objects),
            "input_hash": self.input_hash,
            "parameters": _json_value(dict(self.parameters), "manifest.parameters"),
            "stale_fingerprint": self.stale_fingerprint,
            "created": [entry.to_dict() for entry in self.created],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OutputManifest":
        data = _mapping(data, "OutputManifest")
        created = _sequence(data.get("created", []), "manifest.created")
        source_objects = _sequence(
            data.get("source_objects", [data.get("source")]),
            "manifest.source_objects",
        )
        value = cls(
            schema=_text(data.get("schema"), "manifest.schema"),
            run_id=_text(data.get("run_id"), "manifest.run_id"),
            root_group=_text(data.get("root_group"), "manifest.root_group"),
            source=_text(data.get("source"), "manifest.source"),
            preset=_text(data.get("preset"), "manifest.preset"),
            created=tuple(ManifestEntry.from_dict(item) for item in created),
            source_objects=tuple(
                _text(item, "manifest.source_objects[]") for item in source_objects
            ),
            input_hash=_text(
                data.get("input_hash", ""), "manifest.input_hash", allow_empty=True
            ),
            parameters=_json_value(
                dict(_mapping(data.get("parameters", {}), "manifest.parameters")),
                "manifest.parameters",
            ),
            stale_fingerprint=_text(
                data.get("stale_fingerprint", ""),
                "manifest.stale_fingerprint",
                allow_empty=True,
            ),
        )
        value.validate()
        return value

    @classmethod
    def from_json(cls, payload: str) -> "OutputManifest":
        try:
            data = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise SchemaError("invalid OutputManifest JSON: %s" % exc)
        return cls.from_dict(data)
