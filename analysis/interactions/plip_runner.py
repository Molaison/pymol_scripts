import argparse
import json
import sys
import traceback


def _as_int(value):
    try:
        return int(value)
    except Exception:
        return value


def _as_float_list(values):
    return [float(v) for v in values]


def _site_payload(vis, bsid: str):
    return {
        "bsid": bsid,
        "hydrophobic_contacts": {
            "pairs_ids": [[_as_int(a), _as_int(b)] for a, b in vis.hydrophobic_contacts.pairs_ids],
        },
        "hbonds": {
            "ldon_id": [[_as_int(a), _as_int(b)] for a, b in vis.hbonds.ldon_id],
            "pdon_id": [[_as_int(a), _as_int(b)] for a, b in vis.hbonds.pdon_id],
        },
        "halogen_bonds": [
            {"don_id": _as_int(h.don_id), "acc_id": _as_int(h.acc_id)} for h in vis.halogen_bonds
        ],
        "waterbridges": [
            {
                "don_id": _as_int(w.don_id),
                "acc_id": _as_int(w.acc_id),
                "water_id": _as_int(w.water_id),
                "protisdon": bool(w.protisdon),
            }
            for w in vis.waterbridges
        ],
        "metal_complexes": [
            {
                "metal_id": _as_int(m.metal_id),
                "target_id": _as_int(m.target_id),
                "location": str(m.location),
            }
            for m in vis.metal_complexes
        ],
        "pistacking": [
            {
                "proteinring_atoms": [_as_int(v) for v in p.proteinring_atoms],
                "proteinring_center": _as_float_list(p.proteinring_center),
                "ligandring_atoms": [_as_int(v) for v in p.ligandring_atoms],
                "ligandring_center": _as_float_list(p.ligandring_center),
                "type": str(p.type),
            }
            for p in vis.pistacking
        ],
        "pication": [
            {
                "ring_center": _as_float_list(p.ring_center),
                "charge_center": _as_float_list(p.charge_center),
                "ring_atoms": [_as_int(v) for v in p.ring_atoms],
                "charge_atoms": [_as_int(v) for v in p.charge_atoms],
                "protcharged": bool(p.protcharged),
            }
            for p in vis.pication
        ],
        "saltbridges": [
            {
                "positive_atoms": [_as_int(v) for v in s.positive_atoms],
                "negative_atoms": [_as_int(v) for v in s.negative_atoms],
                "positive_center": _as_float_list(s.positive_center),
                "negative_center": _as_float_list(s.negative_center),
                "protispos": bool(s.protispos),
            }
            for s in vis.saltbridges
        ],
    }


def main(argv) -> int:
    parser = argparse.ArgumentParser(description="Run PLIP and emit interaction JSON for PyMOL.")
    parser.add_argument("--pdb", required=True, help="Input PDB path")
    args = parser.parse_args(argv)

    try:
        from plip.structure.preparation import PDBComplex
        from plip.basic.remote import VisualizerData

        mol = PDBComplex()
        mol.load_pdb(args.pdb)
        mol.analyze()

        payload = {
            "version": 1,
            "sites": [],
        }

        for bsid in sorted(getattr(mol, "interaction_sets", {}).keys()):
            vis = VisualizerData(mol, bsid)
            payload["sites"].append(_site_payload(vis, bsid))

        json.dump(payload, sys.stdout)
        return 0

    except Exception as e:
        payload = {
            "version": 1,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }
        json.dump(payload, sys.stdout)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
