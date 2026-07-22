"""Strict PLIP request/raw validation and canonical interaction conversion.

This module has no PyMOL or PLIP dependency.  The Windows Host supplies a PDB
export plus an explicit atom identity map; the WSL backend supplies versioned
PLIP rows keyed only by export serial.  Conversion joins those two inputs into
the public :mod:`interaction_schema` contract without guessing atom identity.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

try:
    from pymolplugins.interactions.schema import (
        AtomRef,
        BackendInfo,
        Geometry,
        Interaction,
        InteractionResult,
        InteractionSite,
        Partner,
        Participant,
        ResidueRef,
        RunInfo,
        SchemaError,
        SourceInfo,
    )
except ImportError:  # Supports direct execution from this directory.
    from schema import (  # type: ignore
        AtomRef,
        BackendInfo,
        Geometry,
        Interaction,
        InteractionResult,
        InteractionSite,
        Partner,
        Participant,
        ResidueRef,
        RunInfo,
        SchemaError,
        SourceInfo,
    )


PLIP_RAW_SCHEMA = "pymolplugins.plip_raw/2.0"
PLIP_RAW_VERSION = 2
PLIP_REQUEST_VERSION = 1
PLIP_INTERACTION_TYPES = frozenset(
    (
        "hydrophobic",
        "hydrogen_bond",
        "halogen_bond",
        "water_bridge",
        "metal_complex",
        "pi_stacking",
        "pi_cation",
        "salt_bridge",
    )
)

_ROOT_KEYS = frozenset(
    (
        "schema_version",
        "run_id",
        "preset",
        "structure",
        "source",
        "atom_map",
        "partners",
        "parameters",
    )
)
_ROOT_REQUIRED = frozenset(
    ("schema_version", "run_id", "preset", "structure", "source", "atom_map")
)
_LEGACY_SITE_KEYS = frozenset(
    (
        "hydrophobic_contacts",
        "hbonds",
        "halogen_bonds",
        "waterbridges",
        "metal_complexes",
        "pistacking",
        "pication",
        "saltbridges",
    )
)

_PARTICIPANT_ROLES = {
    "hydrophobic": ({"protein", "ligand"}, 2),
    "hydrogen_bond": ({"acceptor", "donor"}, 2),
    "halogen_bond": ({"donor", "acceptor"}, 2),
    "water_bridge": ({"donor", "acceptor", "water"}, 3),
    "metal_complex": ({"metal", "target"}, 2),
    "pi_stacking": ({"protein_ring", "ligand_ring"}, None),
    "pi_cation": ({"ring", "charge"}, None),
    "salt_bridge": ({"positive", "negative"}, None),
}
_ANGLE_COUNTS = {
    "hydrogen_bond": 1,
    "halogen_bond": 2,
    "water_bridge": 2,
    "pi_stacking": 1,
}


class PlipProtocolError(ValueError):
    """Raised for an invalid Host request, atom map, or PLIP raw payload."""


@dataclass(frozen=True)
class AtomMapEntry:
    """One unambiguous export-serial to PyMOL-source identity mapping."""

    atom: AtomRef
    residue: ResidueRef

    @property
    def export_serial(self) -> int:
        value = self.atom.export_serial
        if value is None:  # Prevent Optional from leaking beyond validation.
            raise PlipProtocolError("atom_map export_serial must not be null")
        return value

    @property
    def source_identity(self) -> Tuple[str, int]:
        return (self.atom.object, self.atom.source_index)

    def participant(self, role: str) -> Participant:
        return Participant(role=role, atom=self.atom, residue=self.residue)


@dataclass(frozen=True)
class PlipRequest:
    """Validated request data needed by both the backend and adapter."""

    run_id: str
    preset: str
    structure_text: str
    source: SourceInfo
    atom_map: Mapping[int, AtomMapEntry]
    partners: Tuple[Partner, ...]
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class _PdbAtom:
    serial: int
    name: str
    alt: str
    resn: str
    chain: str
    resseq: str
    insertion: str
    segi: str
    element: str
    line_number: int

    @property
    def resi_with_insertion(self) -> str:
        return self.resseq + self.insertion


_ATOM_RECORDS = ("ATOM  ", "HETATM")
_ATOM_SERIAL_RECORDS = frozenset(("ANISOU", "SIGATM", "SIGUIJ"))


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlipProtocolError("%s must be an object" % label)
    return value


def _array(value: Any, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PlipProtocolError("%s must be an array" % label)
    return value


def _string(value: Any, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise PlipProtocolError("%s must be a string" % label)
    if not allow_empty and not value:
        raise PlipProtocolError("%s must not be empty" % label)
    return value


def _integer(value: Any, label: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PlipProtocolError("%s must be an integer >= %d" % (label, minimum))
    return value


def _number(value: Any, label: str, allow_none: bool = False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlipProtocolError("%s must be numeric" % label)
    result = float(value)
    if not math.isfinite(result):
        raise PlipProtocolError("%s must be finite" % label)
    return result


def _sha256(value: Any, label: str) -> str:
    digest = _string(value, label)
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise PlipProtocolError("%s must be a lowercase SHA-256 digest" % label)
    return digest


def _exact_keys(
    value: Mapping[str, Any],
    allowed: Iterable[str],
    required: Iterable[str],
    label: str,
) -> None:
    allowed_set = set(allowed)
    required_set = set(required)
    keys = set(value)
    unknown = sorted(str(key) for key in keys.difference(allowed_set))
    missing = sorted(required_set.difference(keys))
    if unknown:
        raise PlipProtocolError("%s has unknown field(s): %s" % (label, ", ".join(unknown)))
    if missing:
        raise PlipProtocolError("%s is missing field(s): %s" % (label, ", ".join(missing)))


def _json_detach(value: Any, label: str) -> Any:
    _validate_json_tree(value, label)
    try:
        return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise PlipProtocolError("%s must be finite JSON data: %s" % (label, exc))


def _validate_json_tree(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise PlipProtocolError("%s object keys must be strings" % label)
            _validate_json_tree(child, "%s.%s" % (label, key))
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_json_tree(child, "%s[%d]" % (label, index))
        return
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise PlipProtocolError("%s contains unsupported or non-finite JSON data" % label)


def _pdb_serial(line: str, label: str) -> int:
    if len(line) < 11:
        raise PlipProtocolError("%s is shorter than 11 columns" % label)
    try:
        value = int(line[6:11])
    except ValueError as exc:
        raise PlipProtocolError("%s has an invalid atom serial" % label) from exc
    if value < 1:
        raise PlipProtocolError("%s atom serial must be >= 1" % label)
    return value


def _replace_pdb_serial(line: str, serial: int) -> str:
    if serial > 99999:
        raise PlipProtocolError("PDB export exceeds the 99999 serial limit")
    padded = line if len(line) >= 11 else line.ljust(11)
    return padded[:6] + ("%5d" % serial) + padded[11:]


def _normalize_conect(line: str, serial_map: Mapping[int, int], line_number: int) -> str:
    fields = [line[offset : offset + 5] for offset in range(6, len(line), 5)]
    normalized = []
    for field in fields:
        if not field.strip():
            normalized.append(field)
            continue
        try:
            old_serial = int(field)
        except ValueError as exc:
            raise PlipProtocolError(
                "PDB CONECT line %d has an invalid serial" % line_number
            ) from exc
        try:
            normalized.append("%5d" % serial_map[old_serial])
        except KeyError as exc:
            raise PlipProtocolError(
                "PDB CONECT line %d references unknown serial %d"
                % (line_number, old_serial)
            ) from exc
    return "CONECT" + "".join(normalized)


def _renumber_pdb_records(lines: Sequence[str]):
    serial_map: Dict[int, int] = {}
    normalized: List[str] = []
    next_serial = 0
    seen_atom = False
    previous_ter = False
    for line_number, line in enumerate(lines, start=1):
        record = line[:6]
        if line.startswith(("MODEL", "ENDMDL")):
            raise PlipProtocolError("multi-model PDB exports are not supported")
        if record in _ATOM_RECORDS:
            old_serial = _pdb_serial(line, "PDB atom line %d" % line_number)
            if old_serial in serial_map:
                raise PlipProtocolError("duplicate PDB atom serial %d" % old_serial)
            next_serial += 1
            serial_map[old_serial] = next_serial
            normalized.append(_replace_pdb_serial(line, next_serial))
            seen_atom, previous_ter = True, False
        elif line.startswith("TER"):
            if not seen_atom or previous_ter:
                raise PlipProtocolError("orphan or consecutive TER on line %d" % line_number)
            next_serial += 1
            normalized.append(_replace_pdb_serial(line, next_serial))
            previous_ter = True
        else:
            normalized.append(line)
    if not serial_map:
        raise PlipProtocolError("structure.text contains no ATOM/HETATM records")
    return normalized, serial_map


def _normalize_pdb_references(
    lines: List[str], serial_map: Mapping[int, int]
) -> None:
    for index, line in enumerate(lines):
        record = line[:6]
        if record == "CONECT":
            lines[index] = _normalize_conect(line, serial_map, index + 1)
            continue
        if record not in _ATOM_SERIAL_RECORDS:
            continue
        old_serial = _pdb_serial(line, "PDB %s line %d" % (record, index + 1))
        if old_serial not in serial_map:
            raise PlipProtocolError(
                "PDB %s line %d references unknown serial %d"
                % (record, index + 1, old_serial)
            )
        lines[index] = _replace_pdb_serial(line, serial_map[old_serial])


def normalize_pymol_pdb_export(text: str) -> str:
    """Renumber a one-state PyMOL PDB export for PLIP ``orig_idx`` joins.

    PyMOL emits short ``TER`` records without consuming atom serial numbers,
    while PLIP counts each such boundary in its public ``*_orig_idx`` values.
    ATOM/HETATM/TER and serial references are normalized without changing
    coordinates or residue identity.
    """

    lines, serial_map = _renumber_pdb_records(
        _string(text, "structure.text").splitlines()
    )
    _normalize_pdb_references(lines, serial_map)
    result = "\n".join(lines) + "\n"
    _parse_pdb_atoms(result)
    return result


def _pdb_atom_from_line(
    line: str, line_number: int, expected_serial: int, atoms: Mapping[int, _PdbAtom]
) -> _PdbAtom:
    if len(line) < 27:
        raise PlipProtocolError("PDB atom line %d is shorter than 27 columns" % line_number)
    serial = _pdb_serial(line, "PDB atom line %d" % line_number)
    if serial in atoms:
        raise PlipProtocolError(
            "duplicate PDB export_serial %d on lines %d and %d"
            % (serial, atoms[serial].line_number, line_number)
        )
    if serial != expected_serial:
        raise PlipProtocolError(
            "PDB export_serial %d on line %d must equal PLIP record index %d"
            % (serial, line_number, expected_serial)
        )
    return _PdbAtom(
        serial=serial,
        name=line[12:16].strip(),
        alt=line[16:17].strip(),
        resn=line[17:20].strip(),
        chain=line[21:22].strip(),
        resseq=line[22:26].strip(),
        insertion=line[26:27].strip(),
        segi=line[72:76].strip() if len(line) >= 76 else "",
        element=line[76:78].strip() if len(line) >= 78 else "",
        line_number=line_number,
    )


def _parse_pdb_atoms(text: str) -> Mapping[int, _PdbAtom]:
    atoms: Dict[int, _PdbAtom] = {}
    plip_original_index = 0
    previous_ter = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.startswith(("MODEL", "ENDMDL")):
            raise PlipProtocolError("multi-model PDB exports are not supported")
        # Match PLIP's PDBParser exactly: it treats any record beginning with
        # ``TER`` as a numbering boundary, including PyMOL's short TER lines.
        if line.startswith("TER"):
            previous_ter = True
            continue
        if line[:6] not in _ATOM_RECORDS:
            continue
        # PLIP PDBParser maps OpenBabel indices back to record order, not to an
        # arbitrary serial printed in columns 7-11.  A TER consumes one index.
        plip_original_index += 2 if previous_ter else 1
        previous_ter = False
        atom = _pdb_atom_from_line(
            line, line_number, plip_original_index, atoms
        )
        atoms[atom.serial] = atom
    if not atoms:
        raise PlipProtocolError("structure.text contains no ATOM/HETATM records")
    return atoms


def _pymol_insertion(atom: Any, resi: str) -> str:
    explicit = str(getattr(atom, "ins_code", "") or "")
    if explicit:
        return explicit
    resv = getattr(atom, "resv", None)
    if isinstance(resv, int) and not isinstance(resv, bool):
        prefix = str(resv)
        if resi.startswith(prefix):
            return resi[len(prefix) :]
    index = 1 if resi.startswith("-") else 0
    while index < len(resi) and resi[index].isdigit():
        index += 1
    return resi[index:]


def _atom_map_entry_from_pymol(atom: Any, export_serial: int) -> Dict[str, Any]:
    try:
        source_index = int(getattr(atom, "index"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PlipProtocolError("PyMOL atom index must be an integer") from exc
    resi = str(getattr(atom, "resi", "") or "")
    element = getattr(atom, "symbol", getattr(atom, "elem", ""))
    return {
        "export_serial": export_serial,
        "source_index": source_index,
        "object": str(getattr(atom, "model", "") or ""),
        "name": str(getattr(atom, "name", "") or ""),
        "element": str(element or ""),
        "alt": str(getattr(atom, "alt", "") or ""),
        "residue": {
            "resn": str(getattr(atom, "resn", "") or ""),
            "chain": str(getattr(atom, "chain", "") or ""),
            "segi": str(getattr(atom, "segi", "") or ""),
            "resi": resi,
            "insertion": _pymol_insertion(atom, resi),
        },
    }


def atom_map_from_pymol_export(
    pdb_text: str, atoms: Sequence[Any]
) -> Tuple[str, Tuple[Dict[str, Any], ...]]:
    """Return normalized PDB text and a losslessly verified PyMOL atom map."""

    normalized = normalize_pymol_pdb_export(pdb_text)
    pdb_atoms = _parse_pdb_atoms(normalized)
    atom_values = list(atoms)
    if len(atom_values) != len(pdb_atoms):
        raise PlipProtocolError(
            "PyMOL model/PDB atom count mismatch: model=%d PDB=%d"
            % (len(atom_values), len(pdb_atoms))
        )
    result = []
    for atom, (serial, pdb_atom) in zip(atom_values, pdb_atoms.items()):
        raw_entry = _atom_map_entry_from_pymol(atom, serial)
        entry = _parse_atom_map_entry(raw_entry, len(result))
        try:
            _validate_pdb_identity(entry, pdb_atom)
        except PlipProtocolError as exc:
            raise PlipProtocolError(
                "PyMOL model/PDB order or identity mismatch at atom %d: %s"
                % (len(result) + 1, exc)
            ) from exc
        result.append(raw_entry)
    _parse_atom_map(result, pdb_atoms)
    return normalized, tuple(result)


def _parse_residue(value: Any, label: str) -> ResidueRef:
    data = _object(value, label)
    keys = ("resn", "chain", "segi", "resi", "insertion")
    _exact_keys(data, keys, keys, label)
    residue = ResidueRef(
        resn=_string(data["resn"], label + ".resn", True),
        chain=_string(data["chain"], label + ".chain", True),
        segi=_string(data["segi"], label + ".segi", True),
        resi=_string(data["resi"], label + ".resi", True),
        insertion=_string(data["insertion"], label + ".insertion", True),
    )
    residue.validate()
    return residue


def _parse_atom_map_entry(value: Any, index: int) -> AtomMapEntry:
    label = "atom_map[%d]" % index
    data = _object(value, label)
    keys = (
        "export_serial",
        "source_index",
        "object",
        "name",
        "element",
        "alt",
        "residue",
    )
    _exact_keys(data, keys, keys, label)
    atom = AtomRef(
        source_index=_integer(data["source_index"], label + ".source_index"),
        export_serial=_integer(data["export_serial"], label + ".export_serial"),
        name=_string(data["name"], label + ".name"),
        element=_string(data["element"], label + ".element", True),
        alt=_string(data["alt"], label + ".alt", True),
        object=_string(data["object"], label + ".object"),
    )
    atom.validate()
    return AtomMapEntry(atom, _parse_residue(data["residue"], label + ".residue"))


def _validate_pdb_identity(entry: AtomMapEntry, pdb_atom: _PdbAtom) -> None:
    label = "atom_map export_serial %d" % entry.export_serial
    residue = entry.residue
    comparisons = (
        (entry.atom.name, pdb_atom.name, "name"),
        (entry.atom.alt, pdb_atom.alt, "alt"),
        (residue.resn, pdb_atom.resn, "resn"),
        (residue.chain, pdb_atom.chain, "chain"),
        (residue.insertion, pdb_atom.insertion, "insertion"),
        (residue.segi, pdb_atom.segi, "segi"),
    )
    for actual, expected, field_name in comparisons:
        if actual != expected:
            raise PlipProtocolError(
                "%s %s %r does not match PDB value %r"
                % (label, field_name, actual, expected)
            )
    valid_resi = {pdb_atom.resseq, pdb_atom.resi_with_insertion}
    if residue.resi not in valid_resi:
        raise PlipProtocolError(
            "%s resi %r does not match PDB residue %r"
            % (label, residue.resi, pdb_atom.resi_with_insertion)
        )
    if entry.atom.element and pdb_atom.element:
        if entry.atom.element.upper() != pdb_atom.element.upper():
            raise PlipProtocolError("%s element does not match PDB" % label)


def _parse_atom_map(values: Any, pdb_atoms: Mapping[int, _PdbAtom]):
    entries: Dict[int, AtomMapEntry] = {}
    source_identities = set()
    for index, raw in enumerate(_array(values, "atom_map")):
        entry = _parse_atom_map_entry(raw, index)
        if entry.export_serial in entries:
            raise PlipProtocolError(
                "duplicate atom_map export_serial %d" % entry.export_serial
            )
        if entry.source_identity in source_identities:
            raise PlipProtocolError(
                "duplicate atom_map source identity %r" % (entry.source_identity,)
            )
        entries[entry.export_serial] = entry
        source_identities.add(entry.source_identity)
    missing = sorted(set(pdb_atoms).difference(entries))
    extra = sorted(set(entries).difference(pdb_atoms))
    if missing or extra:
        raise PlipProtocolError(
            "atom_map/PDB serial mismatch: missing=%s extra=%s" % (missing, extra)
        )
    for serial, entry in entries.items():
        _validate_pdb_identity(entry, pdb_atoms[serial])
    return entries


def _parse_partners(values: Any) -> Tuple[Partner, ...]:
    partners = []
    for index, raw in enumerate(_array(values, "partners")):
        label = "partners[%d]" % index
        data = _object(raw, label)
        keys = ("role", "selection", "atom_count")
        _exact_keys(data, keys, keys, label)
        try:
            partners.append(Partner.from_dict(data))
        except SchemaError as exc:
            raise PlipProtocolError(str(exc)) from exc
    roles = [partner.role for partner in partners]
    if len(roles) != len(set(roles)):
        raise PlipProtocolError("partners[].role values must be unique")
    return tuple(partners)


def validate_plip_request(value: Mapping[str, Any]) -> PlipRequest:
    """Strictly validate a Host request and its PDB atom identity map."""

    data = _object(value, "request")
    _exact_keys(data, _ROOT_KEYS, _ROOT_REQUIRED, "request")
    schema_version = _integer(data["schema_version"], "schema_version")
    if schema_version != PLIP_REQUEST_VERSION:
        raise PlipProtocolError("unsupported schema_version")
    structure = _object(data["structure"], "structure")
    _exact_keys(structure, ("format", "text"), ("format", "text"), "structure")
    if _string(structure["format"], "structure.format").lower() != "pdb":
        raise PlipProtocolError("only structure.format='pdb' is supported")
    text = _string(structure["text"], "structure.text")
    source_data = _object(data["source"], "source")
    source_keys = ("object", "state", "sha256")
    _exact_keys(source_data, source_keys, source_keys, "source")
    try:
        source = SourceInfo.from_dict(source_data)
    except SchemaError as exc:
        raise PlipProtocolError(str(exc)) from exc
    parameters = _object(data.get("parameters", {}), "parameters")
    return PlipRequest(
        run_id=_string(data["run_id"], "run_id"),
        preset=_string(data["preset"], "preset"),
        structure_text=text,
        source=source,
        atom_map=_parse_atom_map(data["atom_map"], _parse_pdb_atoms(text)),
        partners=_parse_partners(data.get("partners", [])),
        parameters=_json_detach(dict(parameters), "parameters"),
    )


def _pymol_get_model(cmd_api: Any, selection: str, state: int) -> Any:
    try:
        return cmd_api.get_model(selection, state=state)
    except TypeError:
        return cmd_api.get_model(selection, state)


def _pymol_get_pdbstr(cmd_api: Any, selection: str, state: int) -> str:
    try:
        return cmd_api.get_pdbstr(selection, state=state)
    except TypeError:
        return cmd_api.get_pdbstr(selection, state)


_PYMOL_EXPORT_SETTINGS = {
    "pdb_conect_all": 1,
    "pdb_conect_nodup": 1,
    "pdb_retain_ids": 0,
    "pdb_use_ter_records": 1,
}


def _pymol_export_pdb(cmd_api: Any, selection: str, state: int) -> str:
    previous = {}
    try:
        for name, value in _PYMOL_EXPORT_SETTINGS.items():
            previous[name] = cmd_api.get_setting_text(name)
            cmd_api.set(name, value)
        return _pymol_get_pdbstr(cmd_api, selection, state)
    finally:
        for name, value in reversed(tuple(previous.items())):
            cmd_api.set(name, value)


def build_plip_request_from_pymol(
    cmd_api: Any,
    source: str,
    state: int = 1,
    preset: str = "plip_overview",
    run_id: Optional[str] = None,
    partners: Sequence[Mapping[str, Any]] = (),
    parameters: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Export one PyMOL state and build a validated worker request.

    No source object is altered. The atom map is joined against the exact
    order of ``get_model`` and the normalized ``get_pdbstr`` records before a
    request is returned, so an order/truncation mismatch fails locally.
    """

    source_value = _string(source, "source")
    state_value = _integer(state, "state")
    model = _pymol_get_model(cmd_api, source_value, state_value)
    atoms = list(getattr(model, "atom", ()))
    if not atoms:
        raise PlipProtocolError("source selection has no atoms in state %d" % state_value)
    pdb_text, atom_map = atom_map_from_pymol_export(
        _pymol_export_pdb(cmd_api, source_value, state_value), atoms
    )
    digest = hashlib.sha256(pdb_text.encode("utf-8")).hexdigest()
    request = {
        "schema_version": PLIP_REQUEST_VERSION,
        "run_id": str(run_id or uuid.uuid4().hex),
        "preset": _string(preset, "preset"),
        "structure": {"format": "pdb", "text": pdb_text},
        "source": {"object": source_value, "state": state_value, "sha256": digest},
        "atom_map": list(atom_map),
        "partners": [dict(value) for value in partners],
        "parameters": dict(parameters or {}),
    }
    validate_plip_request(request)
    return request


