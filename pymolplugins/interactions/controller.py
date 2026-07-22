"""Local interaction presets and their mutation-bounded PyMOL adapter.

PyMOL is optional at import time.  The computation/result modules can therefore
be unit tested with a fake command API on WSL, while ``register_commands`` binds
the same controller to Host PyMOL.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import sys
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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
_CMD_CONTROLLER_ATTR = "_pymolplugins_interaction_controller_v1"
_OUTPUT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
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
    try:
        raw = cmd_api.find_pairs(
            selection1,
            selection2,
            cutoff=cutoff,
            mode=mode,
            state1=state,
            state2=state,
        )
    except TypeError:
        raw = cmd_api.find_pairs(selection1, selection2, cutoff=cutoff, mode=mode)
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
        raise ValueError("source selection %r has no atoms in state %d" % (source, state))
    atom_map = {_atom_key(atom): atom for atom in atoms}
    return atoms, atom_map


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
        return (
            [("partner1_partner2", selection1, selection2)],
            [
                Partner("partner1", selection1, _count_atoms(cmd_api, selection1, state)),
                Partner("partner2", selection2, _count_atoms(cmd_api, selection2, state)),
            ],
        )
    chains = _get_chains(cmd_api, source, state)
    selections = {chain: _chain_selection(source, chain) for chain in chains}
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
        {"preset": "polar_qc", "partner1": partner1, "partner2": partner2}
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
        warnings.append("no local ligand interactions found")
    if _count_atoms(cmd_api, receptor, state) == 0:
        warnings.append("receptor selection has no atoms")
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
) -> OutputManifest:
    recorder = _OutputRecorder(cmd_api, _existing_names(cmd_api))
    try:
        partners_group = _render_partners(cmd_api, recorder, result, root_group)
        _render_interface(
            cmd_api, recorder, result, preset, root_group, partners_group
        )
        site_groups = _render_site_groups(cmd_api, recorder, result, root_group)
        helpers_group = _child_name(root_group, "helpers")
        recorder.group(helpers_group, "", "helpers_group")
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
    result: InteractionResult
    manifest: OutputManifest


@dataclass(frozen=True)
class RemoteInteractionSubmission:
    request: Mapping[str, Any]
    preset: str
    group: str
    output_json: str
    replace: bool
    ligand_identities: Tuple[Tuple[str, int], ...]


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
    for key in ("surface", "labels"):
        if key in effective:
            effective[key] = _as_bool(effective[key])
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


class InteractionController:
    """Compute, render, export, and exactly clear local interaction runs."""

    def __init__(self, cmd_api: Any) -> None:
        if cmd_api is None:
            raise RuntimeError("PyMOL cmd API is unavailable")
        self.cmd = cmd_api
        self._runs: Dict[str, InteractionRunRecord] = {}

    def list_presets(self) -> Tuple[str, ...]:
        return PRESETS.names()

    def describe(self, preset: str) -> Dict[str, Any]:
        return PRESETS.resolve(preset).to_dict()

    def _prepare_group(
        self, root_group: str, replace: Any
    ) -> Optional[InteractionRunRecord]:
        old_record = self._runs.get(root_group)
        if old_record is not None and not _as_bool(replace):
            raise ValueError("interaction group already exists: %s" % root_group)
        if old_record is not None:
            self.clear(root_group)
        elif root_group in _existing_names(self.cmd):
            raise ValueError(
                "refusing to replace unowned PyMOL name %s (no exact manifest)"
                % root_group
            )
        return old_record

    def _restore_replaced_run(
        self, root_group: str, old_record: InteractionRunRecord
    ) -> None:
        restored_manifest = _render_result(
            self.cmd,
            old_record.result,
            old_record.manifest.preset,
            root_group,
        )
        self._runs[root_group] = InteractionRunRecord(
            result=old_record.result,
            manifest=restored_manifest,
        )

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
        old_record = self._prepare_group(root_group, replace)
        try:
            manifest = _render_result(self.cmd, result, canonical, root_group)
            record = InteractionRunRecord(result=result, manifest=manifest)
            self._runs[root_group] = record
            if output_json:
                self.export(root_group, output_json)
        except Exception as exc:
            if root_group in self._runs:
                try:
                    self.clear(root_group)
                except Exception:
                    pass
            if old_record is not None:
                try:
                    self._restore_replaced_run(root_group, old_record)
                except Exception as restore_exc:
                    raise RuntimeError(
                        "interaction replacement failed and the prior result could not "
                        f"be restored: replacement={exc}; restore={restore_exc}"
                    ) from exc
            raise
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
        effective = _effective_parameters(definition, parameters)
        site_cutoff = _cutoff(effective["site_cutoff"], "site_cutoff")
        ligand, receptor = _ligand_partner_selections(
            source_value, str(partner1).strip(), str(partner2).strip()
        )
        ligand_atoms = list(getattr(_get_model(self.cmd, ligand, state_value), "atom", ()))
        if not ligand_atoms:
            raise ValueError("PLIP ligand selection has no atoms in source/state")
        partners = (
            Partner("receptor", receptor, _count_atoms(self.cmd, receptor, state_value)),
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
        request = build_plip_request_from_pymol(
            self.cmd,
            source_value,
            state=state_value,
            preset=definition.name,
            partners=tuple(value.to_dict() for value in partners),
            parameters=effective,
        )
        return RemoteInteractionSubmission(
            request=request,
            preset=definition.name,
            group=str(group),
            output_json=str(output_json),
            replace=_as_bool(replace),
            ligand_identities=tuple(sorted({_atom_key(atom) for atom in ligand_atoms})),
        )

    def commit_remote_result(
        self,
        submission: RemoteInteractionSubmission,
        payload: Mapping[str, Any],
    ) -> InteractionRunRecord:
        result = InteractionResult.from_dict(payload)
        if result.run.id != submission.request["run_id"]:
            raise ValueError("remote result run id does not match submitted request")
        if result.run.source.to_dict() != submission.request["source"]:
            raise ValueError("remote result source identity does not match submitted request")
        result = _filter_result_to_ligand(result, submission.ligand_identities)
        return self.commit_result(
            submission.preset,
            result,
            group=submission.group,
            output_json=submission.output_json,
            replace=submission.replace,
        )

    def get(self, group: str) -> InteractionRunRecord:
        try:
            return self._runs[str(group).strip()]
        except KeyError:
            raise KeyError("unknown interaction group %r" % group)

    def clear(self, group: str) -> OutputManifest:
        group_value = str(group).strip()
        try:
            record = self._runs[group_value]
        except KeyError:
            raise KeyError(
                "no exact interaction manifest for %r; nothing was deleted" % group
            )
        errors = []
        for name in record.manifest.delete_order:
            try:
                # Deliberately exact: never pass a wildcard or a source selection.
                self.cmd.delete(name)
            except Exception as exc:
                errors.append("%s: %s" % (name, exc))
        if errors:
            raise RuntimeError("failed to clear all manifest names: %s" % "; ".join(errors))
        del self._runs[group_value]
        return record.manifest

    def export(self, group: str, filename: str) -> str:
        record = self.get(group)
        target = os.path.abspath(os.path.expanduser(str(filename).strip()))
        if not filename or os.path.isdir(target):
            raise ValueError("filename must identify a JSON file")
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
    if attached is not None and getattr(attached, "cmd", None) is selected:
        _CONTROLLER = attached
    elif _CONTROLLER is None or _CONTROLLER.cmd is not selected:
        _CONTROLLER = InteractionController(selected)
        try:
            setattr(selected, _CMD_CONTROLLER_ATTR, _CONTROLLER)
        except (AttributeError, TypeError):
            # Some proxy cmd implementations may disallow custom attributes.
            pass
    return _CONTROLLER


def pp_interact_list() -> Tuple[str, ...]:
    names = get_controller().list_presets()
    for name in names:
        print(name)
    return names


def pp_interact_describe(preset: str) -> Dict[str, Any]:
    description = get_controller().describe(preset)
    print(json.dumps(description, indent=2, sort_keys=True))
    return description


def pp_interact_run(
    preset: str,
    source: str,
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
) -> Any:
    parameters: Dict[str, Any] = {}
    canonical = PRESETS.resolve(preset).name
    if canonical in {"ligand_site", "plip_publication"}:
        parameters.update({"surface": _as_bool(surface), "labels": _as_bool(labels)})
    supplied_cutoffs = {
        "good_cutoff": good_cutoff,
        "weak_cutoff": weak_cutoff,
        "clash_cutoff": clash_cutoff,
        "site_cutoff": site_cutoff,
    }
    for name, value in supplied_cutoffs.items():
        if value != "":
            parameters[name] = value
    controller = get_controller()
    definition = PRESETS.resolve(canonical)
    if definition.backend == "plip-wsl":
        submission = controller.prepare_remote(
            canonical,
            source,
            partner1=partner1,
            partner2=partner2,
            state=_state_number(state),
            group=group,
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
            source,
            partner1=partner1,
            partner2=partner2,
            state=_state_number(state),
            group=group,
            output_json=output_json,
            replace=replace,
            parameters=parameters,
        )
    print(
        "Interaction preset ready: group=%s counts=%s"
        % (record.manifest.root_group, dict(record.result.summary["counts_by_type"]))
    )
    return record.manifest.root_group


def pp_interact_clear(group: str) -> Dict[str, Any]:
    manifest = get_controller().clear(group)
    print("Cleared exact interaction manifest: %s" % manifest.root_group)
    return manifest.to_dict()


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
                "available": "probe with pp_worker_health",
                "url": os.environ.get(
                    "PYMOLPLUGINS_WORKER_URL", "http://127.0.0.1:8765"
                ),
                "asynchronous_default": True,
            },
        },
    }
    print(json.dumps(status, indent=2, sort_keys=True))
    return status


def register_commands(cmd_api: Any = None) -> None:
    selected = cmd_api if cmd_api is not None else _PYMOL_CMD
    if selected is None:
        return
    get_controller(selected)
    selected.extend("pp_interact_list", pp_interact_list)
    selected.extend("pp_interact_describe", pp_interact_describe)
    selected.extend("pp_interact_run", pp_interact_run)
    selected.extend("pp_interact_clear", pp_interact_clear)
    selected.extend("pp_interact_export", pp_interact_export)
    selected.extend("pp_interact_backend_status", pp_interact_backend_status)


register_commands()
