"""Run PLIP and emit a versioned, geometry-preserving raw payload."""

import argparse
import hashlib
import json
import math
import sys
import traceback

try:
    from pymolplugins.interactions.plip_protocol import (
        PLIP_RAW_SCHEMA,
        PLIP_RAW_VERSION,
        validate_plip_raw,
    )
except ImportError:
    from plip_protocol import (  # type: ignore
        PLIP_RAW_SCHEMA,
        PLIP_RAW_VERSION,
        validate_plip_raw,
    )


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def get_plip_version(plip_module):
    """Return the upstream PLIP version across supported package layouts."""

    value = getattr(plip_module, "__version__", None)
    if value:
        return str(value)
    try:
        from plip.basic import config
    except (ImportError, AttributeError):
        return "unknown"
    return str(getattr(config, "__version__", None) or "unknown")


def _finite_or_none(value):
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _as_float_list(values):
    result = []
    for value in values or ():
        converted = _finite_or_none(value)
        if converted is not None:
            result.append(converted)
    return result


def _plain(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    try:
        converted = value.item()
    except (AttributeError, ValueError):
        return str(value)
    return _plain(converted)


def _properties(**values):
    return {key: _plain(value) for key, value in values.items()}


def _participants(role, serials):
    return [
        {"role": role, "export_serial": _as_int(serial)}
        for serial in serials
    ]


def _row(
    interaction_type,
    subtype,
    participants,
    distance=None,
    angles=(),
    properties=None,
):
    return {
        "type": interaction_type,
        "subtype": str(subtype or ""),
        "participants": list(participants),
        "geometry": {
            "distance_angstrom": _finite_or_none(distance),
            "angles_degree": _as_float_list(angles),
        },
        "properties": dict(properties or {}),
    }


def _center_distance(left, right):
    left_values = _as_float_list(left)
    right_values = _as_float_list(right)
    if len(left_values) != 3 or len(right_values) != 3:
        return None
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left_values, right_values)))


def _legacy_pair_payload(vis):
    return {
        "hydrophobic_contacts": {
            "pairs_ids": [
                [_as_int(a), _as_int(b)]
                for a, b in vis.hydrophobic_contacts.pairs_ids
            ],
        },
        "hbonds": {
            "ldon_id": [
                [_as_int(a), _as_int(b)] for a, b in vis.hbonds.ldon_id
            ],
            "pdon_id": [
                [_as_int(a), _as_int(b)] for a, b in vis.hbonds.pdon_id
            ],
        },
    }


def _legacy_atom_payload(vis):
    return {
        "halogen_bonds": [
            {"don_id": _as_int(item.don_id), "acc_id": _as_int(item.acc_id)}
            for item in vis.halogen_bonds
        ],
        "waterbridges": [
            {
                "don_id": _as_int(item.don_id),
                "acc_id": _as_int(item.acc_id),
                "water_id": _as_int(item.water_id),
                "protisdon": bool(item.protisdon),
            }
            for item in vis.waterbridges
        ],
        "metal_complexes": [
            {
                "metal_id": _as_int(item.metal_id),
                "target_id": _as_int(item.target_id),
                "location": str(item.location),
            }
            for item in vis.metal_complexes
        ],
    }


def _legacy_center_payload(vis):
    return {
        "pistacking": [
            {
                "proteinring_atoms": [_as_int(value) for value in item.proteinring_atoms],
                "proteinring_center": _as_float_list(item.proteinring_center),
                "ligandring_atoms": [_as_int(value) for value in item.ligandring_atoms],
                "ligandring_center": _as_float_list(item.ligandring_center),
                "type": str(item.type),
            }
            for item in vis.pistacking
        ],
        "pication": [
            {
                "ring_center": _as_float_list(item.ring_center),
                "charge_center": _as_float_list(item.charge_center),
                "ring_atoms": [_as_int(value) for value in item.ring_atoms],
                "charge_atoms": [_as_int(value) for value in item.charge_atoms],
                "protcharged": bool(item.protcharged),
            }
            for item in vis.pication
        ],
        "saltbridges": [
            {
                "positive_atoms": [_as_int(value) for value in item.positive_atoms],
                "negative_atoms": [_as_int(value) for value in item.negative_atoms],
                "positive_center": _as_float_list(item.positive_center),
                "negative_center": _as_float_list(item.negative_center),
                "protispos": bool(item.protispos),
            }
            for item in vis.saltbridges
        ],
    }


def _legacy_site_payload(vis, bsid):
    """Keep the existing standalone visualizer reader working during migration."""

    payload = {"bsid": str(bsid)}
    payload.update(_legacy_pair_payload(vis))
    payload.update(_legacy_atom_payload(vis))
    payload.update(_legacy_center_payload(vis))
    return payload


def _hydrophobic_rows(interaction_set):
    rows = []
    for item in getattr(interaction_set, "hydrophobic_contacts", ()):
        rows.append(
            _row(
                "hydrophobic",
                "contact",
                _participants("protein", (item.bsatom_orig_idx,))
                + _participants("ligand", (item.ligatom_orig_idx,)),
                item.distance,
                properties=_properties(
                    protein_residue=getattr(item, "restype", ""),
                    ligand_residue=getattr(item, "restype_l", ""),
                ),
            )
        )
    return rows


