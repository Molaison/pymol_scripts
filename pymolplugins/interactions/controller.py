"""Local interaction presets and their mutation-bounded PyMOL adapter.

PyMOL is optional at import time.  The computation/result modules can therefore
be unit tested with a fake command API on WSL, while ``register_commands`` binds
the same controller to Host PyMOL.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import wraps
import hashlib
import json
import math
import os
import re
import sys
import traceback
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from pymolplugins.core.parameters import extend_command

try:
    from pymolplugins.interactions.presets import PRESETS, preset_parameters
    from pymolplugins.interactions.plip_protocol import build_plip_request_from_pymol
    from pymolplugins.interactions.schema import (
        AtomRef,
        BackendInfo,
        Geometry,
        Interaction,
        InteractionResult,
        InteractionSite,
        ManifestEntry,
        OutputManifest,
        Participant,
        Partner,
        ResidueRef,
        RunInfo,
        SourceInfo,
    )
except ImportError:  # Supports ``run path/to/pymol_interactions.py``.
    _MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
    if _MODULE_DIR not in sys.path:
        sys.path.insert(0, _MODULE_DIR)
    from presets import PRESETS, preset_parameters
    from plip_protocol import build_plip_request_from_pymol
    from schema import (
        AtomRef,
        BackendInfo,
        Geometry,
        Interaction,
        InteractionResult,
        InteractionSite,
        ManifestEntry,
        OutputManifest,
        Participant,
        Partner,
        ResidueRef,
        RunInfo,
        SourceInfo,
    )

try:
    from pymol import cmd as _PYMOL_CMD
except ImportError:  # Normal for WSL schema/fake-cmd tests.
    _PYMOL_CMD = None


LOCAL_BACKEND_VERSION = "1.0"
H_BOND_ANGLE = 45.0
_CMD_CONTROLLER_ATTR = "_pymolplugins_interaction_controller_v1"
_CMD_RUNS_ATTR = "_pymolplugins_interaction_runs_v1"
_MANIFEST_PROPERTY = "_pymolplugins_interaction_manifest"
_MANIFEST_CARRIER_SCHEMA = "pymolplugins.interaction_manifest_carrier/1.0"
_OWNER_REFERENCE_PREFIX = "ppi-run:"
_COMPLETION_EVENT_SCHEMA = "pymolplugins.interaction_completion/1.0"
_PREVIEW_SCHEMA = "pymolplugins.interaction_preview/1.0"
_previous_completion_listeners = globals().get("_COMPLETION_LISTENERS", {})
_COMPLETION_LISTENERS: Dict[str, Any] = (
    _previous_completion_listeners
    if isinstance(_previous_completion_listeners, dict)
    else {}
)
_OUTPUT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_OWNER_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_POSITIVE_ATOMS = (
    "((resn ARG and name NH1+NH2+NE) or (resn LYS and name NZ) "
    "or (formal_charge > 0))"
)
_NEGATIVE_ATOMS = (
    "((resn ASP and name OD1+OD2) or (resn GLU and name OE1+OE2) "
    "or (resn SEP+TPO+PTR and name O1P+O2P+O3P+OP1+OP2+OP3) "
    "or (formal_charge < 0))"
)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("expected a boolean value, got %r" % value)


def _cutoff(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be numeric" % field_name)
    if not math.isfinite(result) or result <= 0.0 or result > 100.0:
        raise ValueError("%s must be > 0 and <= 100 Angstrom" % field_name)
    return result


def _safe_component(value: Any, limit: int = 36) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_").lower()
    if not text:
        text = "blank"
    if text[0].isdigit():
        text = "n_" + text
    if len(text) <= limit:
        return text
    digest = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:8]
    return "%s_%s" % (text[: max(1, limit - 9)], digest)


def _child_name(root: str, suffix: str, limit: int = 180) -> str:
    suffix_value = "__" + _safe_component(suffix, 54)
    if len(root) + len(suffix_value) <= limit:
        return root + suffix_value
    digest = hashlib.sha1(root.encode("utf-8")).hexdigest()[:8]
    keep = max(1, limit - len(suffix_value) - len(digest) - 1)
    return "%s_%s%s" % (root[:keep], digest, suffix_value)


def _selector_literal(value: Any) -> str:
    return '"%s"' % str(value).replace("\\", "\\\\").replace('"', '\\"')


def _chain_selection(source: str, chain: str) -> str:
    return "(%s) and chain %s" % (source, _selector_literal(chain))


def _bounded_selection(source: str, selection: str) -> str:
    return "(%s) and (%s)" % (source, selection)


def _count_atoms(cmd_api: Any, selection: str, state: int) -> int:
    try:
        return int(cmd_api.count_atoms(selection, state=state))
    except TypeError:
        return int(cmd_api.count_atoms(selection))


def _get_model(cmd_api: Any, selection: str, state: int) -> Any:
    try:
        return cmd_api.get_model(selection, state=state)
    except TypeError:
        return cmd_api.get_model(selection, state)


def _get_chains(cmd_api: Any, selection: str, state: int) -> List[str]:
    try:
        chains = cmd_api.get_chains(selection, state=state)
    except TypeError:
        chains = cmd_api.get_chains(selection)
    return sorted({str(chain) for chain in chains})


def _atom_key(atom: Any) -> Tuple[str, int]:
    return (str(getattr(atom, "model", "")), int(getattr(atom, "index")))


def _pair_key(value: Any) -> Tuple[str, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("PyMOL find_pairs returned an invalid atom reference: %r" % (value,))
    return (str(value[0]), int(value[1]))


def _find_pairs(
    cmd_api: Any,
    selection1: str,
    selection2: str,
    cutoff: float,
    mode: int,
    state: int,
) -> Tuple[Tuple[Tuple[str, int], Tuple[str, int]], ...]:
    base = {"cutoff": cutoff, "mode": mode}
    state_kwargs = dict(base, state1=state, state2=state)
    attempts = []
    if mode == 1:
        attempts.append(dict(state_kwargs, angle=H_BOND_ANGLE))
    attempts.append(state_kwargs)
    if mode == 1:
        attempts.append(dict(base, angle=H_BOND_ANGLE))
    attempts.append(base)
    last_error = None
    for kwargs in attempts:
        try:
            raw = cmd_api.find_pairs(selection1, selection2, **kwargs)
            break
        except TypeError as exc:
            last_error = exc
    else:  # pragma: no cover - every supported PyMOL accepts the base call.
        assert last_error is not None
        raise last_error
    pairs = {(_pair_key(item[0]), _pair_key(item[1])) for item in raw}
    return tuple(sorted(pairs))


def _coordinates(atom: Any) -> Tuple[float, float, float]:
    coord = getattr(atom, "coord", (0.0, 0.0, 0.0))
    if len(coord) != 3:
        raise ValueError("atom coordinate must contain three values")
    return (float(coord[0]), float(coord[1]), float(coord[2]))


def _source_atoms(cmd_api: Any, source: str, state: int) -> Tuple[List[Any], Dict[Tuple[str, int], Any]]:
    atoms = list(getattr(_get_model(cmd_api, source, state), "atom", ()))
    if not atoms:
        raise ValueError(
            "source %r has no atoms in state %d; load a molecule or choose "
            "another source" % (source, state)
        )
    atom_map = {_atom_key(atom): atom for atom in atoms}
    return atoms, atom_map


def _selection_atom_keys(
    cmd_api: Any, selection: str, state: int
) -> Tuple[Tuple[str, int], ...]:
    atoms = getattr(_get_model(cmd_api, selection, state), "atom", ())
    return tuple(sorted({_atom_key(atom) for atom in atoms}))


def _source_sha256(atoms: Sequence[Any], state: int) -> str:
    rows = []
    for atom in sorted(atoms, key=_atom_key):
        coord = _coordinates(atom)
        rows.append(
            [
                str(getattr(atom, "model", "")),
                int(getattr(atom, "index")),
                str(getattr(atom, "name", "")),
                str(getattr(atom, "symbol", getattr(atom, "elem", "")) or ""),
                str(getattr(atom, "alt", "") or ""),
                str(getattr(atom, "resn", "")),
                str(getattr(atom, "chain", "")),
                str(getattr(atom, "segi", "")),
                str(getattr(atom, "resi", "")),
                [value.hex() for value in coord],
            ]
        )
    payload = json.dumps(
        {"state": state, "atoms": rows}, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _insertion_code(atom: Any, resi: str) -> str:
    explicit = str(getattr(atom, "ins_code", "") or "")
    if explicit:
        return explicit
    resv = getattr(atom, "resv", None)
    if resv is not None:
        prefix = str(resv)
        if resi.startswith(prefix):
            return resi[len(prefix) :]
    match = re.match(r"^-?\d+(.*)$", resi)
    return match.group(1) if match else ""


def _atom_ref(atom: Any) -> AtomRef:
    serial = getattr(atom, "id", None)
    if serial is not None:
        try:
            serial = int(serial)
        except (TypeError, ValueError):
            serial = None
    element = getattr(atom, "symbol", getattr(atom, "elem", ""))
    return AtomRef(
        source_index=int(getattr(atom, "index")),
        export_serial=serial,
        name=str(getattr(atom, "name", "")),
        element=str(element or ""),
        alt=str(getattr(atom, "alt", "") or ""),
        object=str(getattr(atom, "model", "") or ""),
    )


def _residue_ref(atom: Any) -> ResidueRef:
    resi = str(getattr(atom, "resi", "") or "")
    return ResidueRef(
        resn=str(getattr(atom, "resn", "") or ""),
        chain=str(getattr(atom, "chain", "") or ""),
        segi=str(getattr(atom, "segi", "") or ""),
        resi=resi,
        insertion=_insertion_code(atom, resi),
    )


def _distance(atom1: Any, atom2: Any) -> float:
    coord1 = _coordinates(atom1)
    coord2 = _coordinates(atom2)
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(coord1, coord2)))


def _interaction_id(
    interaction_type: str,
    subtype: str,
    key1: Tuple[str, int],
    key2: Tuple[str, int],
) -> str:
    payload = "%s|%s|%s:%d|%s:%d" % (
        interaction_type,
        subtype,
        key1[0],
        key1[1],
        key2[0],
        key2[1],
    )
    return "%s_%s" % (
        _safe_component(interaction_type, 20),
        hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12],
    )


def _make_interaction(
    atom_map: Mapping[Tuple[str, int], Any],
    key1: Tuple[str, int],
    key2: Tuple[str, int],
    interaction_type: str,
    subtype: str,
    role1: str,
    role2: str,
) -> Interaction:
    try:
        atom1 = atom_map[key1]
        atom2 = atom_map[key2]
    except KeyError as exc:
        raise ValueError(
            "interaction atom %r is outside the source selection" % (exc.args[0],)
        )
    return Interaction(
        id=_interaction_id(interaction_type, subtype, key1, key2),
        type=interaction_type,
        subtype=subtype,
        participants=(
            Participant(role=role1, atom=_atom_ref(atom1), residue=_residue_ref(atom1)),
            Participant(role=role2, atom=_atom_ref(atom2), residue=_residue_ref(atom2)),
        ),
        geometry=Geometry(distance_angstrom=_distance(atom1, atom2)),
    )


def _interactions_from_specs(
    cmd_api: Any,
    atom_map: Mapping[Tuple[str, int], Any],
    state: int,
    specs: Iterable[Tuple[str, str, float, int, str, str, str, str]],
) -> Tuple[Interaction, ...]:
    interactions: Dict[str, Interaction] = {}
    for selection1, selection2, cutoff, mode, kind, subtype, role1, role2 in specs:
        for key1, key2 in _find_pairs(
            cmd_api, selection1, selection2, cutoff, mode, state
        ):
            value = _make_interaction(
                atom_map, key1, key2, kind, subtype, role1, role2
            )
            interactions[value.id] = value
    return tuple(interactions[key] for key in sorted(interactions))


def _hbond_specs(
    partner1: str,
    partner2: str,
    cutoff: float,
    subtype: str = "donor_acceptor",
) -> Tuple[Tuple[str, str, float, int, str, str, str, str], ...]:
    return (
        (
            "(%s) and donor" % partner1,
            "(%s) and acceptor" % partner2,
            cutoff,
            1,
            "hydrogen_bond",
            subtype,
            "donor",
            "acceptor",
        ),
        (
            "(%s) and donor" % partner2,
            "(%s) and acceptor" % partner1,
            cutoff,
            1,
            "hydrogen_bond",
            subtype,
            "donor",
            "acceptor",
        ),
    )


def _polar_clash_specs(
    partner1: str, partner2: str, cutoff: float
) -> Tuple[Tuple[str, str, float, int, str, str, str, str], ...]:
    return (
        (
            "(%s) and (donor and not acceptor)" % partner1,
            "(%s) and (donor and not acceptor)" % partner2,
            cutoff,
            0,
            "polar_clash",
            "donor_donor",
            "donor",
            "donor",
        ),
        (
            "(%s) and (acceptor and not donor)" % partner1,
            "(%s) and (acceptor and not donor)" % partner2,
            cutoff,
            0,
            "polar_clash",
            "acceptor_acceptor",
            "acceptor",
            "acceptor",
        ),
    )


def _pair_identity(interaction: Interaction) -> Tuple[Tuple[str, int], ...]:
    return tuple(
        sorted(
            (participant.atom.object, participant.atom.source_index)
            for participant in interaction.participants[:2]
        )
    )


def _polar_interactions(
    cmd_api: Any,
    atom_map: Mapping[Tuple[str, int], Any],
    selection1: str,
    selection2: str,
    state: int,
    good_cutoff: float,
    weak_cutoff: float,
    clash_cutoff: float,
) -> Tuple[Interaction, ...]:
    good = _interactions_from_specs(
        cmd_api,
        atom_map,
        state,
        _hbond_specs(selection1, selection2, good_cutoff, "good"),
    )
    weak_candidates = _interactions_from_specs(
        cmd_api,
        atom_map,
        state,
        _hbond_specs(selection1, selection2, weak_cutoff, "weak"),
    )
    good_pairs = {_pair_identity(value) for value in good}
    weak = tuple(
        value for value in weak_candidates if _pair_identity(value) not in good_pairs
    )
    clashes = _interactions_from_specs(
        cmd_api,
        atom_map,
        state,
        _polar_clash_specs(selection1, selection2, clash_cutoff),
    )
    values = {value.id: value for value in good + weak + clashes}
    return tuple(values[key] for key in sorted(values))


def _salt_specs(
    partner1: str, partner2: str, cutoff: float
) -> Tuple[Tuple[str, str, float, int, str, str, str, str], ...]:
    return (
        (
            "(%s) and %s" % (partner1, _POSITIVE_ATOMS),
            "(%s) and %s" % (partner2, _NEGATIVE_ATOMS),
            cutoff,
            0,
            "salt_bridge",
            "positive_negative",
            "positive",
            "negative",
        ),
        (
            "(%s) and %s" % (partner2, _POSITIVE_ATOMS),
            "(%s) and %s" % (partner1, _NEGATIVE_ATOMS),
            cutoff,
            0,
            "salt_bridge",
            "positive_negative",
            "positive",
            "negative",
        ),
    )


def _residue_key(atom: Any) -> Tuple[str, str, str, str, str]:
    return (
        str(getattr(atom, "model", "") or ""),
        str(getattr(atom, "segi", "") or ""),
        str(getattr(atom, "chain", "") or ""),
        str(getattr(atom, "resi", "") or ""),
        str(getattr(atom, "resn", "") or ""),
    )


def _site_id(key: Tuple[str, str, str, str, str]) -> str:
    model, segi, chain, resi, resn = key
    readable = "%s_%s_%s" % (resn or "unk", chain or "blank", resi or "blank")
    digest = hashlib.sha1("|".join(key).encode("utf-8")).hexdigest()[:7]
    return "%s_%s" % (_safe_component(readable, 34), digest)


def _indices_selection(source: str, keys: Sequence[Tuple[str, int]]) -> str:
    atoms = []
    for model, index in sorted(keys):
        model_part = "model %s and " % _selector_literal(model) if model else ""
        atoms.append("(%sindex %d)" % (model_part, index))
    if not atoms:
        return "none"
    return "(%s) and (%s)" % (source, " or ".join(atoms))


def _source_info(
    source: str, state: int, atoms: Sequence[Any]
) -> SourceInfo:
    return SourceInfo(object=source, state=state, sha256=_source_sha256(atoms, state))


def _local_result(
    run_id: str,
    source: str,
    state: int,
    atoms: Sequence[Any],
    parameters: Mapping[str, Any],
    partners: Iterable[Partner],
    sites: Iterable[InteractionSite],
    warnings: Iterable[str],
) -> InteractionResult:
    return InteractionResult.create(
        run=RunInfo(
            id=run_id,
            backend=BackendInfo("pymol-local", LOCAL_BACKEND_VERSION),
            source=_source_info(source, state, atoms),
            parameters=parameters,
        ),
        partners=partners,
        sites=sites,
        warnings=warnings,
    )


def _interchain_inputs(
    cmd_api: Any,
    source: str,
    partner1: str,
    partner2: str,
    state: int,
) -> Tuple[List[Tuple[str, str, str]], List[Partner]]:
    if bool(partner1) != bool(partner2):
        raise ValueError("interchain_basic requires both partner1 and partner2, or neither")
    if partner1 and partner2:
        selection1 = _bounded_selection(source, partner1)
        selection2 = _bounded_selection(source, partner2)
        keys1 = set(_selection_atom_keys(cmd_api, selection1, state))
        keys2 = set(_selection_atom_keys(cmd_api, selection2, state))
        if not keys1:
            raise ValueError(
                "partner1 selection has no atoms in source/state; check partner1=%r"
                % partner1
            )
        if not keys2:
            raise ValueError(
                "partner2 selection has no atoms in source/state; check partner2=%r"
                % partner2
            )
        overlap = keys1.intersection(keys2)
        if overlap:
            raise ValueError(
                "partner1 and partner2 overlap by %d atom(s); use disjoint selections"
                % len(overlap)
            )
        return (
            [("partner1_partner2", selection1, selection2)],
            [
                Partner("partner1", selection1, _count_atoms(cmd_api, selection1, state)),
                Partner("partner2", selection2, _count_atoms(cmd_api, selection2, state)),
            ],
        )
    polymer_source = "(%s) and polymer" % source
    chains = _get_chains(cmd_api, polymer_source, state)
    if len(chains) < 2:
        raise ValueError(
            "source has fewer than two chains; pass explicit disjoint partner1 and "
            "partner2 selections"
        )
    selections = {
        chain: _chain_selection(polymer_source, chain) for chain in chains
    }
    pairs = [
        (
            "%s_%s" % (chain1 or "blank", chain2 or "blank"),
            selections[chain1],
            selections[chain2],
        )
        for index, chain1 in enumerate(chains)
        for chain2 in chains[index + 1 :]
    ]
    partners = []
    for chain in chains:
        digest = hashlib.sha1(chain.encode("utf-8")).hexdigest()[:5]
        role = "chain_%s_%s" % (_safe_component(chain or "blank", 18), digest)
        partners.append(
            Partner(role, selections[chain], _count_atoms(cmd_api, selections[chain], state))
        )
    return pairs, partners


def _interchain_sites(
    cmd_api: Any,
    atom_map: Mapping[Tuple[str, int], Any],
    pairs: Iterable[Tuple[str, str, str]],
    state: int,
    hbond_cutoff: float,
    salt_cutoff: float,
) -> List[InteractionSite]:
    sites = []
    for pair_name, selection1, selection2 in pairs:
        specs = list(_hbond_specs(selection1, selection2, hbond_cutoff))
        specs.extend(_salt_specs(selection1, selection2, salt_cutoff))
        digest = hashlib.sha1(
            (selection1 + "\0" + selection2).encode("utf-8")
        ).hexdigest()[:7]
        sites.append(
            InteractionSite(
                "chains_%s_%s" % (_safe_component(pair_name, 32), digest),
                _interactions_from_specs(cmd_api, atom_map, state, specs),
            )
        )
    return sites


def _build_interchain_result(
    cmd_api: Any,
    run_id: str,
    source: str,
    partner1: str,
    partner2: str,
    state: int,
    parameters: Mapping[str, Any],
) -> InteractionResult:
    atoms, atom_map = _source_atoms(cmd_api, source, state)
    hbond_cutoff = _cutoff(parameters["hbond_cutoff"], "hbond_cutoff")
    salt_cutoff = _cutoff(parameters["salt_cutoff"], "salt_cutoff")
    pairs, partner_values = _interchain_inputs(
        cmd_api, source, partner1, partner2, state
    )
    sites = _interchain_sites(
        cmd_api, atom_map, pairs, state, hbond_cutoff, salt_cutoff
    )
    warnings = []
    if len(partner_values) < 2:
        warnings.append("source has fewer than two chain partners")
    if not any(site.interactions for site in sites):
        warnings.append("no local interchain interactions found")
    run_parameters = dict(parameters)
    run_parameters.update(
        {
            "preset": "interchain_basic",
            "partner1": partner1,
            "partner2": partner2,
            "hbond_angle": H_BOND_ANGLE,
        }
    )
    return _local_result(
        run_id,
        source,
        state,
        atoms,
        run_parameters,
        partner_values,
        sites,
        warnings,
    )


def _build_polar_result(
    cmd_api: Any,
    run_id: str,
    source: str,
    partner1: str,
    partner2: str,
    state: int,
    parameters: Mapping[str, Any],
) -> InteractionResult:
    atoms, atom_map = _source_atoms(cmd_api, source, state)
    good_cutoff = _cutoff(parameters["good_cutoff"], "good_cutoff")
    weak_cutoff = _cutoff(parameters["weak_cutoff"], "weak_cutoff")
    clash_cutoff = _cutoff(parameters["clash_cutoff"], "clash_cutoff")
    if weak_cutoff < good_cutoff:
        raise ValueError("weak_cutoff must be greater than or equal to good_cutoff")
    pairs, partner_values = _interchain_inputs(
        cmd_api, source, partner1, partner2, state
    )
    sites = []
    for pair_name, selection1, selection2 in pairs:
        digest = hashlib.sha1(
            (selection1 + "\0" + selection2).encode("utf-8")
        ).hexdigest()[:7]
        sites.append(
            InteractionSite(
                "polar_%s_%s" % (_safe_component(pair_name, 32), digest),
                _polar_interactions(
                    cmd_api,
                    atom_map,
                    selection1,
                    selection2,
                    state,
                    good_cutoff,
                    weak_cutoff,
                    clash_cutoff,
                ),
            )
        )
    run_parameters = dict(parameters)
    run_parameters.update(
        {
            "preset": "polar_qc",
            "partner1": partner1,
            "partner2": partner2,
            "hbond_angle": H_BOND_ANGLE,
        }
    )
    warnings = [] if any(site.interactions for site in sites) else [
        "no local polar contacts or clashes found"
    ]
    return _local_result(
        run_id,
        source,
        state,
        atoms,
        run_parameters,
        partner_values,
        sites,
        warnings,
    )


def _ligand_partner_selections(
    source: str, partner1: str, partner2: str
) -> Tuple[str, str]:
    # partner2 is always the ligand.  ``organic`` deliberately excludes the
    # whole containing complex, crystallographic water, and metal ions.
    ligand = _bounded_selection(source, partner2) if partner2 else "(%s) and organic" % source
    if partner1:
        receptor = "(%s) and not (%s)" % (
            _bounded_selection(source, partner1),
            ligand,
        )
    else:
        receptor = "(%s) and not (%s) and not solvent" % (source, ligand)
    return ligand, receptor


def _ligand_residue_maps(
    ligand_atoms: Iterable[Any],
) -> Tuple[
    Dict[Tuple[str, str, str, str, str], List[Tuple[str, int]]],
    Dict[Tuple[str, int], Tuple[str, str, str, str, str]],
]:
    residue_atoms: Dict[
        Tuple[str, str, str, str, str], List[Tuple[str, int]]
    ] = defaultdict(list)
    atom_to_residue: Dict[Tuple[str, int], Tuple[str, str, str, str, str]] = {}
    for atom in ligand_atoms:
        atom_key = _atom_key(atom)
        residue_key = _residue_key(atom)
        residue_atoms[residue_key].append(atom_key)
        atom_to_residue[atom_key] = residue_key
    return residue_atoms, atom_to_residue


def _ligand_interactions(
    cmd_api: Any,
    atom_map: Mapping[Tuple[str, int], Any],
    preset: str,
    ligand: str,
    receptor: str,
    state: int,
    parameters: Mapping[str, Any],
) -> Tuple[Interaction, ...]:
    if preset == "ligand_contacts":
        contact_cutoff = _cutoff(parameters["contact_cutoff"], "contact_cutoff")
        candidates = _interactions_from_specs(
            cmd_api,
            atom_map,
            state,
            (
                (
                    "(%s) and not hydro" % ligand,
                    "(%s) and not hydro" % receptor,
                    contact_cutoff,
                    0,
                    "close_contact",
                    "closest_heavy_atom",
                    "ligand",
                    "receptor",
                ),
            ),
        )
        closest: Dict[Tuple[Any, ...], Interaction] = {}
        for interaction in candidates:
            residue_pair = tuple(
                (
                    participant.atom.object,
                    participant.residue.segi,
                    participant.residue.chain,
                    participant.residue.resi,
                    participant.residue.insertion,
                    participant.residue.resn,
                )
                for participant in interaction.participants[:2]
            )
            previous = closest.get(residue_pair)
            distance = interaction.geometry.distance_angstrom
            previous_distance = (
                previous.geometry.distance_angstrom if previous is not None else None
            )
            if previous is None or (
                distance is not None
                and (previous_distance is None or distance < previous_distance)
            ):
                closest[residue_pair] = interaction
        return tuple(sorted(closest.values(), key=lambda value: value.id))
    cutoff = _cutoff(parameters["hbond_cutoff"], "hbond_cutoff")
    specs = list(_hbond_specs(ligand, receptor, cutoff))
    if preset == "ligand_site":
        salt_cutoff = _cutoff(parameters["salt_cutoff"], "salt_cutoff")
        specs.extend(_salt_specs(ligand, receptor, salt_cutoff))
    return _interactions_from_specs(cmd_api, atom_map, state, specs)


def _bucket_ligand_interactions(
    interactions: Iterable[Interaction],
    atom_to_residue: Mapping[Tuple[str, int], Tuple[str, str, str, str, str]],
) -> Dict[Tuple[str, str, str, str, str], List[Interaction]]:
    result: Dict[Tuple[str, str, str, str, str], List[Interaction]] = defaultdict(list)
    for interaction in interactions:
        residue_key = next(
            (
                atom_to_residue.get(
                    (participant.atom.object, participant.atom.source_index)
                )
                for participant in interaction.participants
                if (participant.atom.object, participant.atom.source_index)
                in atom_to_residue
            ),
            None,
        )
        if residue_key is None:
            raise ValueError("local ligand interaction has no ligand participant")
        result[residue_key].append(interaction)
    return result


def _ligand_sites(
    source: str,
    residue_atoms: Mapping[
        Tuple[str, str, str, str, str], Sequence[Tuple[str, int]]
    ],
    interactions: Mapping[
        Tuple[str, str, str, str, str], Sequence[Interaction]
    ],
) -> Tuple[List[InteractionSite], Dict[str, str]]:
    sites = []
    selections = {}
    for residue_key in sorted(residue_atoms):
        site_id = _site_id(residue_key)
        selections[site_id] = _indices_selection(source, residue_atoms[residue_key])
        sites.append(
            InteractionSite(
                site_id,
                tuple(
                    sorted(interactions.get(residue_key, ()), key=lambda item: item.id)
                ),
            )
        )
    return sites, selections


def _ligand_run_parameters(
    parameters: Mapping[str, Any],
    preset: str,
    partner1: str,
    partner2: str,
    ligand: str,
    receptor: str,
    interface: str,
    site_selections: Mapping[str, str],
) -> Dict[str, Any]:
    values = dict(parameters)
    values.update(
        {
            "preset": preset,
            "partner1": partner1,
            "partner2": partner2,
            "ligand_selection": ligand,
            "receptor_selection": receptor,
            "interface_selection": interface,
            "site_selections": dict(site_selections),
        }
    )
    return values


def _analyze_ligand_sites(
    cmd_api: Any,
    atom_map: Mapping[Tuple[str, int], Any],
    preset: str,
    source: str,
    ligand: str,
    receptor: str,
    state: int,
    parameters: Mapping[str, Any],
) -> Tuple[List[Any], Tuple[Interaction, ...], List[InteractionSite], Dict[str, str]]:
    ligand_atoms = list(getattr(_get_model(cmd_api, ligand, state), "atom", ()))
    if not ligand_atoms:
        raise ValueError(
            "ligand selection has no atoms in source/state; pass ligand as partner2"
        )
    if _count_atoms(cmd_api, receptor, state) == 0:
        raise ValueError(
            "receptor selection has no atoms after excluding the ligand; check "
            "source and partner1/partner2"
        )
    residue_atoms, atom_to_residue = _ligand_residue_maps(ligand_atoms)
    interactions = _ligand_interactions(
        cmd_api, atom_map, preset, ligand, receptor, state, parameters
    )
    buckets = _bucket_ligand_interactions(interactions, atom_to_residue)
    sites, selections = _ligand_sites(source, residue_atoms, buckets)
    return ligand_atoms, interactions, sites, selections


def _build_ligand_result(
    cmd_api: Any,
    run_id: str,
    preset: str,
    source: str,
    partner1: str,
    partner2: str,
    state: int,
    parameters: Mapping[str, Any],
) -> InteractionResult:
    atoms, atom_map = _source_atoms(cmd_api, source, state)
    site_cutoff = _cutoff(parameters["site_cutoff"], "site_cutoff")
    if preset == "ligand_contacts":
        contact_cutoff = _cutoff(parameters["contact_cutoff"], "contact_cutoff")
        if site_cutoff < contact_cutoff:
            raise ValueError(
                "site_cutoff must be greater than or equal to contact_cutoff so "
                "every rendered contact is in the displayed interface"
            )
    ligand, receptor = _ligand_partner_selections(source, partner1, partner2)
    ligand_atoms, all_interactions, sites, site_selections = _analyze_ligand_sites(
        cmd_api, atom_map, preset, source, ligand, receptor, state, parameters
    )
    interface = "(byres ((%s) within %.6g of (%s)))" % (
        receptor,
        site_cutoff,
        ligand,
    )
    run_parameters = _ligand_run_parameters(
        parameters,
        preset,
        partner1,
        partner2,
        ligand,
        receptor,
        interface,
        site_selections,
    )
    warnings = []
    if not all_interactions:
        if preset == "ligand_contacts":
            warnings.append("no ligand/receptor heavy-atom contacts found")
        else:
            warnings.append("no local ligand interactions found")
    if preset == "ligand_contacts":
        warnings.append(
            "close_contact is distance-only; it does not classify interaction chemistry"
        )
    if not partner2:
        ligand_residues = {_residue_key(atom) for atom in ligand_atoms}
        if len(ligand_residues) > 1:
            warnings.append(
                "default organic matched %d ligand residues; pass exact partner2 to "
                "analyze one ligand" % len(ligand_residues)
            )
    if preset in {"ligand_quick", "ligand_site"}:
        run_parameters["hbond_angle"] = H_BOND_ANGLE
    return _local_result(
        run_id,
        source,
        state,
        atoms,
        run_parameters,
        (
            Partner("receptor", receptor, _count_atoms(cmd_api, receptor, state)),
            Partner("ligand", ligand, len(ligand_atoms)),
        ),
        sites,
        warnings,
    )


class _OutputRecorder:
    """Record every created name before mutation so failures are reversible."""

    def __init__(self, cmd_api: Any, existing_names: Iterable[str]) -> None:
        self.cmd = cmd_api
        self.existing_names = set(existing_names)
        self.entries: List[ManifestEntry] = []
        self._entry_names = set()

    def record(
        self,
        name: str,
        kind: str,
        role: str,
        interaction_ids: Iterable[str] = (),
    ) -> None:
        if name in self._entry_names:
            return
        if name in self.existing_names:
            raise ValueError("output name already exists and is not owned: %s" % name)
        self._entry_names.add(name)
        self.entries.append(
            ManifestEntry(name, kind, role, tuple(sorted(interaction_ids)))
        )

    def select(self, name: str, selection: str, role: str) -> None:
        self.record(name, "selection", role)
        self.cmd.select(name, selection)

    def create(self, name: str, selection: str, state: int, role: str) -> None:
        self.record(name, "object", role)
        self.cmd.create(name, selection, state, 1)

    def pseudoatom(self, name: str, role: str) -> None:
        self.record(name, "object", role)
        self.cmd.pseudoatom(
            name,
            name="PS1",
            resn="PPM",
            resi="1",
            elem="C",
            pos=[0.0, 0.0, 0.0],
        )

    def distance(
        self,
        name: str,
        interactions: Sequence[Interaction],
        source: str,
        state: int,
    ) -> None:
        self.record(
            name,
            "measurement",
            "interaction_measurement",
            (interaction.id for interaction in interactions),
        )
        for interaction in interactions:
            for first, second in _display_pairs(interaction):
                self.cmd.distance(
                    name,
                    _participant_selection(source, first),
                    _participant_selection(source, second),
                    cutoff=100.0,
                    mode=0,
                    state=state,
                )

    def group(self, name: str, members: str, role: str) -> None:
        self.record(name, "group", role)
        self.cmd.group(name, members)

    def cleanup(self) -> None:
        for entry in reversed(self.entries):
            try:
                self.cmd.delete(entry.name)
            except Exception:
                pass


def _participant_selection(source: str, participant: Participant) -> str:
    model = participant.atom.object
    model_clause = "model %s and " % _selector_literal(model) if model else ""
    return "(%s) and (%sindex %d)" % (
        source,
        model_clause,
        participant.atom.source_index,
    )


def _display_pairs(interaction: Interaction) -> Tuple[Tuple[Participant, Participant], ...]:
    """Choose cross-role atom anchors for a canonical visual measurement."""

    participants = interaction.participants
    if len(participants) < 2:
        return ()
    first_by_role: Dict[str, Participant] = {}
    role_order = []
    for participant in participants:
        if participant.role not in first_by_role:
            first_by_role[participant.role] = participant
            role_order.append(participant.role)
    if interaction.type == "water_bridge" and set(role_order) >= {
        "donor",
        "acceptor",
        "water",
    }:
        return (
            (first_by_role["donor"], first_by_role["water"]),
            (first_by_role["water"], first_by_role["acceptor"]),
        )
    if len(role_order) >= 2:
        return ((first_by_role[role_order[0]], first_by_role[role_order[1]]),)
    return ((participants[0], participants[1]),)


def _existing_names(cmd_api: Any) -> Tuple[str, ...]:
    try:
        return tuple(str(name) for name in cmd_api.get_names("all"))
    except (AttributeError, TypeError):
        try:
            return tuple(str(name) for name in cmd_api.get_names())
        except AttributeError:
            return ()


_INTERACTION_COLORS = {
    "hydrogen_bond": "cyan",
    "salt_bridge": "magenta",
    "polar_good": "cyan",
    "polar_weak": "purple",
    "donor_clash": "red",
    "acceptor_clash": "red",
    "hydrophobic": "gray70",
    "halogen_bond": "forest",
    "water_bridge": "marine",
    "metal_complex": "orange",
    "pi_stacking": "tv_blue",
    "pi_cation": "violet",
    "close_contact": "yellow",
}


def _apply_property_colors(cmd_api: Any, selection: str) -> None:
    # ``selection`` is always a generated copy, never the user's source object.
    cmd_api.color("red", "(%s) and resn ASP+GLU" % selection)
    cmd_api.color("blue", "(%s) and resn LYS+ARG+HIS" % selection)
    cmd_api.color("green", "(%s) and resn SER+THR+ASN+GLN" % selection)
    cmd_api.color(
        "yellow",
        "(%s) and resn ALA+VAL+LEU+ILE+MET+PHE+TRP+TYR" % selection,
    )
    cmd_api.color("orange", "(%s) and resn GLY+PRO+CYS" % selection)


def _render_partners(
    cmd_api: Any,
    recorder: _OutputRecorder,
    result: InteractionResult,
    root_group: str,
) -> str:
    partners_group = _child_name(root_group, "partners")
    recorder.group(partners_group, "", "partners_group")
    for partner in result.partners:
        name = _child_name(root_group, partner.role)
        if partner.role == "ligand":
            recorder.create(
                name, partner.selection, result.run.source.state, "partner_ligand_copy"
            )
            cmd_api.show("sticks", name)
            cmd_api.color("orange", name)
        else:
            recorder.select(name, partner.selection, "partner_selection")
        recorder.group(partners_group, name, "partners_group")
    return partners_group


def _render_interface(
    cmd_api: Any,
    recorder: _OutputRecorder,
    result: InteractionResult,
    preset: str,
    root_group: str,
    partners_group: str,
) -> None:
    if preset not in {
        "ligand_contacts",
        "ligand_quick",
        "ligand_site",
        "plip_overview",
        "plip_publication",
    }:
        return
    parameters = result.run.parameters
    name = _child_name(root_group, "interface")
    recorder.create(
        name,
        str(parameters["interface_selection"]),
        result.run.source.state,
        "interface_copy",
    )
    cmd_api.show("sticks", name)
    if preset in {"ligand_site", "plip_publication"}:
        _apply_property_colors(cmd_api, name)
        if _as_bool(parameters.get("surface", False)):
            cmd_api.show("surface", name)
            cmd_api.set("transparency", 0.25, name)
        if _as_bool(parameters.get("labels", False)):
            cmd_api.label("(%s) and name CA" % name, "resn+resi")
            cmd_api.set("label_color", "black", name)
            cmd_api.set("label_size", 10, name)
    else:
        cmd_api.color("cyan", name)
    recorder.group(partners_group, name, "partners_group")


def _render_site_groups(
    cmd_api: Any,
    recorder: _OutputRecorder,
    result: InteractionResult,
    root_group: str,
) -> List[str]:
    groups = []
    for site in result.sites:
        site_group = _child_name(root_group, "site_%s" % site.site_id)
        recorder.group(site_group, "", "site_group")
        by_type: Dict[str, List[Interaction]] = defaultdict(list)
        for interaction in site.interactions:
            category = interaction.type
            if interaction.type == "hydrogen_bond" and interaction.subtype in {
                "good",
                "weak",
            }:
                category = "polar_%s" % interaction.subtype
            elif interaction.type == "polar_clash":
                category = {
                    "donor_donor": "donor_clash",
                    "acceptor_acceptor": "acceptor_clash",
                }.get(interaction.subtype, "polar_clash")
            by_type[category].append(interaction)
        for interaction_type in sorted(by_type):
            interactions = sorted(by_type[interaction_type], key=lambda item: item.id)
            measurement = _child_name(
                root_group, "%s_%s" % (site.site_id, interaction_type)
            )
            recorder.distance(
                measurement,
                interactions,
                result.run.source.object,
                result.run.source.state,
            )
            cmd_api.set(
                "dash_color",
                _INTERACTION_COLORS.get(interaction_type, "yellow"),
                measurement,
            )
            cmd_api.set("dash_width", 2.0, measurement)
            cmd_api.hide("labels", measurement)
            recorder.group(site_group, measurement, "site_group")
        groups.append(site_group)
    return groups


def _validate_rendered_ids(
    manifest: OutputManifest, result: InteractionResult
) -> None:
    rendered = {
        interaction_id
        for entry in manifest.created
        if entry.kind == "measurement"
        for interaction_id in entry.interaction_ids
    }
    expected = {interaction.id for interaction in result.interactions}
    if rendered != expected:
        raise RuntimeError(
            "renderer/result mismatch: rendered=%r expected=%r"
            % (sorted(rendered), sorted(expected))
        )


def _render_result(
    cmd_api: Any,
    result: InteractionResult,
    preset: str,
    root_group: str,
    name_root: str = "",
) -> OutputManifest:
    recorder = _OutputRecorder(cmd_api, _existing_names(cmd_api))
    child_root = str(name_root).strip() or root_group
    try:
        partners_group = _render_partners(cmd_api, recorder, result, child_root)
        _render_interface(
            cmd_api, recorder, result, preset, child_root, partners_group
        )
        site_groups = _render_site_groups(cmd_api, recorder, result, child_root)
        helpers_group = _child_name(child_root, "helpers")
        recorder.group(helpers_group, "", "helpers_group")
        if hasattr(cmd_api, "pseudoatom"):
            carrier = _child_name(child_root, "manifest")
            recorder.pseudoatom(carrier, "manifest_carrier")
            cmd_api.hide("everything", carrier)
            cmd_api.disable(carrier)
            recorder.group(helpers_group, carrier, "helpers_group")
        cmd_api.disable(helpers_group)
        recorder.group(root_group, "", "root_group")
        for member in [partners_group] + site_groups + [helpers_group]:
            recorder.group(root_group, member, "root_group")
        try:
            source_objects = tuple(
                dict.fromkeys(
                    str(name)
                    for name in cmd_api.get_object_list(
                        "(%s)" % result.run.source.object
                    )
                )
            )
        except Exception:
            source_objects = tuple(
                sorted(
                    {
                        participant.atom.object
                        for interaction in result.interactions
                        for participant in interaction.participants
                        if participant.atom.object
                    }
                )
            )
        manifest = OutputManifest.create(
            result.run.id,
            root_group,
            result.run.source.object,
            preset,
            recorder.entries,
            source_objects=source_objects,
            input_hash=result.run.source.sha256,
            parameters=result.run.parameters,
            stale_fingerprint=result.run.source.sha256,
        )
        _validate_rendered_ids(manifest, result)
        return manifest
    except Exception:
        recorder.cleanup()
        raise


@dataclass(frozen=True)
class InteractionRunRecord:
    result: Optional[InteractionResult]
    manifest: OutputManifest
    retired_manifests: Tuple[OutputManifest, ...] = ()

    @property
    def owner_reference(self) -> str:
        return _interaction_owner_reference(
            self.manifest.root_group, self.manifest.run_id
        )


class StaleInteractionOwnerError(RuntimeError):
    """An undo reference points to a run replaced within the same group."""

    status = "stale"


class InteractionClearError(RuntimeError):
    """Exact clear removed only part of a manifest and remains retryable."""

    status = "partial_error"
    scene_preserved = False

    def __init__(self, owner_reference: str, errors: Sequence[str]):
        self.owner_reference = str(owner_reference)
        self.errors = tuple(str(value) for value in errors)
        super().__init__(
            "interaction clear was partial; retry %s after fixing: %s"
            % (self.owner_reference, "; ".join(self.errors))
        )


def _interaction_owner_reference(group: str, run_id: str) -> str:
    group_value = str(group).strip()
    run_value = str(run_id).strip()
    if (
        not _OUTPUT_NAME.match(group_value)
        or not _OWNER_RUN_ID.match(run_value)
    ):
        raise ValueError("interaction owner reference requires safe group and run_id")
    return "%s%s:%s" % (_OWNER_REFERENCE_PREFIX, run_value, group_value)


def _parse_interaction_owner_reference(value: Any) -> Optional[Tuple[str, str]]:
    text = str(value).strip()
    if not text.startswith(_OWNER_REFERENCE_PREFIX):
        return None
    payload = text[len(_OWNER_REFERENCE_PREFIX) :]
    run_id, separator, group = payload.partition(":")
    if (
        not separator
        or not _OWNER_RUN_ID.match(run_id)
        or not _OUTPUT_NAME.match(group)
    ):
        raise ValueError("invalid interaction owner reference: %s" % text)
    return group, run_id


def register_interaction_completion_listener(callback: Any) -> str:
    """Register a main-thread owner event listener for local/remote commits."""

    if not callable(callback):
        raise TypeError("interaction completion listener must be callable")
    token = "interaction-listener-" + uuid.uuid4().hex[:12]
    _COMPLETION_LISTENERS[token] = callback
    return token


def unregister_interaction_completion_listener(token: Any) -> bool:
    """Remove one listener without affecting interaction controller state."""

    return _COMPLETION_LISTENERS.pop(str(token).strip(), None) is not None


def _completion_event(
    record: InteractionRunRecord, replaced_run_id: str = ""
) -> Dict[str, Any]:
    result = record.result
    counts = (
        dict(result.summary.get("counts_by_type", {}))
        if result is not None
        else {}
    )
    backend = result.run.backend.name if result is not None else ""
    replaced_owner_reference = (
        _interaction_owner_reference(
            record.manifest.root_group, str(replaced_run_id)
        )
        if str(replaced_run_id)
        else ""
    )
    return {
        "schema": _COMPLETION_EVENT_SCHEMA,
        "status": "completed",
        "group": record.manifest.root_group,
        "run_id": record.manifest.run_id,
        "owner_reference": record.owner_reference,
        "preset": record.manifest.preset,
        "backend": backend,
        "source": record.manifest.source,
        "counts": counts,
        "counts_by_type": counts,
        "replaced_run_id": str(replaced_run_id),
        "replaced_owner_reference": replaced_owner_reference,
        "retired_cleanup_count": len(record.retired_manifests),
    }


def _emit_interaction_completion(
    record: InteractionRunRecord, replaced_run_id: str = ""
) -> Dict[str, Any]:
    event = _completion_event(record, replaced_run_id)
    for token, callback in tuple(_COMPLETION_LISTENERS.items()):
        try:
            payload = dict(event)
            payload["counts"] = dict(event["counts"])
            payload["counts_by_type"] = dict(event["counts_by_type"])
            callback(payload)
        except Exception as exc:
            print(
                "Warning: interaction completion listener %s failed: %s"
                % (token, exc)
            )
    return event


class RemoteInteractionStaleError(RuntimeError):
    """Raised when an asynchronous result no longer matches its source."""

    status = "stale"


@dataclass(frozen=True)
class RemoteInteractionSubmission:
    request: Mapping[str, Any]
    preset: str
    group: str
    output_json: str
    replace: bool
    ligand_identities: Tuple[Tuple[str, int], ...]
    source_selection: str = ""
    source_objects: Tuple[str, ...] = ()
    source_state: int = 1
    source_fingerprint: str = ""
    output_group: str = ""


def _remote_source_snapshot(
    cmd_api: Any, source: str, state: int
) -> Tuple[Tuple[str, ...], str]:
    atoms, _ = _source_atoms(cmd_api, source, state)
    source_objects = tuple(
        sorted(
            {
                str(getattr(atom, "model", ""))
                for atom in atoms
                if str(getattr(atom, "model", ""))
            }
        )
    )
    payload = json.dumps(
        {
            "source_selection": source,
            "source_objects": list(source_objects),
            "source_state": state,
            "atom_fingerprint": _source_sha256(atoms, state),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return source_objects, hashlib.sha256(payload).hexdigest()


def _filter_result_to_ligand(
    result: InteractionResult,
    ligand_identities: Iterable[Tuple[str, int]],
) -> InteractionResult:
    selected = set(ligand_identities)
    if not selected:
        raise ValueError("remote ligand identity filter is empty")
    sites = []
    removed_interactions = 0
    for site in result.sites:
        kept = tuple(
            interaction
            for interaction in site.interactions
            if any(
                (participant.atom.object, participant.atom.source_index) in selected
                for participant in interaction.participants
            )
        )
        removed_interactions += len(site.interactions) - len(kept)
        if kept:
            sites.append(InteractionSite(site.site_id, kept))
    warnings = list(result.warnings)
    if removed_interactions:
        warnings.append(
            "host ligand filter removed %d interaction(s) from other PLIP sites"
            % removed_interactions
        )
    if not sites:
        warnings.append("PLIP found no interactions for the exact ligand selection")
    provenance = dict(result.provenance)
    provenance["host_ligand_filter"] = {
        "source_identity_count": len(selected),
        "removed_interactions": removed_interactions,
        "kept_sites": len(sites),
    }
    return InteractionResult.create(
        run=result.run,
        partners=result.partners,
        sites=sites,
        warnings=warnings,
        provenance=provenance,
        raw=result.raw,
    )


def _state_number(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("state must be an integer >= 1")
    try:
        state = int(value)
    except (TypeError, ValueError):
        raise ValueError("state must be an integer >= 1")
    if state < 1 or (isinstance(value, float) and not value.is_integer()):
        raise ValueError("state must be an integer >= 1")
    return state


def _effective_parameters(definition: Any, supplied: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    effective = preset_parameters(definition)
    overrides = dict(supplied or {})
    unknown = sorted(set(overrides) - set(effective))
    if unknown:
        raise ValueError(
            "unsupported parameters for %s: %s"
            % (definition.name, ", ".join(unknown))
        )
    effective.update(overrides)
    for key, parameter in definition.parameters.items():
        parameter_type = str(parameter.get("type", ""))
        if parameter_type == "boolean":
            effective[key] = _as_bool(effective[key])
        elif parameter_type == "number":
            effective[key] = _cutoff(effective[key], key)
    return effective


def _compute_local_result(
    cmd_api: Any,
    run_id: str,
    canonical: str,
    source: str,
    partner1: str,
    partner2: str,
    state: int,
    parameters: Mapping[str, Any],
) -> InteractionResult:
    if canonical == "interchain_basic":
        return _build_interchain_result(
            cmd_api, run_id, source, partner1, partner2, state, parameters
        )
    if canonical == "polar_qc":
        return _build_polar_result(
            cmd_api, run_id, source, partner1, partner2, state, parameters
        )
    return _build_ligand_result(
        cmd_api, run_id, canonical, source, partner1, partner2, state, parameters
    )


def _root_group_name(source: str, preset: str, run_id: str, group: str) -> str:
    if not group:
        return "ppi_%s_%s_%s" % (
            _safe_component(source, 34),
            preset,
            run_id[:8],
        )
    value = str(group).strip()
    if len(value) > 120 or not _OUTPUT_NAME.match(value):
        raise ValueError(
            "group must be a PyMOL-safe name matching %s and <= 120 characters"
            % _OUTPUT_NAME.pattern
        )
    return value


@dataclass(frozen=True)
class _StagedInteractionExport:
    target: str
    temporary: str


def _unused_scene_name(cmd_api: Any, prefix: str) -> str:
    existing = set(_existing_names(cmd_api))
    while True:
        candidate = "%s_%s" % (prefix, uuid.uuid4().hex[:12])
        if candidate not in existing:
            return candidate


def _interaction_output_target(filename: str, allow_empty: bool = False) -> str:
    raw = str(filename).strip()
    if not raw:
        if allow_empty:
            return ""
        raise ValueError("output_json must identify a JSON file")
    target = os.path.abspath(os.path.expanduser(raw))
    if os.path.exists(target) and not os.path.isfile(target):
        raise ValueError("output_json must identify a regular JSON file")
    parent = os.path.dirname(target) or os.getcwd()
    if not os.path.isdir(parent):
        raise ValueError("output directory does not exist: %s" % parent)
    if not os.access(parent, os.W_OK):
        raise ValueError("output directory is not writable: %s" % parent)
    return target


def _stage_result_export(
    result: InteractionResult, filename: str
) -> Optional[_StagedInteractionExport]:
    target = _interaction_output_target(filename, allow_empty=True)
    if not target:
        return None
    temporary = "%s.tmp.%s" % (target, uuid.uuid4().hex[:8])
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(result.to_json())
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return _StagedInteractionExport(target=target, temporary=temporary)


def _discard_staged_export(staged: Optional[_StagedInteractionExport]) -> None:
    if staged is not None and os.path.exists(staged.temporary):
        os.unlink(staged.temporary)


def _manifest_with_root_name(
    manifest: OutputManifest, old_root: str, new_root: str
) -> OutputManifest:
    created = tuple(
        ManifestEntry(
            new_root if entry.name == old_root else entry.name,
            entry.kind,
            entry.role,
            entry.interaction_ids,
        )
        for entry in manifest.created
    )
    return OutputManifest.create(
        manifest.run_id,
        new_root,
        manifest.source,
        manifest.preset,
        created,
        source_objects=manifest.source_objects,
        input_hash=manifest.input_hash,
        parameters=manifest.parameters,
        stale_fingerprint=manifest.stale_fingerprint,
    )


def _delete_manifest_outputs(
    cmd_api: Any,
    manifest: OutputManifest,
    renamed_root: str = "",
) -> Tuple[str, ...]:
    errors = []
    for name in manifest.delete_order:
        target = renamed_root if name == manifest.root_group and renamed_root else name
        try:
            cmd_api.delete(target)
        except Exception as exc:
            errors.append("%s: %s" % (target, exc))
    return tuple(errors)


def _manifest_carrier_name(manifest: OutputManifest) -> str:
    return next(
        (
            entry.name
            for entry in manifest.created
            if entry.role == "manifest_carrier" and entry.kind == "object"
        ),
        "",
    )


def _persist_scene_manifest(
    cmd_api: Any,
    manifest: OutputManifest,
    cleanup_state: str = "active",
    retired_manifests: Sequence[OutputManifest] = (),
) -> bool:
    carrier = _manifest_carrier_name(manifest)
    if not carrier:
        return False
    payload = json.dumps(
        {
            "schema": _MANIFEST_CARRIER_SCHEMA,
            "carrier": carrier,
            "cleanup_state": str(cleanup_state),
            "manifest": manifest.to_dict(),
            "retired_manifests": [
                value.to_dict() for value in retired_manifests
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    if hasattr(cmd_api, "set_property") and hasattr(cmd_api, "get_property"):
        try:
            cmd_api.set_property(
                _MANIFEST_PROPERTY,
                payload,
                object=carrier,
                state=0,
                proptype=6,
                quiet=1,
            )
            restored = cmd_api.get_property(
                _MANIFEST_PROPERTY, carrier, state=0, quiet=1
            )
            if str(restored or "") == payload:
                return True
        except Exception:
            pass
    if hasattr(cmd_api, "set_title") and hasattr(cmd_api, "get_title"):
        try:
            cmd_api.set_title(carrier, 1, payload)
            if str(cmd_api.get_title(carrier, 1) or "") == payload:
                return True
        except Exception:
            pass
    return False


def _read_scene_manifest_envelope(
    cmd_api: Any, carrier: str
) -> Optional[Tuple[InteractionRunRecord, str]]:
    payloads = []
    if hasattr(cmd_api, "get_property"):
        try:
            payloads.append(
                cmd_api.get_property(
                    _MANIFEST_PROPERTY, carrier, state=0, quiet=1
                )
            )
        except Exception:
            pass
    if hasattr(cmd_api, "get_title"):
        try:
            payloads.append(cmd_api.get_title(carrier, 1))
        except Exception:
            pass
    for raw in payloads:
        if not isinstance(raw, str) or not raw:
            continue
        try:
            payload = json.loads(raw)
            if payload.get("schema") != _MANIFEST_CARRIER_SCHEMA:
                continue
            if payload.get("carrier") != carrier:
                continue
            manifest = OutputManifest.from_dict(payload.get("manifest"))
            if _manifest_carrier_name(manifest) != carrier:
                continue
            cleanup_state = str(payload.get("cleanup_state", "active"))
            if cleanup_state not in {"active", "clearing"}:
                continue
            retired_raw = payload.get("retired_manifests", ())
            if isinstance(retired_raw, (str, bytes)) or not isinstance(
                retired_raw, Sequence
            ):
                continue
            retired = tuple(
                OutputManifest.from_dict(value) for value in retired_raw
            )
            return InteractionRunRecord(None, manifest, retired), cleanup_state
        except Exception:
            continue
    return None


def _read_scene_manifest(cmd_api: Any, carrier: str) -> Optional[OutputManifest]:
    envelope = _read_scene_manifest_envelope(cmd_api, carrier)
    return envelope[0].manifest if envelope is not None else None


def _recover_scene_manifests(cmd_api: Any) -> Dict[str, InteractionRunRecord]:
    existing = set(_existing_names(cmd_api))
    candidates: Dict[
        str, List[Tuple[InteractionRunRecord, str]]
    ] = defaultdict(list)
    for carrier in sorted(name for name in existing if name.endswith("__manifest")):
        envelope = _read_scene_manifest_envelope(cmd_api, carrier)
        if envelope is None:
            continue
        record, cleanup_state = envelope
        candidates[record.manifest.root_group].append((record, cleanup_state))
    recovered: Dict[str, InteractionRunRecord] = {}
    for root_group, values in candidates.items():
        complete = [
            record
            for record, _ in values
            if set(record.manifest.created_names).issubset(existing)
        ]
        if len(complete) == 1:
            recovered[root_group] = complete[0]
            continue
        clearing = [
            record
            for record, cleanup_state in values
            if cleanup_state == "clearing"
        ]
        if not complete and len(clearing) == 1:
            # Only an explicit clear-in-progress marker authorizes recovery
            # from missing manifest members. An externally damaged active
            # manifest remains unowned instead of risking a same-name delete.
            recovered[root_group] = clearing[0]
        # Multiple complete/clearing carriers for one root are ambiguous;
        # never guess which run owns a same-name scene.
    return recovered


def _retryable_clear_order(manifest: OutputManifest) -> Tuple[str, ...]:
    """Delete ordinary outputs first and persistent ownership anchors last."""

    carrier = _manifest_carrier_name(manifest)
    anchors = {manifest.root_group}
    if carrier:
        anchors.add(carrier)
    ordinary = [name for name in manifest.delete_order if name not in anchors]
    ordered_anchors = []
    if manifest.root_group in manifest.created_names:
        ordered_anchors.append(manifest.root_group)
    if carrier and carrier != manifest.root_group:
        ordered_anchors.append(carrier)
    return tuple(ordinary + ordered_anchors)


def _clear_manifest_outputs(
    cmd_api: Any, manifest: OutputManifest
) -> Tuple[str, ...]:
    """Best-effort exact deletion while preserving retry anchors on failure."""

    errors = []
    carrier = _manifest_carrier_name(manifest)
    anchors = {manifest.root_group, carrier}
    for name in _retryable_clear_order(manifest):
        if name in anchors and errors:
            continue
        try:
            cmd_api.delete(name)
        except Exception as exc:
            errors.append("%s: %s" % (name, exc))
    return tuple(errors)


class InteractionController:
    """Compute, render, export, and exactly clear local interaction runs."""

    def __init__(
        self,
        cmd_api: Any,
        runs: Optional[Dict[str, InteractionRunRecord]] = None,
    ) -> None:
        if cmd_api is None:
            raise RuntimeError("PyMOL cmd API is unavailable")
        self.cmd = cmd_api
        self._runs = runs if isinstance(runs, dict) else {}
        self.recover_scene_manifests()
        try:
            setattr(cmd_api, _CMD_RUNS_ATTR, self._runs)
        except (AttributeError, TypeError):
            pass

    def recover_scene_manifests(self) -> Tuple[str, ...]:
        """Merge manifest carriers loaded after plugin/controller creation."""

        recovered = _recover_scene_manifests(self.cmd)
        added = []
        for root_group, record in recovered.items():
            if root_group not in self._runs:
                self._runs[root_group] = record
                added.append(root_group)
        return tuple(sorted(added))

    def list_presets(self) -> Tuple[str, ...]:
        return PRESETS.names()

    def describe(self, preset: str) -> Dict[str, Any]:
        return PRESETS.resolve(preset).to_dict()

    def owner_reference(self, group: str) -> str:
        """Return the run-qualified owner reference for one current group."""

        group_value = str(group).strip()
        parsed = _parse_interaction_owner_reference(group_value)
        if parsed is not None:
            group_value, expected_run_id = parsed
        else:
            expected_run_id = ""
        try:
            record = self._runs[group_value]
        except KeyError:
            raise KeyError("unknown interaction group %r" % group)
        current_run_id = record.manifest.run_id
        if expected_run_id and expected_run_id != current_run_id:
            raise StaleInteractionOwnerError(
                "interaction owner is stale: group %s now contains run %s, not %s; "
                "nothing was deleted"
                % (group_value, current_run_id, expected_run_id)
            )
        return record.owner_reference

    def _preflight_group(
        self, root_group: str, replace: Any
    ) -> Optional[InteractionRunRecord]:
        old_record = self._runs.get(root_group)
        if old_record is not None and not _as_bool(replace):
            raise ValueError("interaction group already exists: %s" % root_group)
        existing = set(_existing_names(self.cmd))
        if old_record is not None:
            missing = sorted(set(old_record.manifest.created_names) - existing)
            if missing:
                raise ValueError(
                    "owned interaction output is incomplete (%s); refusing unsafe "
                    "replacement" % ", ".join(missing)
                )
        elif root_group in existing:
            raise ValueError(
                "refusing to replace unowned PyMOL name %s (no exact manifest)"
                % root_group
            )
        return old_record

    def run(
        self,
        preset: str,
        source: str,
        partner1: str = "",
        partner2: str = "",
        state: int = 1,
        group: str = "",
        output_json: str = "",
        replace: Any = True,
        parameters: Optional[Mapping[str, Any]] = None,
    ) -> InteractionRunRecord:
        definition = PRESETS.resolve(preset)
        if definition.backend != "pymol-local":
            raise ValueError(
                "%s uses %s; call prepare_remote and commit_remote_result"
                % (definition.name, definition.backend)
            )
        canonical = definition.name
        source_value = str(source).strip()
        if not source_value:
            raise ValueError("source must not be empty")
        state_value = _state_number(state)
        _interaction_output_target(str(output_json), allow_empty=True)
        requested_group = str(group).strip()
        if requested_group:
            planned_group = _root_group_name(
                source_value, canonical, "pending", requested_group
            )
            self._preflight_group(planned_group, replace)
        effective = _effective_parameters(definition, parameters)
        run_id = uuid.uuid4().hex
        result = _compute_local_result(
            self.cmd,
            run_id,
            canonical,
            source_value,
            str(partner1).strip(),
            str(partner2).strip(),
            state_value,
            effective,
        )
        return self.commit_result(
            canonical,
            result,
            group=group,
            output_json=output_json,
            replace=replace,
        )

    def preview(
        self,
        preset: str,
        source: str,
        partner1: str = "",
        partner2: str = "",
        state: int = 1,
        group: str = "",
        output_json: str = "",
        replace: Any = True,
        parameters: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Validate and compute a read-only interaction plan.

        Local presets run the same contact computation as :meth:`run` but do
        not render or export.  PLIP presets build and validate the exact Host
        request without performing HTTP or reserving an asynchronous output.
        """

        definition = PRESETS.resolve(preset)
        canonical = definition.name
        source_value = str(source).strip()
        if not source_value:
            raise ValueError("source must not be empty")
        state_value = _state_number(state)
        replace_value = _as_bool(replace)
        _interaction_output_target(str(output_json), allow_empty=True)
        if definition.backend == "plip-wsl":
            submission = self.prepare_remote(
                canonical,
                source_value,
                partner1=partner1,
                partner2=partner2,
                state=state_value,
                group=group,
                output_json=output_json,
                replace=replace_value,
                parameters=parameters,
            )
            request = submission.request
            return {
                "schema": _PREVIEW_SCHEMA,
                "status": "preview",
                "scene_mutated": False,
                "backend_contacted": False,
                "preset": canonical,
                "backend": definition.backend,
                "source": source_value,
                "state": state_value,
                "group": submission.output_group,
                "run_id": str(request["run_id"]),
                "partners": [dict(value) for value in request.get("partners", ())],
                "parameters": dict(request.get("parameters", {})),
                "summary": {
                    "result_available": False,
                    "counts_by_type": None,
                },
                "warnings": [
                    "PLIP request validated locally; the worker was not contacted"
                ],
            }

        requested_group = str(group).strip()
        run_id = uuid.uuid4().hex
        root_group = _root_group_name(
            source_value, canonical, run_id, requested_group
        )
        self._preflight_group(root_group, replace_value)
        effective = _effective_parameters(definition, parameters)
        result = _compute_local_result(
            self.cmd,
            run_id,
            canonical,
            source_value,
            str(partner1).strip(),
            str(partner2).strip(),
            state_value,
            effective,
        )
        return {
            "schema": _PREVIEW_SCHEMA,
            "status": "preview",
            "scene_mutated": False,
            "backend_contacted": False,
            "preset": canonical,
            "backend": definition.backend,
            "source": source_value,
            "state": state_value,
            "group": root_group,
            "run_id": result.run.id,
            "partners": [value.to_dict() for value in result.partners],
            "parameters": dict(result.run.parameters),
            "summary": dict(result.summary),
            "warnings": list(result.warnings),
        }

    def commit_result(
        self,
        preset: str,
        result: InteractionResult,
        group: str = "",
        output_json: str = "",
        replace: Any = True,
    ) -> InteractionRunRecord:
        definition = PRESETS.resolve(preset)
        canonical = definition.name
        result.validate()
        result_preset = str(result.run.parameters.get("preset", ""))
        if result_preset != canonical:
            raise ValueError(
                "result preset mismatch: expected %s, got %s"
                % (canonical, result_preset or "(missing)")
            )
        root_group = _root_group_name(
            result.run.source.object, canonical, result.run.id, group
        )
        # Validate journal identity before any scene or filesystem mutation.
        _interaction_owner_reference(root_group, result.run.id)
        old_record = self._preflight_group(root_group, replace)
        if not hasattr(self.cmd, "set_name"):
            raise RuntimeError(
                "Host PyMOL lacks cmd.set_name required for transactional "
                "interaction output"
            )
        staged_export = _stage_result_export(result, str(output_json))
        stage_root = _unused_scene_name(self.cmd, "__pp_interact_stage")
        backup_root = _unused_scene_name(self.cmd, "__pp_interact_backup")
        token = uuid.uuid4().hex[:6]
        name_root = _child_name(
            root_group, "run_%s_%s" % (result.run.id[:8], token)
        )
        staged_manifest = None
        new_is_final = False
        old_is_backup = False
        try:
            staged_manifest = _render_result(
                self.cmd,
                result,
                canonical,
                stage_root,
                name_root=name_root,
            )
            manifest = _manifest_with_root_name(
                staged_manifest, stage_root, root_group
            )
            if old_record is not None:
                self.cmd.set_name(root_group, backup_root)
                old_is_backup = True
            self.cmd.set_name(stage_root, root_group)
            new_is_final = True
            if staged_export is not None:
                os.replace(staged_export.temporary, staged_export.target)
            record = InteractionRunRecord(result=result, manifest=manifest)
        except Exception as exc:
            rollback_errors = []
            if new_is_final:
                try:
                    self.cmd.set_name(root_group, stage_root)
                    new_is_final = False
                except Exception as rollback_exc:
                    rollback_errors.append(
                        "%s->%s: %s" % (root_group, stage_root, rollback_exc)
                    )
            if old_is_backup:
                try:
                    self.cmd.set_name(backup_root, root_group)
                    old_is_backup = False
                except Exception as rollback_exc:
                    rollback_errors.append(
                        "%s->%s: %s" % (backup_root, root_group, rollback_exc)
                    )
            if staged_manifest is not None and not new_is_final:
                cleanup_errors = _delete_manifest_outputs(self.cmd, staged_manifest)
                rollback_errors.extend(cleanup_errors)
            _discard_staged_export(staged_export)
            if rollback_errors:
                raise RuntimeError(
                    "interaction transaction failed: %s; rollback errors: %s"
                    % (exc, "; ".join(rollback_errors))
                ) from exc
            raise
        retired_manifests = []
        if old_record is not None:
            cleanup_candidates = list(old_record.retired_manifests)
            cleanup_candidates.append(
                _manifest_with_root_name(
                    old_record.manifest, root_group, backup_root
                )
            )
            cleanup_failures = []
            for retired in cleanup_candidates:
                errors = _delete_manifest_outputs(self.cmd, retired)
                if errors:
                    retired_manifests.append(retired)
                    cleanup_failures.extend(errors)
            if cleanup_failures:
                print(
                    "Warning: replacement committed; prior output cleanup remains "
                    "owned and will retry on clear/replace: %s"
                    % "; ".join(cleanup_failures)
                )
        record = InteractionRunRecord(
            result=result,
            manifest=manifest,
            retired_manifests=tuple(retired_manifests),
        )
        if not _persist_scene_manifest(
            self.cmd,
            manifest,
            retired_manifests=record.retired_manifests,
        ):
            print(
                "Warning: Host could not persist the interaction manifest in the "
                "scene; clear/replace remains available until this PyMOL session ends"
            )
        self._runs[root_group] = record
        _emit_interaction_completion(
            record,
            old_record.manifest.run_id if old_record is not None else "",
        )
        return record

    def prepare_remote(
        self,
        preset: str,
        source: str,
        partner1: str = "",
        partner2: str = "",
        state: int = 1,
        group: str = "",
        output_json: str = "",
        replace: Any = True,
        parameters: Optional[Mapping[str, Any]] = None,
    ) -> RemoteInteractionSubmission:
        definition = PRESETS.resolve(preset)
        if definition.backend != "plip-wsl":
            raise ValueError("prepare_remote requires a PLIP preset")
        source_value = str(source).strip()
        if not source_value:
            raise ValueError("source must not be empty")
        state_value = _state_number(state)
        replace_value = _as_bool(replace)
        output_target = _interaction_output_target(
            str(output_json), allow_empty=True
        )
        effective = _effective_parameters(definition, parameters)
        site_cutoff = _cutoff(effective["site_cutoff"], "site_cutoff")
        ligand, receptor = _ligand_partner_selections(
            source_value, str(partner1).strip(), str(partner2).strip()
        )
        ligand_atoms = list(getattr(_get_model(self.cmd, ligand, state_value), "atom", ()))
        if not ligand_atoms:
            raise ValueError(
                "PLIP ligand selection has no atoms; pass the ligand as partner2"
            )
        if not str(partner2).strip():
            ligand_residues = {_residue_key(atom) for atom in ligand_atoms}
            if len(ligand_residues) > 1:
                raise ValueError(
                    "default organic matches %d ligand residues; PLIP requires an "
                    "explicit ligand selection (use ligand=... in pp_interact_run)"
                    % len(ligand_residues)
                )
        receptor_count = _count_atoms(self.cmd, receptor, state_value)
        if receptor_count == 0:
            raise ValueError(
                "PLIP receptor selection has no atoms after excluding the ligand"
            )
        partners = (
            Partner("receptor", receptor, receptor_count),
            Partner("ligand", ligand, len(ligand_atoms)),
        )
        effective.update(
            {
                "preset": definition.name,
                "partner1": str(partner1).strip(),
                "partner2": str(partner2).strip(),
                "ligand_selection": ligand,
                "receptor_selection": receptor,
                "interface_selection": "(byres ((%s) within %.6g of (%s)))"
                % (receptor, site_cutoff, ligand),
            }
        )
        requested_group = str(group).strip()
        if requested_group:
            explicit_output_group = _root_group_name(
                source_value, definition.name, "pending", requested_group
            )
            self._preflight_group(explicit_output_group, replace_value)
        source_objects, source_fingerprint = _remote_source_snapshot(
            self.cmd, source_value, state_value
        )
        request = build_plip_request_from_pymol(
            self.cmd,
            source_value,
            state=state_value,
            preset=definition.name,
            partners=tuple(value.to_dict() for value in partners),
            parameters=effective,
        )
        output_group = _root_group_name(
            source_value,
            definition.name,
            str(request["run_id"]),
            str(group),
        )
        _interaction_owner_reference(output_group, str(request["run_id"]))
        self._preflight_group(output_group, replace_value)
        return RemoteInteractionSubmission(
            request=request,
            preset=definition.name,
            group=str(group),
            output_json=output_target,
            replace=replace_value,
            ligand_identities=tuple(sorted({_atom_key(atom) for atom in ligand_atoms})),
            source_selection=source_value,
            source_objects=source_objects,
            source_state=state_value,
            source_fingerprint=source_fingerprint,
            output_group=output_group,
        )

    def _require_remote_source_current(
        self, submission: RemoteInteractionSubmission
    ) -> None:
        source = str(submission.source_selection).strip()
        fingerprint = str(submission.source_fingerprint).strip()
        if not source or not fingerprint:
            raise RemoteInteractionStaleError(
                "remote submission has no current-source snapshot"
            )
        try:
            state = _state_number(submission.source_state)
            request_source = submission.request["source"]
            if not isinstance(request_source, Mapping):
                raise ValueError("submitted source contract is not an object")
            if str(request_source.get("object", "")).strip() != source:
                raise ValueError("submitted source selection changed")
            if _state_number(request_source.get("state")) != state:
                raise ValueError("submitted source state changed")
            source_objects, current_fingerprint = _remote_source_snapshot(
                self.cmd, source, state
            )
        except Exception as exc:
            if isinstance(exc, RemoteInteractionStaleError):
                raise
            raise RemoteInteractionStaleError(
                "remote interaction source is no longer available: %s" % exc
            ) from exc
        if source_objects != tuple(submission.source_objects):
            raise RemoteInteractionStaleError(
                "remote interaction source objects changed while the job was running"
            )
        if current_fingerprint != fingerprint:
            raise RemoteInteractionStaleError(
                "remote interaction source changed while the job was running"
            )

    def commit_remote_result(
        self,
        submission: RemoteInteractionSubmission,
        payload: Mapping[str, Any],
        *,
        expected_current: bool = False,
    ) -> InteractionRunRecord:
        if expected_current:
            self._require_remote_source_current(submission)
        result = InteractionResult.from_dict(payload)
        if result.run.id != submission.request["run_id"]:
            raise ValueError("remote result run id does not match submitted request")
        if result.run.source.to_dict() != submission.request["source"]:
            raise ValueError("remote result source identity does not match submitted request")
        result = _filter_result_to_ligand(result, submission.ligand_identities)
        return self.commit_result(
            submission.preset,
            result,
            group=submission.output_group or submission.group,
            output_json=submission.output_json,
            replace=submission.replace,
        )

    def get(self, group: str) -> InteractionRunRecord:
        try:
            return self._runs[str(group).strip()]
        except KeyError:
            raise KeyError("unknown interaction group %r" % group)

    def clear(self, group: str) -> OutputManifest:
        requested = str(group).strip()
        parsed = _parse_interaction_owner_reference(requested)
        if parsed is None:
            group_value = requested
            expected_run_id = ""
        else:
            group_value, expected_run_id = parsed
        try:
            record = self._runs[group_value]
        except KeyError:
            raise KeyError(
                "no exact interaction manifest for %r; nothing was deleted" % group
            )
        if expected_run_id and expected_run_id != record.manifest.run_id:
            raise StaleInteractionOwnerError(
                "interaction owner is stale: group %s now contains run %s, not %s; "
                "nothing was deleted"
                % (group_value, record.manifest.run_id, expected_run_id)
            )
        errors = []
        carrier = _manifest_carrier_name(record.manifest)
        if carrier and not _persist_scene_manifest(
            self.cmd,
            record.manifest,
            cleanup_state="clearing",
            retired_manifests=record.retired_manifests,
        ):
            print(
                "Warning: could not persist clear-in-progress state; retry remains "
                "available in this PyMOL process"
            )
        for retired in record.retired_manifests:
            errors.extend(_clear_manifest_outputs(self.cmd, retired))
        if not errors:
            errors.extend(_clear_manifest_outputs(self.cmd, record.manifest))
        if errors:
            # Keep the run record so the same exact owner reference can retry
            # missing/failed names. Never mark a partial deletion as preserved.
            raise InteractionClearError(record.owner_reference, errors)
        del self._runs[group_value]
        return record.manifest

    def export(self, group: str, filename: str) -> str:
        record = self.get(group)
        if record.result is None:
            raise ValueError(
                "interaction result rows are unavailable after scene-only manifest "
                "recovery; rerun the preset before exporting"
            )
        target = _interaction_output_target(filename)
        temporary = "%s.tmp.%s" % (target, uuid.uuid4().hex[:8])
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(record.result.to_json())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target


