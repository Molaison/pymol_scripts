from pymol import cmd
from pymol.Qt import QtWidgets
import hashlib
import json
import os
import re
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Iterable, List, Optional, Sequence, Set, Tuple
import traceback
import glob


DEFAULT_LIGAND_SELECTION = "organic and not solvent"
DEFAULT_PROTEIN_SELECTION = "polymer.protein"
PLIP_PYTHON_ENVVAR = "PYMOL_PLIP_PYTHON"
MAX_GROUP_PREFIX_LEN = 60


def _sanitize_name(text: str, fallback: str) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z_]+", "_", (text or "").strip()).strip("_")
    if not sanitized:
        sanitized = fallback
    if sanitized.startswith("_"):
        sanitized = f"{fallback}_{sanitized.lstrip('_')}"
    if sanitized[0].isdigit():
        sanitized = f"{fallback}_{sanitized}"
    return sanitized


def _shorten_with_hash(text: str, max_len: int, hash_len: int = 10) -> str:
    if len(text) <= max_len:
        return text
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:hash_len]
    keep = max_len - (hash_len + 1)
    if keep <= 0:
        return digest[:max_len]
    head = text[:keep].rstrip("_")
    if not head:
        return digest[:max_len]
    return f"{head}_{digest}"


def _normalize_group_prefix(text: str, fallback: str = "plip") -> str:
    return _shorten_with_hash(_sanitize_name(text, fallback), MAX_GROUP_PREFIX_LEN)


def _chunks(values: Sequence[int], size: int) -> Iterable[Sequence[int]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _renumber_pdb_serials(pdb_str: str) -> str:
    lines = pdb_str.splitlines(True)
    serial_map = {}
    new_serial = 1

    for line in lines:
        rec = line[:6]
        if rec in ("ATOM  ", "HETATM"):
            try:
                old_serial = int(line[6:11])
            except Exception:
                old_serial = None
            if old_serial is not None and old_serial not in serial_map:
                serial_map[old_serial] = new_serial
            new_serial += 1
            continue
        if rec == "TER   ":
            new_serial += 1

    new_serial = 1
    out_lines = []
    for line in lines:
        rec = line[:6]
        if rec in ("ATOM  ", "HETATM", "TER   "):
            if new_serial > 99999:
                raise ValueError("Too many records for PDB serial numbering (>99999)")
            out_lines.append(f"{line[:6]}{new_serial:>5}{line[11:]}")
            new_serial += 1
            continue
        if rec == "CONECT":
            tokens = line[6:].split()
            if not tokens:
                out_lines.append(line)
                continue
            mapped = []
            for token in tokens:
                try:
                    old_id = int(token)
                except Exception:
                    continue
                mapped_id = serial_map.get(old_id, old_id)
                mapped.append(f"{mapped_id:>5}")
            out_lines.append("CONECT" + "".join(mapped) + ("\n" if line.endswith("\n") else ""))
            continue
        out_lines.append(line)

    return "".join(out_lines)


def _collect_residue_sites(selection: str) -> Set[Tuple[str, str, int]]:
    sites: Set[Tuple[str, str, int]] = set()
    cmd.iterate(
        selection, "sites.add((resn, chain, int(resv)))", space={"sites": sites}
    )
    return sites


def _select_by_ids(
    selname: str,
    obj: str,
    ids: Sequence[int],
    chunk_size: int = 200,
) -> int:
    ids_sorted = sorted({int(v) for v in ids})
    try:
        cmd.select(selname, "none")
    except Exception:
        return 0
    if not ids_sorted:
        return 0
    for idx, chunk in enumerate(_chunks(ids_sorted, chunk_size)):
        id_expr = "+".join(str(v) for v in chunk)
        expr = f"({obj}) and id {id_expr}"
        try:
            if idx == 0:
                cmd.select(selname, expr)
            else:
                cmd.select(selname, f"({selname}) or ({expr})")
        except Exception:
            return cmd.count_atoms(selname)
    return cmd.count_atoms(selname)


def plip_interface_clear(prefix: str = "plip") -> None:
    """
    Delete objects/selections created by this plugin.

    Args:
        prefix: Prefix used in `plip_interface(group=...)`.
    """
    prefix = _normalize_group_prefix(prefix, "plip")
    try:
        try:
            from pymolplugins.interactions.controller import get_controller
        except ImportError:
            from pymol_interactions import get_controller  # type: ignore
        get_controller(cmd).clear(prefix)
        print(f"Cleared exact canonical PLIP manifest: {prefix}")
    except KeyError:
        print(
            f"No exact canonical PLIP manifest for {prefix!r}; "
            "nothing was deleted"
        )
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()


def _safe_import_plip():
    try:
        from plip.structure.preparation import PDBComplex
        from plip.basic.remote import VisualizerData
    except Exception as e:
        raise ImportError(
            "PLIP is not available in the Python used by PyMOL.\n"
            "If you are on Windows, installing PLIP via pip often fails because OpenBabel "
            "needs native binaries.\n"
            "Workarounds:\n"
            f"  1) Use an external Python (conda/venv) with PLIP+OpenBabel and pass "
            f"`plip_python=...`, or set env var {PLIP_PYTHON_ENVVAR}.\n"
            "  2) (Advanced) Install OpenBabel build dependencies and compile wheels."
        ) from e
    return PDBComplex, VisualizerData


def _resolve_plip_python(plip_python: str) -> str:
    plip_python = (plip_python or "").strip()
    if plip_python:
        return plip_python
    return (os.environ.get(PLIP_PYTHON_ENVVAR) or "").strip()


def _runner_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "plip_runner.py")