def _validate_geometry(value: Any, label: str) -> Dict[str, Any]:
    data = _object(value, label)
    keys = ("distance_angstrom", "angles_degree")
    _exact_keys(data, keys, keys, label)
    distance = _number(
        data["distance_angstrom"], label + ".distance_angstrom", allow_none=True
    )
    if distance is not None and distance < 0:
        raise PlipProtocolError(label + ".distance_angstrom must be >= 0")
    angles = []
    for index, raw in enumerate(_array(data["angles_degree"], label + ".angles_degree")):
        angle = _number(raw, "%s.angles_degree[%d]" % (label, index))
        if angle < 0 or angle > 360:
            raise PlipProtocolError("%s angle must be in [0, 360]" % label)
        angles.append(angle)
    return {"distance_angstrom": distance, "angles_degree": angles}


def _validate_participant_roles(interaction_type, participants, label):
    expected, exact_count = _PARTICIPANT_ROLES[interaction_type]
    roles = [participant["role"] for participant in participants]
    if set(roles) != expected:
        raise PlipProtocolError(
            "%s roles must be exactly %s" % (label, sorted(expected))
        )
    if exact_count is not None and len(participants) != exact_count:
        raise PlipProtocolError(
            "%s must contain exactly %d participants" % (label, exact_count)
        )


