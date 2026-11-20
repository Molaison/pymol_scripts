"""Fast extended-FASTA export and sequence matching for PyMOL.

Commands
--------
get_fasta [selection=enabled [, include_solvent=0 [, state=-1 [, width=80]]]]
find_seq query [, selection=enabled [, color=yellow [, name=seq_match]]]

Standard amino acids use one-letter codes. Modified residues, ligands, ions,
and other non-standard components use ``(RESN)`` tokens, for example
``AC(SEP)D(ATP)(ZN)``.
"""

from pymol import cmd

from collections import OrderedDict
import re
import traceback
import uuid


AMINO_ACID_CODES = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    "ASX": "B",
    "GLX": "Z",
    "XLE": "J",
    "SEC": "U",
    "PYL": "O",
    "UNK": "X",
}

NUCLEIC_ACID_CODES = {
    "A": "A",
    "C": "C",
    "G": "G",
    "T": "T",
    "U": "U",
    "DA": "A",
    "DC": "C",
    "DG": "G",
    "DT": "T",
    "DU": "U",
}

SOLVENT_RESIDUES = {
    "DOD",
    "H2O",
    "HOH",
    "OH2",
    "SOL",
    "TIP",
    "TIP3",
    "TIP3P",
    "WAT",
}


def _as_bool(value):
    """Interpret PyMOL console boolean arguments."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "off", "none"}


def _residue_token(residue_name):
    """Return a one-letter code or an explicit ``(RESN)`` token."""
    residue_name = str(residue_name or "UNK").strip().upper() or "UNK"
    if residue_name in AMINO_ACID_CODES:
        return AMINO_ACID_CODES[residue_name]
    if residue_name in NUCLEIC_ACID_CODES:
        return NUCLEIC_ACID_CODES[residue_name]
    return f"({residue_name})"


def _collect_residues(selection, state, include_solvent):
    """Collect unique residues in PyMOL atom order."""
    model = cmd.get_model(f"({selection})", state=int(state))
    by_key = OrderedDict()
    for atom in model.atom:
        residue_name = str(atom.resn or "UNK").strip().upper() or "UNK"
        if not include_solvent and residue_name in SOLVENT_RESIDUES:
            continue
        key = (atom.model, atom.segi or "", atom.chain or "", atom.resi, residue_name)
        if key not in by_key:
            by_key[key] = {
                "object": atom.model,
                "segi": atom.segi or "",
                "chain": atom.chain or "",
                "resi": atom.resi,
                "resn": residue_name,
                "token": _residue_token(residue_name),
                "indices": [],
            }
        by_key[key]["indices"].append(int(atom.index))
    return list(by_key.values())


def _group_residues(residues):
    """Group residue records by object, segment, and chain."""
    groups = OrderedDict()
    for residue in residues:
        key = (residue["object"], residue["segi"], residue["chain"])
        groups.setdefault(key, []).append(residue)
    return groups


def _wrap_tokens(tokens, width):
    """Wrap without splitting parenthesized residue tokens."""
    width = int(width)
    if width < 1:
        raise ValueError("width must be at least 1")
    lines, current, current_length = [], [], 0
    for token in tokens:
        if current and current_length + len(token) > width:
            lines.append("".join(current))
            current, current_length = [], 0
        current.append(token)
        current_length += len(token)
    if current:
        lines.append("".join(current))
    return lines


def _format_extended_fasta(residues, width):
    """Format collected residue records as extended FASTA text."""
    blocks = []
    for (object_name, segment, chain), chain_residues in _group_residues(residues).items():
        header = f">{object_name}|chain={chain or '_'}"
        if segment:
            header += f"|segi={segment}"
        blocks.append(header)
        blocks.extend(_wrap_tokens([item["token"] for item in chain_residues], width))
    return "\n".join(blocks)


def get_fasta(selection="enabled", include_solvent=0, state=-1, width=80, quiet=0):
    """Print and return extended FASTA for a PyMOL selection.

    Args:
        selection: Atom selection; defaults to all enabled objects.
        include_solvent: Include waters and common solvent residue names.
        state: Coordinate state, where ``-1`` means the current state.
        width: Maximum output line width without splitting ``(RESN)`` tokens.
        quiet: Suppress printing while still returning the text.

    Returns:
        Extended FASTA text, or an empty string after a reported error.
    """
    try:
        residues = _collect_residues(selection, state, _as_bool(include_solvent))
        if not residues:
            raise ValueError(f"selection contains no residues: {selection}")
        result = _format_extended_fasta(residues, width)
        if not _as_bool(quiet):
            print(result)
        return result
    except Exception as exc:
        print(f"get_fasta error: {exc}")
        traceback.print_exc()
        return ""


def _parse_query(query):
    """Parse letters and explicit ``(RESN)`` components into tokens."""
    lines = [line.strip() for line in str(query).splitlines() if not line.lstrip().startswith(">")]
    text = "".join(lines)
    tokens, position = [], 0
    while position < len(text):
        character = text[position]
        if character.isspace() or character == "-":
            position += 1
        elif character == "(":
            closing = text.find(")", position + 1)
            if closing < 0:
                raise ValueError("query has an unclosed '(' token")
            residue_name = text[position + 1 : closing].strip().upper()
            if not residue_name or "(" in residue_name:
                raise ValueError("query contains an invalid '(RESN)' token")
            tokens.append(f"({residue_name})")
            position = closing + 1
        elif character.isalpha():
            tokens.append(character.upper())
            position += 1
        else:
            raise ValueError(f"unsupported query character: {character!r}")
    if not tokens:
        raise ValueError("query is empty")
    return tokens


def _matching_offsets(sequence, query):
    """Return all overlapping exact token-match offsets."""
    size = len(query)
    return [
        start
        for start in range(len(sequence) - size + 1)
        if sequence[start : start + size] == query
    ]


def _create_index_selection(name, indices_by_object, state):
    """Create one named selection from atom indices across objects."""
    cmd.select(name, "none", quiet=1)
    for object_name, indices in indices_by_object.items():
        temporary = f"_seq_find_part_{uuid.uuid4().hex}"
        try:
            cmd.select_list(
                temporary,
                object_name,
                sorted(set(indices)),
                state=int(state),
                mode="index",
                quiet=1,
            )
            cmd.select(name, temporary, quiet=1, merge=1)
        finally:
            cmd.delete(temporary)
    cmd.enable(name)
    return cmd.count_atoms(name)


def find_seq(
    query,
    selection="enabled",
    color="yellow",
    name="seq_match",
    show="sticks",
    state=-1,
    include_solvent=0,
    quiet=0,
):
    """Find an exact token sequence, select its residues, and highlight it.

    Args:
        query: One-letter sequence, optionally containing ``(RESN)`` tokens.
        selection: Search scope; defaults to all enabled objects.
        color: PyMOL color name, or ``none`` to leave colors unchanged.
        name: Name for the resulting atom selection.
        show: Representation to show, or ``none`` to only apply color.
        state: Coordinate state, where ``-1`` means the current state.
        include_solvent: Include waters/common solvents in the searchable tokens.
        quiet: Suppress match-detail and summary messages.

    Returns:
        Number of sequence occurrences found, or ``0`` after a reported error.
    """
    try:
        query_tokens = _parse_query(query)
        residues = _collect_residues(selection, state, _as_bool(include_solvent))
        indices_by_object, match_count = OrderedDict(), 0
        for (object_name, _segment, chain), chain_residues in _group_residues(residues).items():
            sequence = [item["token"] for item in chain_residues]
            for offset in _matching_offsets(sequence, query_tokens):
                matched = chain_residues[offset : offset + len(query_tokens)]
                indices = indices_by_object.setdefault(object_name, [])
                for residue in matched:
                    indices.extend(residue["indices"])
                match_count += 1
                if not _as_bool(quiet):
                    span = f"{matched[0]['resi']}-{matched[-1]['resi']}"
                    print(f"find_seq: {object_name} chain {chain or '_'} residues {span}")

        atom_count = _create_index_selection(name, indices_by_object, state)
        if match_count:
            if str(color).strip().lower() not in {"", "none", "off", "0"}:
                cmd.color(str(color), name)
            if str(show).strip().lower() not in {"", "none", "off", "0"}:
                cmd.show(str(show), name)
        if not _as_bool(quiet):
            print(f"find_seq: {match_count} match(es), {atom_count} atoms in '{name}'")
        return match_count
    except Exception as exc:
        print(f"find_seq error: {exc}")
        traceback.print_exc()
        return 0


for command_name in ("get_fasta", "fasta_ext"):
    cmd.extend(command_name, get_fasta)
    cmd.auto_arg[0][command_name] = [cmd.selection_sc, "selection", ""]

for command_name in ("find_seq", "seq_find"):
    cmd.extend(command_name, find_seq)
    cmd.auto_arg[1][command_name] = [cmd.selection_sc, "selection", ""]
    cmd.auto_arg[2][command_name] = [cmd.color_sc, "color", ""]

print("Sequence utils loaded: get_fasta/fasta_ext, find_seq/seq_find")