def _dir_looks_like_openbabel_libdir(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    patterns = [
        "*.obf",
        "openbabel-*.dll",
        "libopenbabel*.so*",
        "libopenbabel*.dylib*",
        "*.dll",
        "*.so",
        "*.dylib",
    ]
    for pattern in patterns:
        if glob.glob(os.path.join(path, pattern)):
            return True
    return False


def _version_key(text: str) -> Tuple[int, ...]:
    parts = re.split(r"[^0-9]+", text or "")
    numbers = [int(p) for p in parts if p.isdigit()]
    return tuple(numbers or [0])


def _guess_openbabel_libdir(prefix: str) -> str:
    candidates = [
        os.path.join(prefix, "Library", "bin"),  # Windows conda
        os.path.join(prefix, "Library", "lib", "openbabel"),
        os.path.join(prefix, "lib", "openbabel"),
    ]

    for base in candidates:
        if _dir_looks_like_openbabel_libdir(base):
            return base

        if not os.path.isdir(base):
            continue

        subdirs = [
            os.path.join(base, name)
            for name in os.listdir(base)
            if os.path.isdir(os.path.join(base, name))
        ]
        subdirs.sort(key=lambda p: _version_key(os.path.basename(p)), reverse=True)
        for subdir in subdirs:
            if _dir_looks_like_openbabel_libdir(subdir):
                return subdir

    return ""


def _guess_openbabel_datadir(prefix: str) -> str:
    candidates = [
        os.path.join(prefix, "Library", "share", "openbabel"),
        os.path.join(prefix, "share", "openbabel"),
    ]

    for base in candidates:
        if not os.path.isdir(base):
            continue
        # If versioned subdirs exist, pick the newest.
        subdirs = [
            os.path.join(base, name)
            for name in os.listdir(base)
            if os.path.isdir(os.path.join(base, name))
        ]
        if subdirs:
            subdirs.sort(key=lambda p: _version_key(os.path.basename(p)), reverse=True)
            return subdirs[0]
        return base

    return ""


def _prepend_env_path(env: dict, key: str, value: str) -> None:
    if not value:
        return
    current = env.get(key, "")
    parts = [p for p in current.split(os.pathsep) if p]
    if value in parts:
        return
    env[key] = value + (os.pathsep + current if current else "")


def _augment_env_for_openbabel(env: dict, python_exe: str) -> dict:
    prefix = os.path.dirname(os.path.abspath(python_exe))

    if os.name == "nt":
        bin_dir = os.path.join(prefix, "Library", "bin")
        if os.path.isdir(bin_dir):
            _prepend_env_path(env, "PATH", bin_dir)
    else:
        bin_dir = os.path.join(prefix, "bin")
        if os.path.isdir(bin_dir):
            _prepend_env_path(env, "PATH", bin_dir)

    if not env.get("BABEL_LIBDIR"):
        babel_libdir = _guess_openbabel_libdir(prefix)
        if babel_libdir:
            env["BABEL_LIBDIR"] = babel_libdir

    if not env.get("BABEL_DATADIR"):
        babel_datadir = _guess_openbabel_datadir(prefix)
        if babel_datadir:
            env["BABEL_DATADIR"] = babel_datadir

    return env


def _embedded_runner_source() -> str:
    return r"""import argparse
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


def _site_payload(vis, bsid):
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


def main(argv):
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
"""


def _run_plip_external(pdb_str: str, python_exe: str) -> dict:
    runner = _runner_path()
    runner_available = os.path.exists(runner)

    python_exe = (python_exe or "").strip()
    if not python_exe:
        raise ValueError("External PLIP python is empty")

    with tempfile.TemporaryDirectory(prefix="pymol_plip_") as tmpdir:
        pdb_path = os.path.join(tmpdir, "plip_input.pdb")
        with open(pdb_path, "wt", encoding="utf-8", newline="") as fh:
            fh.write(pdb_str)

        if not runner_available:
            runner = os.path.join(tmpdir, "plip_runner_embedded.py")
            with open(runner, "wt", encoding="utf-8", newline="") as fh:
                fh.write(_embedded_runner_source())

        env = _augment_env_for_openbabel(os.environ.copy(), python_exe)
        # On Windows, OpenBabel's Python bindings may only discover formats/plugins if the
        # process starts in the directory that contains the OpenBabel plugin binaries.
        # (Observed: `pdb` missing from `pybel.informats` unless cwd=...\\Library\\bin.)
        babel_cwd = env.get("BABEL_LIBDIR") if os.name == "nt" else None
        if not babel_cwd or not os.path.isdir(babel_cwd):
            babel_cwd = None
        proc = subprocess.run(
            [python_exe, runner, "--pdb", pdb_path],
            capture_output=True,
            text=True,
            env=env,
            cwd=babel_cwd,
        )

        stdout = (proc.stdout or "").strip()
        if not stdout:
            raise RuntimeError(
                "PLIP runner returned empty output.\n"
                f"cmd: {python_exe} {runner}\n"
                f"stderr:\n{proc.stderr}"
            )

        try:
            payload = json.loads(stdout)
        except Exception as e:
            raise RuntimeError(
                "Failed to parse PLIP runner JSON output.\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{proc.stderr}"
            ) from e

        if proc.returncode != 0:
            err = payload.get("error") or f"Runner exit code {proc.returncode}"
            tb = payload.get("traceback") or proc.stderr
            if "not a recognised Open Babel format" in tb and "pdb" in tb.lower():
                check = subprocess.run(
                    [
                        python_exe,
                        "-c",
                        "from openbabel import pybel; "
                        "import json; "
                        "print(json.dumps({'pdb': 'pdb' in pybel.informats, 'formats': len(pybel.informats)}))",
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=babel_cwd,
                )
                hint = ""
                if check.returncode == 0 and check.stdout.strip():
                    hint = f"\nOpenBabel formats check: {check.stdout.strip()}"
                raise RuntimeError(
                    "PLIP runner failed because OpenBabel in the external Python does not support PDB.\n"
                    "PLIP currently requires PDB input.\n"
                    "Fix: create/use a conda env where `python -c \"from openbabel import pybel; print('pdb' in pybel.informats)\"` prints True.\n"
                    f"External Python: {python_exe}{hint}\n\nOriginal error: {err}\n{tb}"
                )
            raise RuntimeError(f"PLIP runner failed: {err}\n{tb}")

        return payload


def _site_dict_to_vis(site: dict):
    hbonds = SimpleNamespace(
        ldon_id=[tuple(v) for v in site.get("hbonds", {}).get("ldon_id", [])],
        pdon_id=[tuple(v) for v in site.get("hbonds", {}).get("pdon_id", [])],
    )
    hydrophobic_contacts = SimpleNamespace(
        pairs_ids=[tuple(v) for v in site.get("hydrophobic_contacts", {}).get("pairs_ids", [])]
    )
    halogen_bonds = [
        SimpleNamespace(don_id=h.get("don_id"), acc_id=h.get("acc_id"))
        for h in site.get("halogen_bonds", [])
    ]
    waterbridges = [
        SimpleNamespace(
            don_id=w.get("don_id"),
            acc_id=w.get("acc_id"),
            water_id=w.get("water_id"),
            protisdon=bool(w.get("protisdon")),
        )
        for w in site.get("waterbridges", [])
    ]
    metal_complexes = [
        SimpleNamespace(
            metal_id=m.get("metal_id"),
            target_id=m.get("target_id"),
            location=m.get("location"),
        )
        for m in site.get("metal_complexes", [])
    ]
    pistacking = [
        SimpleNamespace(
            proteinring_atoms=p.get("proteinring_atoms", []),
            proteinring_center=p.get("proteinring_center", []),
            ligandring_atoms=p.get("ligandring_atoms", []),
            ligandring_center=p.get("ligandring_center", []),
            type=p.get("type", ""),
        )
        for p in site.get("pistacking", [])
    ]
    pication = [
        SimpleNamespace(
            ring_center=p.get("ring_center", []),
            charge_center=p.get("charge_center", []),
            ring_atoms=p.get("ring_atoms", []),
            charge_atoms=p.get("charge_atoms", []),
            protcharged=bool(p.get("protcharged")),
        )
        for p in site.get("pication", [])
    ]
    saltbridges = [
        SimpleNamespace(
            positive_atoms=s.get("positive_atoms", []),
            negative_atoms=s.get("negative_atoms", []),
            positive_center=s.get("positive_center", []),
            negative_center=s.get("negative_center", []),
            protispos=bool(s.get("protispos")),
        )
        for s in site.get("saltbridges", [])
    ]
    return SimpleNamespace(
        hydrophobic_contacts=hydrophobic_contacts,
        hbonds=hbonds,
        halogen_bonds=halogen_bonds,
        waterbridges=waterbridges,
        metal_complexes=metal_complexes,
        pistacking=pistacking,
        pication=pication,
        saltbridges=saltbridges,
    )


def _parse_bsid(bsid: str) -> Optional[Tuple[str, str, int]]:
    parts = (bsid or "").split(":")
    if len(parts) < 3:
        return None
    hetid = parts[0].strip()
    chain = parts[1].strip()
    try:
        pos = int(parts[2])
    except Exception:
        return None
    return hetid, chain, pos


def _distance_between_ids(
    dist_name: str,
    obj: str,
    id1: int,
    id2: int,
    tmp1: str,
    tmp2: str,
    max_dist: float = 8.0,
) -> None:
    try:
        n1 = cmd.select(tmp1, f"({obj}) and id {int(id1)}")
        n2 = cmd.select(tmp2, f"({obj}) and id {int(id2)}")
    except Exception:
        return
    if n1 != 1 or n2 != 1:
        return
    if max_dist is not None:
        try:
            if cmd.get_distance(tmp1, tmp2) > float(max_dist):
                return
        except Exception:
            pass
    cmd.distance(dist_name, tmp1, tmp2)


def _pseudoatom(
    name: str,
    pos: Sequence[float],
    color: Optional[str] = None,
) -> None:
    cmd.pseudoatom(name, pos=list(pos))
    if color:
        cmd.color(color, name)


def _render_visualizer_data(
    obj: str,
    group_prefix: str,
    site_tag: str,
    vis,
    involved_ids: Set[int],
    water_ids: Set[int],
    metal_ids: Set[int],
    created_objects: List[str],
) -> None:
    tmp_bs = f"{group_prefix}_{site_tag}_tmp_bs"
    tmp_lig = f"{group_prefix}_{site_tag}_tmp_lig"

    # Hydrophobic contacts
    hydrophobic_name = f"{group_prefix}_{site_tag}_Hydrophobic"
    hydrophobic_drawn = False
    for bs_id, lig_id in sorted(set(vis.hydrophobic_contacts.pairs_ids)):
        involved_ids.update([int(bs_id), int(lig_id)])
        _distance_between_ids(hydrophobic_name, obj, bs_id, lig_id, tmp_bs, tmp_lig)
        hydrophobic_drawn = True
    if hydrophobic_drawn:
        created_objects.append(hydrophobic_name)
        cmd.set("dash_color", "orange", hydrophobic_name)
        cmd.set("dash_radius", 0.05, hydrophobic_name)
        cmd.hide("labels", hydrophobic_name)

    # Hydrogen bonds
    hbonds_name = f"{group_prefix}_{site_tag}_HBonds"
    hbonds_drawn = False
    for acc_id, don_id in sorted(set(vis.hbonds.ldon_id + vis.hbonds.pdon_id)):
        involved_ids.update([int(acc_id), int(don_id)])
        _distance_between_ids(hbonds_name, obj, acc_id, don_id, tmp_bs, tmp_lig)
        hbonds_drawn = True
    if hbonds_drawn:
        created_objects.append(hbonds_name)
        cmd.set("dash_color", "yellow", hbonds_name)
        cmd.set("dash_radius", 0.07, hbonds_name)
        cmd.hide("labels", hbonds_name)

    # Halogen bonds
    halogen_name = f"{group_prefix}_{site_tag}_HalogenBonds"
    halogen_drawn = False
    for entry in vis.halogen_bonds:
        involved_ids.update([int(entry.don_id), int(entry.acc_id)])
        _distance_between_ids(halogen_name, obj, entry.don_id, entry.acc_id, tmp_bs, tmp_lig)
        halogen_drawn = True
    if halogen_drawn:
        created_objects.append(halogen_name)
        cmd.set("dash_color", "skyblue", halogen_name)
        cmd.set("dash_radius", 0.06, halogen_name)
        cmd.hide("labels", halogen_name)

    # Water bridges: draw donor-water and acceptor-water
    wbridge_name = f"{group_prefix}_{site_tag}_WaterBridges"
    wbridge_drawn = False
    for entry in vis.waterbridges:
        involved_ids.update([int(entry.don_id), int(entry.acc_id), int(entry.water_id)])
        water_ids.add(int(entry.water_id))
        _distance_between_ids(wbridge_name, obj, entry.don_id, entry.water_id, tmp_bs, tmp_lig)
        _distance_between_ids(wbridge_name, obj, entry.acc_id, entry.water_id, tmp_bs, tmp_lig)
        wbridge_drawn = True
    if wbridge_drawn:
        created_objects.append(wbridge_name)
        cmd.set("dash_color", "cyan", wbridge_name)
        cmd.set("dash_radius", 0.05, wbridge_name)
        cmd.hide("labels", wbridge_name)

    # Metal complexes
    metal_name = f"{group_prefix}_{site_tag}_MetalComplexes"
    metal_drawn = False
    for entry in vis.metal_complexes:
        involved_ids.update([int(entry.metal_id), int(entry.target_id)])
        metal_ids.add(int(entry.metal_id))
        _distance_between_ids(metal_name, obj, entry.metal_id, entry.target_id, tmp_bs, tmp_lig)
        metal_drawn = True
    if metal_drawn:
        created_objects.append(metal_name)
        cmd.set("dash_color", "gray70", metal_name)
        cmd.set("dash_radius", 0.06, metal_name)
        cmd.hide("labels", metal_name)

    # Pi-stacking (ring centroids)
    pistack_p = f"{group_prefix}_{site_tag}_PiStackingP"
    pistack_t = f"{group_prefix}_{site_tag}_PiStackingT"
    pistack_p_drawn = False
    pistack_t_drawn = False
    for i, entry in enumerate(vis.pistacking, start=1):
        involved_ids.update(int(v) for v in entry.proteinring_atoms)
        involved_ids.update(int(v) for v in entry.ligandring_atoms)

        ps_p = f"{group_prefix}_{site_tag}_ps_pistack_p_{i}"
        ps_l = f"{group_prefix}_{site_tag}_ps_pistack_l_{i}"
        created_objects.extend([ps_p, ps_l])
        _pseudoatom(ps_p, entry.proteinring_center, color="tv_green")
        _pseudoatom(ps_l, entry.ligandring_center, color="tv_green")

        dist_name = pistack_p if str(entry.type).upper().startswith("P") else pistack_t
        cmd.distance(dist_name, ps_p, ps_l)
        if dist_name == pistack_p:
            pistack_p_drawn = True
        else:
            pistack_t_drawn = True
    if pistack_p_drawn:
        created_objects.append(pistack_p)
        cmd.set("dash_color", "green", pistack_p)
        cmd.set("dash_radius", 0.05, pistack_p)
        cmd.hide("labels", pistack_p)
    if pistack_t_drawn:
        created_objects.append(pistack_t)
        cmd.set("dash_color", "green", pistack_t)
        cmd.set("dash_radius", 0.05, pistack_t)
        cmd.hide("labels", pistack_t)

    # Pi-cation
    picat_name = f"{group_prefix}_{site_tag}_PiCation"
    picat_drawn = False
    for i, entry in enumerate(vis.pication, start=1):
        involved_ids.update(int(v) for v in entry.ring_atoms)
        involved_ids.update(int(v) for v in entry.charge_atoms)

        ps_ring = f"{group_prefix}_{site_tag}_ps_picat_ring_{i}"
        ps_charge = f"{group_prefix}_{site_tag}_ps_picat_charge_{i}"
        created_objects.extend([ps_ring, ps_charge])
        _pseudoatom(ps_ring, entry.ring_center, color="tv_green")
        _pseudoatom(ps_charge, entry.charge_center, color="tv_red")

        cmd.distance(picat_name, ps_ring, ps_charge)
        picat_drawn = True
    if picat_drawn:
        created_objects.append(picat_name)
        cmd.set("dash_color", "lightgreen", picat_name)
        cmd.set("dash_radius", 0.05, picat_name)
        cmd.hide("labels", picat_name)

    # Salt bridges
    sbridge_name = f"{group_prefix}_{site_tag}_SaltBridges"
    sbridge_drawn = False
    for i, entry in enumerate(vis.saltbridges, start=1):
        involved_ids.update(int(v) for v in entry.positive_atoms)
        involved_ids.update(int(v) for v in entry.negative_atoms)

        ps_pos = f"{group_prefix}_{site_tag}_ps_sbridge_pos_{i}"
        ps_neg = f"{group_prefix}_{site_tag}_ps_sbridge_neg_{i}"
        created_objects.extend([ps_pos, ps_neg])
        _pseudoatom(ps_pos, entry.positive_center, color="tv_blue")
        _pseudoatom(ps_neg, entry.negative_center, color="tv_red")

        cmd.distance(sbridge_name, ps_pos, ps_neg)
        sbridge_drawn = True
    if sbridge_drawn:
        created_objects.append(sbridge_name)
        cmd.set("dash_color", "magenta", sbridge_name)
        cmd.set("dash_radius", 0.07, sbridge_name)
        cmd.hide("labels", sbridge_name)

    cmd.delete(tmp_bs)
    cmd.delete(tmp_lig)


def plip_interface(
    obj: str,
    ligand: str = DEFAULT_LIGAND_SELECTION,
    protein: str = DEFAULT_PROTEIN_SELECTION,
    group: str = "",
    hide_non_interface: int = 1,
    plip_python: str = "",
) -> None:
    """
    Use pharmai/plip to detect and visualize interactions between two partners.

    This command is designed to be safe in an existing PyMOL session (no reinitialize).

    Args:
        obj: PyMOL object name containing both partners.
        ligand: Ligand/partner-1 selection (relative to `obj`), default: organic ligand.
        protein: Protein/partner-2 selection (relative to `obj`), default: polymer.protein.
        group: Prefix/group name for created objects. Default: plip_<obj>.
        hide_non_interface: If 1, hides non-interface parts of partner-2.
        plip_python: Optional path to an external Python that can `import plip`.
            If empty, uses env var PYMOL_PLIP_PYTHON, then falls back to in-PyMOL imports.
    """
    obj = (obj or "").strip()
    if not obj:
        raise ValueError("obj is required")

    group_prefix = _normalize_group_prefix(group or f"plip_{obj}", "plip")
    hide_non_interface = int(hide_non_interface) != 0
    plip_python = _resolve_plip_python(plip_python)

    # Canonical path: export and atom-map on the PyMOL main thread, perform
    # bounded HTTP in a Qt worker, then render through an exact manifest.  The
    # historical implementation remains below only as source compatibility for
    # old sessions; it is deliberately unreachable from this command.
    try:
        try:
            from pymolplugins.interactions.controller import pp_interact_run
        except ImportError:
            from pymol_interactions import pp_interact_run  # type: ignore
        if plip_python:
            print(
                "plip_python is deprecated and ignored; the canonical command "
                "uses PYMOLPLUGINS_WORKER_URL over HTTP"
            )
        return pp_interact_run(
            "plip_publication" if hide_non_interface else "plip_overview",
            obj,
            partner1=protein,
            partner2=ligand,
            group=group_prefix,
            async_mode=1,
        )
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
        return None

    try:
        ligand_sel = f"({obj}) and ({ligand})"
        protein_sel = f"({obj}) and ({protein})"

        if cmd.count_atoms(ligand_sel) == 0 and ligand == DEFAULT_LIGAND_SELECTION:
            ligand = "hetatm and not solvent"
            ligand_sel = f"({obj}) and ({ligand})"

        if cmd.count_atoms(ligand_sel) == 0:
            raise ValueError(f"Empty ligand selection: {ligand_sel}")
        if cmd.count_atoms(protein_sel) == 0:
            raise ValueError(f"Empty protein selection: {protein_sel}")

        ligand_sites_original = _collect_residue_sites(ligand_sel)

        complex_sel = f"({ligand_sel}) or ({protein_sel})"
        old_pdb_conect_all = None
        old_pdb_conect_nodup = None
        try:
            old_pdb_conect_all = cmd.get_setting_text("pdb_conect_all")
            old_pdb_conect_nodup = cmd.get_setting_text("pdb_conect_nodup")
            cmd.set("pdb_conect_all", 1)
            cmd.set("pdb_conect_nodup", 1)
        except Exception:
            old_pdb_conect_all = None
            old_pdb_conect_nodup = None

        pdb_str = cmd.get_pdbstr(complex_sel, state=-1)

        try:
            if old_pdb_conect_all is not None:
                cmd.set("pdb_conect_all", old_pdb_conect_all)
            if old_pdb_conect_nodup is not None:
                cmd.set("pdb_conect_nodup", old_pdb_conect_nodup)
        except Exception:
            pass

        if not pdb_str or not pdb_str.strip():
            raise ValueError("Failed to export PDB for the selected partners")

        pdb_str = _renumber_pdb_serials(pdb_str)

        plip_interface_clear(group_prefix)

        structure_obj = f"{group_prefix}_structure"
        cmd.read_pdbstr(pdb_str, structure_obj)

        ligand_sel_struct = f"({structure_obj}) and ({ligand})"
        if cmd.count_atoms(ligand_sel_struct) == 0 and ligand == DEFAULT_LIGAND_SELECTION:
            ligand = "hetatm and not solvent"
            ligand_sel_struct = f"({structure_obj}) and ({ligand})"

        ligand_sites = _collect_residue_sites(ligand_sel_struct)
        if not ligand_sites:
            ligand_sites = ligand_sites_original

        bsid_to_vis = {}
        plip_mode = "inproc"
        try:
            PDBComplex, VisualizerData = _safe_import_plip()
            mol = PDBComplex()
            mol.load_pdb(pdb_str, as_string=True)
            mol.analyze()
            for bsid in getattr(mol, "interaction_sets", {}).keys():
                bsid_to_vis[bsid] = VisualizerData(mol, bsid)
        except ImportError:
            if not plip_python:
                raise
            payload = _run_plip_external(pdb_str, plip_python)
            sites = payload.get("sites") or []
            for site in sites:
                bsid = (site or {}).get("bsid")
                if bsid:
                    bsid_to_vis[bsid] = _site_dict_to_vis(site)
            plip_mode = f"external:{plip_python}"

        if not bsid_to_vis:
            raise ValueError(
                "PLIP did not report any interaction sets. "
                "Check that partner-1 is a ligand (HETATM) and partner-2 is a protein."
            )

        matching_bsids: List[str] = []
        for bsid in bsid_to_vis.keys():
            parsed = _parse_bsid(bsid)
            if parsed and parsed in ligand_sites:
                matching_bsids.append(bsid)

        if not matching_bsids:
            if len(bsid_to_vis) == 1:
                matching_bsids = list(bsid_to_vis.keys())
                print(
                    "Warning: ligand selection did not match PLIP bsid; "
                    "falling back to the only detected ligand."
                )
            else:
                raise ValueError(
                    "Could not match ligand selection to PLIP binding sites. "
                    "Please specify a ligand residue explicitly (resn/chain/resi)."
                )

        involved_ids: Set[int] = set()
        water_ids: Set[int] = set()
        metal_ids: Set[int] = set()
        created: List[str] = [structure_obj]

        for bsid in matching_bsids:
            site_tag = _sanitize_name(bsid.replace(":", "_"), "site")
            vis = bsid_to_vis[bsid]
            _render_visualizer_data(
                structure_obj,
                group_prefix,
                site_tag,
                vis,
                involved_ids,
                water_ids,
                metal_ids,
                created,
            )

        ligand_sel_name = f"{group_prefix}_partner1"
        protein_sel_name = f"{group_prefix}_partner2"
        involved_sel_name = f"{group_prefix}_involved"
        interface_protein_name = f"{group_prefix}_interface_protein"
        water_sel_name = f"{group_prefix}_waters"
        metal_sel_name = f"{group_prefix}_metals"
        created.extend(
            [
                ligand_sel_name,
                protein_sel_name,
                involved_sel_name,
                interface_protein_name,
                water_sel_name,
                metal_sel_name,
            ]
        )

        ligand_sel_new = f"({structure_obj}) and ({ligand})"
        if cmd.count_atoms(ligand_sel_new):
            cmd.select(ligand_sel_name, ligand_sel_new)
        else:
            ligand_site_expr = []
            for resn, chain, resv in sorted(ligand_sites):
                if chain:
                    ligand_site_expr.append(
                        f"(resn {resn} and chain {chain} and resi {resv})"
                    )
                else:
                    ligand_site_expr.append(f"(resn {resn} and resi {resv})")
            cmd.select(
                ligand_sel_name, f"({structure_obj}) and ({' or '.join(ligand_site_expr)})"
            )

        protein_sel_new = f"({structure_obj}) and ({protein})"
        if cmd.count_atoms(protein_sel_new):
            cmd.select(protein_sel_name, protein_sel_new)
        else:
            cmd.select(protein_sel_name, f"({structure_obj}) and not ({ligand_sel_name})")

        _select_by_ids(involved_sel_name, structure_obj, sorted(involved_ids))
        cmd.select(interface_protein_name, f"byres (({involved_sel_name}) and ({protein_sel_name}))")
        _select_by_ids(water_sel_name, structure_obj, sorted(water_ids))
        _select_by_ids(metal_sel_name, structure_obj, sorted(metal_ids))

        cmd.hide("everything", structure_obj)
        cmd.show("sticks", ligand_sel_name)
        if hide_non_interface:
            cmd.show("sticks", interface_protein_name)
        else:
            cmd.show("cartoon", protein_sel_name)
            cmd.show("sticks", interface_protein_name)

        if cmd.count_atoms(water_sel_name):
            cmd.show("spheres", water_sel_name)
            cmd.set("sphere_scale", 0.25, water_sel_name)
        if cmd.count_atoms(metal_sel_name):
            cmd.show("spheres", metal_sel_name)
            cmd.set("sphere_scale", 0.35, metal_sel_name)

        cmd.group(group_prefix, structure_obj)
        for name in sorted(set(created)):
            if name == group_prefix:
                continue
            try:
                cmd.group(group_prefix, name)
            except Exception:
                continue
        cmd.zoom(f"({ligand_sel_name}) or ({interface_protein_name})", buffer=2.0)

        print(
            f"PLIP interactions ready: group={group_prefix}, structure={structure_obj}, mode={plip_mode}"
        )

    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()


class PlipInteractionsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PLIP Interactions (Protein–Ligand)")
        self.setMinimumWidth(520)
        self._build_ui()
        self._refresh_objects()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout()

        form = QtWidgets.QFormLayout()

        self.object_combo = QtWidgets.QComboBox()
        form.addRow("Object:", self.object_combo)

        self.ligand_edit = QtWidgets.QLineEdit(DEFAULT_LIGAND_SELECTION)
        form.addRow("Partner-1 (ligand) selection:", self.ligand_edit)

        self.protein_edit = QtWidgets.QLineEdit(DEFAULT_PROTEIN_SELECTION)
        form.addRow("Partner-2 (protein) selection:", self.protein_edit)

        self.plip_python_edit = QtWidgets.QLineEdit(os.environ.get(PLIP_PYTHON_ENVVAR, ""))
        self.plip_python_edit.setPlaceholderText(
            r"Optional (Windows): C:\Miniconda3\envs\plip\python.exe"
        )
        form.addRow("PLIP Python (optional):", self.plip_python_edit)

        self.group_edit = QtWidgets.QLineEdit("")
        self.group_edit.setPlaceholderText("plip_<object>")
        form.addRow("Group prefix:", self.group_edit)

        self.hide_checkbox = QtWidgets.QCheckBox("Hide non-interface parts (partner-2)")
        self.hide_checkbox.setChecked(True)
        form.addRow("", self.hide_checkbox)

        layout.addLayout(form)

        btns = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton("Refresh Objects")
        self.refresh_btn.clicked.connect(self._refresh_objects)
        btns.addWidget(self.refresh_btn)

        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.clear_btn.clicked.connect(self._clear)
        btns.addWidget(self.clear_btn)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self._run)
        btns.addWidget(self.run_btn)

        layout.addLayout(btns)
        self.setLayout(layout)

    def _refresh_objects(self) -> None:
        try:
            names = cmd.get_names("objects") or []
        except Exception:
            names = []
        self.object_combo.clear()
        self.object_combo.addItems(names)

    def _clear(self) -> None:
        obj = self.object_combo.currentText().strip()
        prefix = self.group_edit.text().strip() or f"plip_{obj}"
        plip_interface_clear(prefix)

    def _run(self) -> None:
        obj = self.object_combo.currentText().strip()
        ligand = self.ligand_edit.text().strip() or DEFAULT_LIGAND_SELECTION
        protein = self.protein_edit.text().strip() or DEFAULT_PROTEIN_SELECTION
        plip_python = self.plip_python_edit.text().strip()
        group_prefix = self.group_edit.text().strip() or f"plip_{obj}"
        hide_non_interface = 1 if self.hide_checkbox.isChecked() else 0
        plip_interface(
            obj=obj,
            ligand=ligand,
            protein=protein,
            group=group_prefix,
            hide_non_interface=hide_non_interface,
            plip_python=plip_python,
        )


_DIALOG = None


def show_plip_interactions_dialog():
    global _DIALOG
    if _DIALOG is None:
        _DIALOG = PlipInteractionsDialog()
    _DIALOG.show()
    _DIALOG.raise_()
    _DIALOG.activateWindow()
    return _DIALOG


cmd.extend("plip_interface", plip_interface)
cmd.extend("plip_interface_clear", plip_interface_clear)
cmd.extend("show_plip_interactions_dialog", show_plip_interactions_dialog)


def __init_plugin__(app=None):
    from pymol.plugins import addmenuitemqt

    addmenuitemqt("PLIP Interactions", show_plip_interactions_dialog)