def _validate_raw_interaction(value: Any, label: str) -> Dict[str, Any]:
    data = _object(value, label)
    keys = ("type", "subtype", "participants", "geometry", "properties")
    _exact_keys(data, keys, keys, label)
    interaction_type = _string(data["type"], label + ".type")
    if interaction_type not in PLIP_INTERACTION_TYPES:
        raise PlipProtocolError("%s has unsupported type %r" % (label, interaction_type))
    participants = []
    seen_serials = set()
    for index, raw in enumerate(_array(data["participants"], label + ".participants")):
        participant_label = "%s.participants[%d]" % (label, index)
        participant = _object(raw, participant_label)
        keys = ("role", "export_serial")
        _exact_keys(participant, keys, keys, participant_label)
        serial = _integer(participant["export_serial"], participant_label + ".export_serial")
        if serial in seen_serials:
            raise PlipProtocolError("%s repeats export_serial %d" % (label, serial))
        seen_serials.add(serial)
        participants.append(
            {
                "role": _string(participant["role"], participant_label + ".role"),
                "export_serial": serial,
            }
        )
    if len(participants) < 2:
        raise PlipProtocolError(label + ".participants must contain at least two atoms")
    _validate_participant_roles(interaction_type, participants, label + ".participants")
    properties = _object(data["properties"], label + ".properties")
    geometry = _validate_geometry(data["geometry"], label + ".geometry")
    if geometry["distance_angstrom"] is None:
        raise PlipProtocolError(label + ".geometry.distance_angstrom is required")
    expected_angles = _ANGLE_COUNTS.get(interaction_type)
    if expected_angles is not None and len(geometry["angles_degree"]) != expected_angles:
        raise PlipProtocolError(
            "%s.geometry.angles_degree must contain %d value(s)"
            % (label, expected_angles)
        )
    return {
        "type": interaction_type,
        "subtype": _string(data["subtype"], label + ".subtype", True),
        "participants": participants,
        "geometry": geometry,
        "properties": _json_detach(dict(properties), label + ".properties"),
    }