def _hbond_rows(interactions, subtype):
    rows = []
    for item in interactions:
        rows.append(
            _row(
                "hydrogen_bond",
                subtype,
                _participants("acceptor", (item.a_orig_idx,))
                + _participants("donor", (item.d_orig_idx,)),
                getattr(item, "distance_ad", None),
                (getattr(item, "angle", None),),
                _properties(
                    distance_ah=getattr(item, "distance_ah", None),
                    protein_is_donor=getattr(item, "protisdon", None),
                    bond_type=getattr(item, "type", ""),
                    acceptor_type=getattr(item, "atype", ""),
                    donor_type=getattr(item, "dtype", ""),
                    sidechain=getattr(item, "sidechain", None),
                ),
            )
        )
    return rows


def _halogen_rows(interaction_set):
    rows = []
    for item in getattr(interaction_set, "halogen_bonds", ()):
        rows.append(
            _row(
                "halogen_bond",
                "donor_acceptor",
                _participants("donor", (item.don_orig_idx,))
                + _participants("acceptor", (item.acc_orig_idx,)),
                item.distance,
                (item.don_angle, item.acc_angle),
                _properties(
                    donor_type=getattr(item, "donortype", ""),
                    acceptor_type=getattr(item, "acctype", ""),
                    sidechain=getattr(item, "sidechain", None),
                ),
            )
        )
    return rows


def _water_rows(interaction_set):
    rows = []
    for item in getattr(interaction_set, "water_bridges", ()):
        subtype = "protein_donor" if item.protisdon else "ligand_donor"
        rows.append(
            _row(
                "water_bridge",
                subtype,
                _participants("donor", (item.d_orig_idx,))
                + _participants("acceptor", (item.a_orig_idx,))
                + _participants("water", (item.water_orig_idx,)),
                item.distance_dw,
                (item.d_angle, item.w_angle),
                _properties(
                    acceptor_water_distance=item.distance_aw,
                    donor_water_distance=item.distance_dw,
                    bridge_type=getattr(item, "type", ""),
                    protein_is_donor=item.protisdon,
                ),
            )
        )
    return rows


def _metal_rows(interaction_set):
    rows = []
    for item in getattr(interaction_set, "metal_complexes", ()):
        rows.append(
            _row(
                "metal_complex",
                getattr(item, "location", ""),
                _participants("metal", (item.metal_orig_idx,))
                + _participants("target", (item.target_orig_idx,)),
                item.distance,
                properties=_properties(
                    metal_type=getattr(item, "metal_type", ""),
                    target_type=getattr(item, "target_type", ""),
                    coordination_num=getattr(item, "coordination_num", None),
                    geometry=getattr(item, "geometry", ""),
                    rms=getattr(item, "rms", None),
                    complex_number=getattr(item, "complexnum", None),
                ),
            )
        )
    return rows


def _pistacking_rows(interaction_set):
    rows = []
    for item in getattr(interaction_set, "pistacking", ()):
        rows.append(
            _row(
                "pi_stacking",
                getattr(item, "type", ""),
                _participants("protein_ring", item.proteinring.atoms_orig_idx)
                + _participants("ligand_ring", item.ligandring.atoms_orig_idx),
                item.distance,
                (item.angle,),
                _properties(offset=item.offset, stack_type=item.type),
            )
        )
    return rows


def _pication_rows(interaction_set):
    interactions = list(getattr(interaction_set, "pication_paro", ()))
    interactions.extend(getattr(interaction_set, "pication_laro", ()))
    rows = []
    for item in interactions:
        subtype = "protein_charged" if item.protcharged else "ligand_charged"
        rows.append(
            _row(
                "pi_cation",
                subtype,
                _participants("ring", item.ring.atoms_orig_idx)
                + _participants("charge", item.charge.atoms_orig_idx),
                item.distance,
                properties=_properties(
                    offset=item.offset,
                    interaction_type=getattr(item, "type", ""),
                    protein_charged=item.protcharged,
                ),
            )
        )
    return rows


def _saltbridge_rows(interaction_set):
    interactions = list(getattr(interaction_set, "saltbridge_lneg", ()))
    interactions.extend(getattr(interaction_set, "saltbridge_pneg", ()))
    rows = []
    for item in interactions:
        subtype = "protein_positive" if item.protispos else "ligand_positive"
        rows.append(
            _row(
                "salt_bridge",
                subtype,
                _participants("positive", item.positive.atoms_orig_idx)
                + _participants("negative", item.negative.atoms_orig_idx),
                item.distance,
                properties=_properties(protein_positive=item.protispos),
            )
        )
    return rows