_CONTROLLER: Optional[InteractionController] = None


def get_controller(cmd_api: Any = None) -> InteractionController:
    global _CONTROLLER
    if cmd_api is not None:
        selected = cmd_api
    elif _PYMOL_CMD is not None:
        selected = _PYMOL_CMD
    else:
        selected = _CONTROLLER.cmd if _CONTROLLER is not None else None
    attached = getattr(selected, _CMD_CONTROLLER_ATTR, None) if selected is not None else None
    if (
        isinstance(attached, InteractionController)
        and getattr(attached, "cmd", None) is selected
    ):
        _CONTROLLER = attached
    elif _CONTROLLER is None or _CONTROLLER.cmd is not selected:
        runs = getattr(selected, _CMD_RUNS_ATTR, None) if selected is not None else None
        if not isinstance(runs, dict):
            attached_runs = getattr(attached, "_runs", None)
            runs = attached_runs if isinstance(attached_runs, dict) else None
        _CONTROLLER = InteractionController(selected, runs=runs)
        try:
            setattr(selected, _CMD_CONTROLLER_ATTR, _CONTROLLER)
            setattr(selected, _CMD_RUNS_ATTR, _CONTROLLER._runs)
        except (AttributeError, TypeError):
            # Some proxy cmd implementations may disallow custom attributes.
            pass
    _CONTROLLER.recover_scene_manifests()
    return _CONTROLLER