def _validate_raw_site(value: Any, index: int) -> Dict[str, Any]:
    label = "raw.sites[%d]" % index
    data = _object(value, label)
    allowed = {"site_id", "bsid", "interactions"}.union(_LEGACY_SITE_KEYS)
    _exact_keys(data, allowed, ("site_id", "interactions"), label)
    site_id = _string(data["site_id"], label + ".site_id")
    if "bsid" in data and _string(data["bsid"], label + ".bsid") != site_id:
        raise PlipProtocolError(label + ".bsid must equal site_id")
    interactions = [
        _validate_raw_interaction(raw, "%s.interactions[%d]" % (label, offset))
        for offset, raw in enumerate(_array(data["interactions"], label + ".interactions"))
    ]
    signatures = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in interactions]
    if len(signatures) != len(set(signatures)):
        raise PlipProtocolError(label + " contains duplicate interaction rows")
    result = {"site_id": site_id, "interactions": interactions}
    if "bsid" in data:
        result["bsid"] = site_id
    for key in sorted(_LEGACY_SITE_KEYS.intersection(data)):
        result[key] = _json_detach(data[key], "%s.%s" % (label, key))
    return result


def validate_plip_raw(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a detached, strictly validated PLIP raw/2.0 payload."""

    data = _object(value, "raw")
    keys = ("schema", "version", "backend", "input_sha256", "sites", "warnings")
    _exact_keys(data, keys, keys, "raw")
    raw_version = _integer(data["version"], "raw.version")
    if data["schema"] != PLIP_RAW_SCHEMA or raw_version != PLIP_RAW_VERSION:
        raise PlipProtocolError("unsupported PLIP raw schema/version")
    backend = _object(data["backend"], "raw.backend")
    _exact_keys(backend, ("name", "version"), ("name", "version"), "raw.backend")
    if _string(backend["name"], "raw.backend.name") != "plip":
        raise PlipProtocolError("raw.backend.name must be 'plip'")
    sites = [
        _validate_raw_site(raw, index)
        for index, raw in enumerate(_array(data["sites"], "raw.sites"))
    ]
    site_ids = [site["site_id"] for site in sites]
    if len(site_ids) != len(set(site_ids)):
        raise PlipProtocolError("raw.sites[].site_id values must be unique")
    warnings = [
        _string(item, "raw.warnings[]", True)
        for item in _array(data["warnings"], "raw.warnings")
    ]
    return {
        "schema": PLIP_RAW_SCHEMA,
        "version": PLIP_RAW_VERSION,
        "backend": {
            "name": "plip",
            "version": _string(backend["version"], "raw.backend.version"),
        },
        "input_sha256": _sha256(data["input_sha256"], "raw.input_sha256"),
        "sites": sites,
        "warnings": warnings,
    }


def _interaction_id(site_id: str, row: Mapping[str, Any]) -> str:
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1((site_id + "\0" + payload).encode("utf-8")).hexdigest()[:16]
    return "plip_%s_%s" % (row["type"], digest)


def _convert_interaction(
    site_id: str,
    row: Mapping[str, Any],
    atom_map: Mapping[int, AtomMapEntry],
) -> Interaction:
    participants = []
    for value in row["participants"]:
        serial = value["export_serial"]
        try:
            entry = atom_map[serial]
        except KeyError as exc:
            raise PlipProtocolError(
                "PLIP interaction references unmapped export_serial %d" % serial
            ) from exc
        participants.append(entry.participant(value["role"]))
    geometry = row["geometry"]
    return Interaction(
        id=_interaction_id(site_id, row),
        type=row["type"],
        subtype=row["subtype"],
        participants=tuple(participants),
        geometry=Geometry(
            distance_angstrom=geometry["distance_angstrom"],
            angles_degree=tuple(geometry["angles_degree"]),
        ),
    )


def interaction_result_from_plip(
    request: Union[Mapping[str, Any], PlipRequest],
    raw: Mapping[str, Any],
) -> InteractionResult:
    """Join a validated Host atom map with raw PLIP rows."""

    parsed = request if isinstance(request, PlipRequest) else validate_plip_request(request)
    payload = validate_plip_raw(raw)
    expected_sha = hashlib.sha256(parsed.structure_text.encode("utf-8")).hexdigest()
    if payload["input_sha256"] != expected_sha:
        raise PlipProtocolError("raw.input_sha256 does not match structure.text")
    sites = tuple(
        InteractionSite(
            site_id=site["site_id"],
            interactions=tuple(
                _convert_interaction(site["site_id"], row, parsed.atom_map)
                for row in site["interactions"]
            ),
        )
        for site in payload["sites"]
    )
    parameters = dict(parsed.parameters)
    parameters["preset"] = parsed.preset
    provenance = {
        "backend_raw_schema": payload["schema"],
        "backend": dict(payload["backend"]),
        "input_sha256": payload["input_sha256"],
        "atom_map_entries": len(parsed.atom_map),
    }
    return InteractionResult.create(
        run=RunInfo(
            id=parsed.run_id,
            backend=BackendInfo("plip", payload["backend"]["version"]),
            source=parsed.source,
            parameters=parameters,
        ),
        partners=parsed.partners,
        sites=sites,
        warnings=payload["warnings"],
        provenance=provenance,
        raw=payload,
    )