def _interaction_set_rows(interaction_set):
    rows = _hydrophobic_rows(interaction_set)
    rows.extend(_hbond_rows(getattr(interaction_set, "hbonds_ldon", ()), "ligand_donor"))
    rows.extend(_hbond_rows(getattr(interaction_set, "hbonds_pdon", ()), "protein_donor"))
    rows.extend(_halogen_rows(interaction_set))
    rows.extend(_water_rows(interaction_set))
    rows.extend(_metal_rows(interaction_set))
    rows.extend(_pistacking_rows(interaction_set))
    rows.extend(_pication_rows(interaction_set))
    rows.extend(_saltbridge_rows(interaction_set))
    return rows


def _fallback_rows(vis):
    """Provide identity-complete rows when only VisualizerData is available."""

    rows = []
    for protein_id, ligand_id in vis.hydrophobic_contacts.pairs_ids:
        rows.append(
            _row(
                "hydrophobic",
                "contact",
                _participants("protein", (protein_id,))
                + _participants("ligand", (ligand_id,)),
            )
        )
    hbond_groups = (
        ("ligand_donor", vis.hbonds.ldon_id),
        ("protein_donor", vis.hbonds.pdon_id),
    )
    for subtype, values in hbond_groups:
        for acceptor_id, donor_id in values:
            rows.append(
                _row(
                    "hydrogen_bond",
                    subtype,
                    _participants("acceptor", (acceptor_id,))
                    + _participants("donor", (donor_id,)),
                )
            )
    rows.extend(_fallback_nonpolar_rows(vis))
    return rows


def _fallback_nonpolar_rows(vis):
    rows = []
    for item in vis.halogen_bonds:
        rows.append(
            _row(
                "halogen_bond",
                "donor_acceptor",
                _participants("donor", (item.don_id,))
                + _participants("acceptor", (item.acc_id,)),
            )
        )
    for item in vis.waterbridges:
        rows.append(
            _row(
                "water_bridge",
                "protein_donor" if item.protisdon else "ligand_donor",
                _participants("donor", (item.don_id,))
                + _participants("acceptor", (item.acc_id,))
                + _participants("water", (item.water_id,)),
            )
        )
    for item in vis.metal_complexes:
        rows.append(
            _row(
                "metal_complex",
                item.location,
                _participants("metal", (item.metal_id,))
                + _participants("target", (item.target_id,)),
            )
        )
    rows.extend(_fallback_center_rows(vis))
    return rows


def _fallback_center_rows(vis):
    rows = []
    for item in vis.pistacking:
        rows.append(
            _row(
                "pi_stacking",
                item.type,
                _participants("protein_ring", item.proteinring_atoms)
                + _participants("ligand_ring", item.ligandring_atoms),
                _center_distance(item.proteinring_center, item.ligandring_center),
            )
        )
    for item in vis.pication:
        rows.append(
            _row(
                "pi_cation",
                "protein_charged" if item.protcharged else "ligand_charged",
                _participants("ring", item.ring_atoms)
                + _participants("charge", item.charge_atoms),
                _center_distance(item.ring_center, item.charge_center),
            )
        )
    for item in vis.saltbridges:
        rows.append(
            _row(
                "salt_bridge",
                "protein_positive" if item.protispos else "ligand_positive",
                _participants("positive", item.positive_atoms)
                + _participants("negative", item.negative_atoms),
                _center_distance(item.positive_center, item.negative_center),
            )
        )
    return rows


def _site_payload(vis, bsid, interaction_set=None):
    legacy = _legacy_site_payload(vis, bsid)
    legacy["site_id"] = str(bsid)
    legacy["interactions"] = (
        _interaction_set_rows(interaction_set)
        if interaction_set is not None
        else _fallback_rows(vis)
    )
    return legacy


def build_raw_payload(mol, visualizer_class, pdb_text, backend_version):
    sites = []
    interaction_sets = getattr(mol, "interaction_sets", {})
    for bsid in sorted(interaction_sets):
        sites.append(
            _site_payload(
                visualizer_class(mol, bsid),
                bsid,
                interaction_set=interaction_sets[bsid],
            )
        )
    payload = {
        "schema": PLIP_RAW_SCHEMA,
        "version": PLIP_RAW_VERSION,
        "backend": {"name": "plip", "version": str(backend_version or "unknown")},
        "input_sha256": hashlib.sha256(pdb_text.encode("utf-8")).hexdigest(),
        "sites": sites,
        "warnings": [],
    }
    return validate_plip_raw(payload)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdb", required=True, help="Input PDB path")
    args = parser.parse_args(argv)

    try:
        import plip
        from plip.basic.remote import VisualizerData
        from plip.structure.preparation import PDBComplex

        with open(args.pdb, "r", encoding="utf-8") as handle:
            pdb_text = handle.read()
        mol = PDBComplex()
        mol.load_pdb(args.pdb)
        mol.analyze()
        payload = build_raw_payload(
            mol,
            VisualizerData,
            pdb_text,
            get_plip_version(plip),
        )
        json.dump(payload, sys.stdout, allow_nan=False)
        return 0
    except Exception as exc:
        payload = {
            "schema": PLIP_RAW_SCHEMA,
            "version": PLIP_RAW_VERSION,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        json.dump(payload, sys.stdout, allow_nan=False)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