def _molecule_source_candidates(
    controller: InteractionController, state: int
) -> Tuple[str, ...]:
    """Return atom-bearing molecule objects, excluding groups/maps/selections."""

    cmd_api = controller.cmd
    managed_objects = {
        entry.name
        for record in controller._runs.values()
        for manifest in (record.manifest,) + record.retired_manifests
        for entry in manifest.created
        if entry.kind == "object"
    }
    try:
        names = tuple(str(name) for name in cmd_api.get_names("objects"))
    except (AttributeError, TypeError):
        names = ()
    if names and hasattr(cmd_api, "get_type"):
        candidates = []
        for name in names:
            if name in managed_objects or name.endswith("__manifest"):
                continue
            try:
                if str(cmd_api.get_type(name)) != "object:molecule":
                    continue
                if _count_atoms(cmd_api, name, state) > 0:
                    candidates.append(name)
            except Exception:
                continue
        return tuple(sorted(dict.fromkeys(candidates)))
    try:
        atoms = getattr(_get_model(cmd_api, "all", state), "atom", ())
    except Exception:
        atoms = ()
    return tuple(
        sorted(
            {
                str(getattr(atom, "model", ""))
                for atom in atoms
                if str(getattr(atom, "model", ""))
            }
        )
    )


def _resolve_public_source(
    controller: InteractionController, source: Any, state: int
) -> str:
    requested = str(source).strip()
    if requested:
        return requested
    candidates = _molecule_source_candidates(controller, state)
    if len(candidates) == 1:
        print("Using source=%s (the only molecule object with atoms)." % candidates[0])
        return candidates[0]
    if not candidates:
        raise ValueError(
            "no molecule object with atoms is loaded; load a protein-ligand complex "
            "and run pp_interact_run"
        )
    raise ValueError(
        "source is ambiguous; molecule objects: %s. Choose one with "
        "source=%s, or explicitly combine a known complex with source=\"(%s) or "
        "(%s)\""
        % (
            ", ".join(candidates),
            candidates[0],
            candidates[0],
            candidates[1],
        )
    )


