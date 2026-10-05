"""Stable, collision-safe filenames for legacy navigation selections."""

import hashlib
import os
import re
from typing import List, Tuple


STRUCTURE_SUFFIXES: Tuple[str, ...] = (".pdb.gz", ".cif.gz", ".pdb", ".cif")


def _split_structure_name(filename: str) -> Tuple[str, str]:
    basename = os.path.basename(str(filename))
    lowered = basename.lower()
    for suffix in STRUCTURE_SUFFIXES:
        if lowered.endswith(suffix):
            return basename[: -len(suffix)], suffix.lstrip(".").replace(".", "_")
    return basename, "structure"


def derive_structure_stem(filename: str, prefix: str = "structure") -> str:
    """Return the historical PyMOL-safe stem for one structure path."""

    base_name, _suffix = _split_structure_name(filename)
    starts_with_underscore = base_name.startswith("_")
    value = re.sub(r"[^0-9A-Za-z_]+", "_", base_name).strip("_") or prefix
    if value[0].isdigit() or starts_with_underscore:
        value = f"{prefix}_{value}"
    return value


def _colliding_sources(source_path: str, stem: str, prefix: str) -> List[str]:
    directory = os.path.dirname(os.path.abspath(source_path))
    if not os.path.isdir(directory):
        return []
    values = []
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        _base, suffix = _split_structure_name(name)
        if suffix == "structure":
            continue
        if derive_structure_stem(name, prefix=prefix) == stem:
            values.append(path)
    return sorted(values, key=lambda value: (os.path.basename(value).casefold(), value))


def selected_output_name(
    source_path: str,
    fallback: str,
    prefix: str = "structure",
) -> str:
    """Return the legacy name unless sibling inputs would overwrite each other."""

    source = str(source_path or fallback)
    stem = derive_structure_stem(source, prefix=prefix)
    collisions = _colliding_sources(str(source_path), stem, prefix) if source_path else []
    if len(collisions) <= 1:
        return stem + ".pdb"

    _base, suffix_token = _split_structure_name(source)
    same_suffix = [
        value
        for value in collisions
        if _split_structure_name(value)[1] == suffix_token
    ]
    discriminator = suffix_token
    if len(same_suffix) > 1:
        digest = hashlib.sha1(
            os.path.basename(source).encode("utf-8", errors="surrogatepass")
        ).hexdigest()[:8]
        discriminator = f"{suffix_token}_{digest}"
    return f"{stem}__{discriminator}.pdb"
