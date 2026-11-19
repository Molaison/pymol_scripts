"""Use an optional local TmDet installation to place a membrane in the input frame."""

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from xml.etree import ElementTree

from .review_membrane import MembranePlacement


# This runs on the same computer as PyMOL. It never stages inputs on a server.
_RUN_TMDET = """
from pathlib import Path
import os
import subprocess
import sys
from tempfile import TemporaryDirectory

prefix = Path(os.environ.get('XDG_DATA_HOME') or (Path.home() / '.local' / 'share'))
prefix = prefix / 'pymolplugins' / 'tmdet-4.1.9'
binary = prefix / 'bin' / 'tmdet'
environment = prefix / 'tmdet.env'
ccd = prefix / 'data' / 'ccd' / 'Z' / 'Z' / 'ZZZ.cif'
if not (binary.is_file() and environment.is_file() and ccd.is_file()):
    sys.exit(127)

with TemporaryDirectory(prefix='pymol-tmdet-') as directory:
    source = Path(directory) / 'candidate.ent'
    output = Path(directory) / 'placement.xml'
    source.write_bytes(sys.stdin.buffer.read())
    result = subprocess.run([str(binary), '-e', str(environment), '-pi', str(source),
                             '-xo', str(output), '-r', '-xf3', '-nc'], cwd=directory,
                            capture_output=True, timeout=20)
    if result.returncode or not output.is_file():
        sys.stderr.buffer.write((result.stderr or result.stdout)[-1000:])
        sys.exit(result.returncode or 1)
    sys.stdout.buffer.write(output.read_bytes())
"""


def _local_runner():
    if os.name != "nt":
        return [sys.executable, "-c", _RUN_TMDET]
    wsl = shutil.which("wsl.exe")
    if wsl:
        return [wsl, "-d", "Debian", "-e", "python3", "-c", _RUN_TMDET]
    return []


def _parse_placement(xml):
    import numpy as np

    root = ElementTree.fromstring(xml)
    if root.get("TMP") == "no":
        return None
    membrane = root.find("{*}MEMBRANE")
    if root.get("TMP") != "yes" or membrane is None:
        raise ValueError("TmDet did not return a definite membrane classification")
    normal_node = membrane.find("{*}NORMAL")
    rows = [membrane.find("{*}TMATRIX/{*}ROW" + axis) for axis in "XYZ"]
    if normal_node is None or any(row is None for row in rows):
        raise ValueError("TmDet did not return a single planar membrane transform")
    axis = np.array([float(normal_node.attrib[key]) for key in "XYZ"])
    transform = np.array([[float(row.attrib[key]) for key in "XYZ"] for row in rows])
    translation = np.array([float(row.attrib["T"]) for row in rows])
    half_width = np.linalg.norm(axis)
    if (not 8 <= half_width <= 25 or not np.all(np.isfinite(translation))
            or not np.allclose(transform @ transform.T, np.eye(3), atol=0.002)):
        raise ValueError("TmDet returned an invalid planar membrane transform")
    # TmDet 4.1.9 applies the XML translation before the rotation.
    segments = tuple(
        (chain.get("CHAINID", ""), start, end)
        for chain in root.findall("{*}CHAIN")
        for region in chain.findall("{*}REGION")
        if region.get("type") in {"H", "B"}
        if (start := region.get("pdb_beg")) and (end := region.get("pdb_end"))
    )
    helical = root.findtext("{*}RAWRES/{*}TMTYPE", "").strip() == "Tm_Alpha"
    return MembranePlacement(-translation, transform.T @ axis / half_width, float(half_width),
                             "TmDet 4.1.9", segments, helical)


@lru_cache(maxsize=8)
def tmdet_slab(pdb):
    """Return a slab or a negative classification from the installed solver."""
    runner = _local_runner()
    if not runner:
        raise FileNotFoundError("Local TmDet runner is unavailable")
    result = subprocess.run(runner, input=pdb.encode("utf-8"), capture_output=True,
                            timeout=24, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode == 127:
        raise FileNotFoundError("Local TmDet or its chemical component data is unavailable")
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "Local TmDet failed")
    return _parse_placement(result.stdout)