def pp_interact_list() -> Tuple[str, ...]:
    controller = get_controller()
    names = controller.list_presets()
    for name in names:
        definition = PRESETS.resolve(name)
        print("%s | backend=%s | %s" % (name, definition.backend, definition.summary))
        if definition.examples:
            print("  Example: %s" % definition.examples[0])
        if definition.limitations:
            print("  Limit: %s" % definition.limitations[0])
    return names


def pp_interact_describe(preset: str) -> Dict[str, Any]:
    description = get_controller().describe(preset)
    print(json.dumps(description, indent=2, sort_keys=True))
    return description


def pp_interact_run(
    preset: str = "ligand_contacts",
    source: str = "",
    partner1: str = "",
    partner2: str = "",
    state: Any = 1,
    group: str = "",
    output_json: str = "",
    replace: Any = 1,
    surface: Any = 0,
    labels: Any = 0,
    good_cutoff: Any = "",
    weak_cutoff: Any = "",
    clash_cutoff: Any = "",
    site_cutoff: Any = "",
    async_mode: Any = 1,
    url: str = "",
    timeout: Any = 120.0,
    hbond_cutoff: Any = "",
    salt_cutoff: Any = "",
    contact_cutoff: Any = "",
    receptor: str = "",
    ligand: str = "",
) -> Any:
    parameters: Dict[str, Any] = {}
    canonical = PRESETS.resolve(preset).name
    partner1_value = str(partner1).strip()
    partner2_value = str(partner2).strip()
    receptor_value = str(receptor).strip()
    ligand_value = str(ligand).strip()
    if receptor_value or ligand_value:
        if canonical not in {
            "ligand_contacts",
            "ligand_quick",
            "ligand_site",
            "plip_overview",
            "plip_publication",
        }:
            raise ValueError("receptor/ligand aliases require a ligand preset")
        if receptor_value and partner1_value:
            raise ValueError("use receptor or partner1, not both")
        if ligand_value and partner2_value:
            raise ValueError("use ligand or partner2, not both")
        partner1_value = receptor_value or partner1_value
        partner2_value = ligand_value or partner2_value
    surface_value = _as_bool(surface)
    labels_value = _as_bool(labels)
    if canonical in {"ligand_site", "plip_publication"}:
        parameters.update({"surface": surface_value, "labels": labels_value})
    elif surface_value or labels_value:
        raise ValueError(
            "surface/labels are supported only by ligand_site and plip_publication"
        )
    supplied_cutoffs = {
        "good_cutoff": good_cutoff,
        "weak_cutoff": weak_cutoff,
        "clash_cutoff": clash_cutoff,
        "site_cutoff": site_cutoff,
        "hbond_cutoff": hbond_cutoff,
        "salt_cutoff": salt_cutoff,
        "contact_cutoff": contact_cutoff,
    }
    for name, value in supplied_cutoffs.items():
        if value != "":
            parameters[name] = value
    controller = get_controller()
    state_value = _state_number(state)
    source_value = _resolve_public_source(controller, source, state_value)
    group_value = str(group).strip() or "ppi_%s_%s" % (
        _safe_component(source_value, 34),
        canonical,
    )
    definition = PRESETS.resolve(canonical)
    if definition.backend == "plip-wsl":
        submission = controller.prepare_remote(
            canonical,
            source_value,
            partner1=partner1_value,
            partner2=partner2_value,
            state=state_value,
            group=group_value,
            output_json=output_json,
            replace=replace,
            parameters=parameters,
        )
        timeout_value = float(timeout)
        if not math.isfinite(timeout_value) or timeout_value < 0.1:
            raise ValueError("timeout must be a finite number >= 0.1 seconds")
        if _as_bool(async_mode):
            try:
                from pymolplugins.remote.commands import submit_plip_interaction
            except ImportError:
                from remote_metrics import submit_plip_interaction  # type: ignore
            return submit_plip_interaction(submission, str(url), timeout_value)
        try:
            from pymolplugins.remote.client import worker_request
        except ImportError:
            _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            if _ROOT not in sys.path:
                sys.path.insert(0, _ROOT)
            from pymolplugins.remote.client import worker_request  # type: ignore
        payload = worker_request(
            "/v1/interactions/plip",
            submission.request,
            url=str(url),
            timeout=timeout_value,
            expected_schema="pymolplugins.interactions/1.0",
        )
        record = controller.commit_remote_result(submission, payload)
    else:
        record = controller.run(
            canonical,
            source_value,
            partner1=partner1_value,
            partner2=partner2_value,
            state=state_value,
            group=group_value,
            output_json=output_json,
            replace=replace,
            parameters=parameters,
        )
    counts = dict(record.result.summary["counts_by_type"])
    status = "ready" if counts else "empty"
    print(
        "Interaction preset %s: status=%s group=%s counts=%s"
        % (canonical, status, record.manifest.root_group, counts)
    )
    for warning in record.result.warnings:
        print("Warning: %s" % warning)
    print("Next: pp_interact_export %s, interactions.json" % record.manifest.root_group)
    print("Clear: pp_interact_clear %s" % record.manifest.root_group)
    return record.manifest.root_group


def pp_interact_clear(group: str) -> Dict[str, Any]:
    manifest = get_controller().clear(group)
    print("Cleared exact interaction manifest: %s" % manifest.root_group)
    return manifest.to_dict()


def interaction_owner_reference(group: str, cmd_api: Any = None) -> str:
    """Return a stable journal/undo reference for the group's current run."""

    return get_controller(cmd_api).owner_reference(group)


def preview_interaction(
    preset: str,
    source: str,
    partner1: str = "",
    partner2: str = "",
    state: int = 1,
    group: str = "",
    output_json: str = "",
    replace: Any = True,
    parameters: Optional[Mapping[str, Any]] = None,
    cmd_api: Any = None,
) -> Dict[str, Any]:
    """Public read-only validation API used by task-oriented facades."""

    controller = get_controller(cmd_api)
    canonical = PRESETS.resolve(preset).name
    source_value = str(source).strip()
    group_value = str(group).strip() or "ppi_%s_%s" % (
        _safe_component(source_value, 34),
        canonical,
    )
    return controller.preview(
        canonical,
        source_value,
        partner1=partner1,
        partner2=partner2,
        state=state,
        group=group_value,
        output_json=output_json,
        replace=replace,
        parameters=parameters,
    )


def pp_interact_export(group: str, filename: str) -> str:
    target = get_controller().export(group, filename)
    print("Exported InteractionResult: %s" % target)
    return target


def pp_interact_backend_status() -> Dict[str, Any]:
    preset_backends = {
        definition.name: definition.backend for definition in PRESETS.definitions()
    }
    status = {
        "schema": "pymolplugins.interaction_backends/1.0",
        "backend": "pymol-local",
        "version": LOCAL_BACKEND_VERSION,
        "available": _PYMOL_CMD is not None,
        "presets": list(PRESETS.names()),
        "preset_backends": preset_backends,
        "backends": {
            "pymol-local": {
                "available": _PYMOL_CMD is not None,
                "version": LOCAL_BACKEND_VERSION,
            },
            "plip-wsl": {
                "available": None,
                "status": "not_checked",
                "check": "pp_worker_health",
                "url": os.environ.get(
                    "PYMOLPLUGINS_WORKER_URL", "http://127.0.0.1:8765"
                ),
                "asynchronous_default": True,
                "local_fallbacks": ["ligand_contacts", "ligand_site"],
            },
        },
    }
    print(json.dumps(status, indent=2, sort_keys=True))
    return status


def _console_error_text(exc: Exception) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


def _interaction_console_guard(command_name: str, function: Any) -> Any:
    """Keep expected PyMOL console mistakes to one actionable error record."""

    @wraps(function)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            message = _console_error_text(exc)
            worker_code = str(getattr(exc, "code", ""))
            if worker_code:
                action = (
                    "Run pp_worker_health; meanwhile use `pp_interact_run "
                    "ligand_contacts, source=OBJECT` for local distance contacts."
                )
                expected = True
            elif isinstance(exc, StaleInteractionOwnerError):
                action = (
                    "This group was replaced by a newer run. Keep the current "
                    "result, or clear its current exact owner reference."
                )
                expected = True
            elif isinstance(exc, InteractionClearError):
                action = (
                    "Fix the reported Host deletion failure, then retry "
                    "pp_interact_clear with the same exact owner reference."
                )
                expected = True
            elif isinstance(exc, (KeyError, ValueError)):
                action = (
                    "Run pp_interact_list, then retry with an exact preset and "
                    "source object."
                )
                expected = True
            elif isinstance(exc, OSError):
                action = "Check output_json path and permissions, then retry."
                expected = True
            elif isinstance(exc, RuntimeError) and message.startswith(
                "Host PyMOL lacks"
            ):
                action = "Run pp_doctor and use a supported PyMOL 2.x/3.x Host."
                expected = True
            else:
                action = (
                    "Inspect the traceback and the staged/backup groups before retrying."
                    if "rollback errors:" in message
                    else "Inspect the traceback; the scene transaction was rolled back."
                )
                expected = False
            print("%s error: %s" % (command_name, message))
            print("Action: %s" % action)
            if not expected:
                traceback.print_exc()
            scene_preserved = bool(
                getattr(exc, "scene_preserved", "rollback errors:" not in message)
            )
            return {
                "schema": "pymolplugins.interaction_command/1.0",
                "status": "error",
                "command": command_name,
                "error": message,
                "action": action,
                "scene_preserved": scene_preserved,
            }

    return guarded


def register_commands(cmd_api: Any = None) -> None:
    selected = cmd_api if cmd_api is not None else _PYMOL_CMD
    if selected is None:
        return
    get_controller(selected)
    for name, function in (
        ("pp_interact_list", pp_interact_list),
        ("pp_interact_describe", pp_interact_describe),
        ("pp_interact_run", pp_interact_run),
        ("pp_interact_clear", pp_interact_clear),
        ("pp_interact_export", pp_interact_export),
    ):
        extend_command(selected, name, _interaction_console_guard(name, function))
    extend_command(selected, "pp_interact_backend_status", pp_interact_backend_status)


register_commands()
