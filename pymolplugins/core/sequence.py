"""PyMOL-independent residue indexing and sequence motif matching.

The central invariant in this module is that sequence letters and residue
metadata are derived from the same atom stream.  This avoids the positional
drift caused by pairing a FASTA string with a separately collected CA list.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .parameters import as_bool, as_int


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
    "SEC": "U",
    "PYL": "O",
    "UNK": "X",
    # Common protein modifications. Unknown protein-like residues are retained
    # as X rather than omitted, so downstream residue positions cannot shift.
    "MSE": "M",
    "FME": "M",
    "HYP": "P",
    "SEP": "S",
    "TPO": "T",
    "PTR": "Y",
    "CSO": "C",
    "CSD": "C",
    "CME": "C",
    "KCX": "K",
    "LLP": "K",
    "MLZ": "K",
    "PCA": "E",
    "ASH": "D",
    "GLH": "E",
    "HID": "H",
    "HIE": "H",
    "HIP": "H",
    "HSD": "H",
    "HSE": "H",
    "HSP": "H",
    "CYM": "C",
    "CYX": "C",
    "LYN": "K",
}

_NON_PROTEIN_RESIDUES = frozenset(
    (
        "HOH",
        "WAT",
        "DOD",
        "SOL",
        "A",
        "C",
        "G",
        "I",
        "T",
        "U",
        "DA",
        "DC",
        "DG",
        "DI",
        "DT",
        "DU",
        "ACE",
        "NME",
        "NH2",
        "FOR",
    )
)


@dataclass(frozen=True)
class ResidueRecord:
    """One residue and the exact atoms from which its sequence letter came."""

    object: str
    state: int
    segi: str
    chain: str
    resi: str
    resv: Optional[int]
    insertion: str
    resn: str
    one_letter: str
    atom_indices: Tuple[int, ...]
    atom_names: Mapping[str, Tuple[int, ...]]

    def to_dict(self, include_atom_indices: bool = True) -> Dict[str, Any]:
        """Return a JSON-serializable representation."""

        result = {
            "object": self.object,
            "state": self.state,
            "segi": self.segi,
            "chain": self.chain,
            "resi": self.resi,
            "resv": self.resv,
            "insertion": self.insertion,
            "resn": self.resn,
            "one_letter": self.one_letter,
            "atom_names": {
                name: list(indices) for name, indices in self.atom_names.items()
            },
        }
        if include_atom_indices:
            result["atom_indices"] = list(self.atom_indices)
        return result


@dataclass(frozen=True)
class SequenceTrack:
    """Ordered residues for one object/state/segment/chain identity."""

    object: str
    state: int
    segi: str
    chain: str
    residues: Tuple[ResidueRecord, ...]

    @property
    def sequence(self) -> str:
        return "".join(residue.one_letter for residue in self.residues)


@dataclass(frozen=True)
class SequenceIndex:
    """Residue records and chain tracks built from one or more atom streams."""

    records: Tuple[ResidueRecord, ...]
    tracks: Tuple[SequenceTrack, ...]


@dataclass(frozen=True)
class SequenceMatch:
    """A query match with exact positions and residue records."""

    query_index: int
    query: str
    object: str
    state: int
    segi: str
    chain: str
    residue_positions: Tuple[int, ...]
    residues: Tuple[ResidueRecord, ...]

    @property
    def sequence_start(self) -> int:
        return self.residue_positions[0]

    @property
    def sequence_end(self) -> int:
        """Exclusive end of the full matched span, including bounded gaps."""

        return self.residue_positions[-1] + 1

    def to_dict(self, include_atom_indices: bool = True) -> Dict[str, Any]:
        """Return a JSON-serializable match record."""

        return {
            "query_index": self.query_index,
            "query": self.query,
            "object": self.object,
            "state": self.state,
            "segi": self.segi,
            "chain": self.chain,
            "sequence_start": self.sequence_start,
            "sequence_end": self.sequence_end,
            "residue_positions": list(self.residue_positions),
            "residues": [
                residue.to_dict(include_atom_indices=include_atom_indices)
                for residue in self.residues
            ],
        }


class SequenceOccurrenceError(ValueError):
    """Raised when ``occurrence=unique`` does not produce exactly one match."""

    def __init__(self, query: str, match_count: int, *, at_least: bool = False):
        self.query = query
        self.match_count = match_count
        self.at_least = bool(at_least)
        super().__init__(
            "query {!r} expected exactly one match, found {}{}".format(
                query, "at least " if self.at_least else "", match_count
            )
        )


class SequenceMatchLimitError(ValueError):
    """Raised before an ``occurrence=all`` search can grow without bound."""

    def __init__(self, query: str, limit: int):
        self.query = str(query)
        self.limit = int(limit)
        self.observed_at_least = self.limit + 1
        super().__init__(
            "query {!r} exceeds the {}-match safety limit".format(
                self.query, self.limit
            )
        )


@dataclass
class _ResidueBuilder:
    object: str
    state: int
    segi: str
    chain: str
    resi: str
    resv: Optional[int]
    insertion: str
    resn: str
    atom_indices: List[int]
    atom_names: "OrderedDict[str, List[int]]"
    hetero_flags: List[Optional[bool]]


def _atom_value(atom: object, name: str, default: object = "") -> object:
    if isinstance(atom, Mapping):
        return atom.get(name, default)
    return getattr(atom, name, default)


def parse_residue_identifier(
    resi: object, resv: object = None
) -> Tuple[Optional[int], str]:
    """Parse PyMOL residue identifiers such as ``-1`` and ``42A``."""

    text = str(resi).strip()
    match = re.fullmatch(r"([+-]?\d+)(.*)", text)
    if match:
        return int(match.group(1)), match.group(2)
    if resv not in (None, ""):
        try:
            return as_int(resv, name="resv"), ""
        except ValueError:
            pass
    return None, ""


def residue_to_one_letter(
    resn: object, extra_codes: Optional[Mapping[str, str]] = None
) -> str:
    """Convert a residue name, retaining unknown residues as ``X``."""

    code = str(resn).strip().upper()
    if extra_codes:
        extra = {
            str(key).upper(): str(value).upper()
            for key, value in extra_codes.items()
        }
        if code in extra:
            letter = extra[code]
            if len(letter) != 1 or not letter.isalpha():
                raise ValueError("extra residue codes must map to one letter")
            return letter
    return AMINO_ACID_CODES.get(code, "X")


def _is_protein_like(builder: _ResidueBuilder) -> bool:
    if builder.resn in _NON_PROTEIN_RESIDUES:
        return False
    if builder.resn in AMINO_ACID_CODES:
        return True
    backbone = frozenset(builder.atom_names).intersection(("N", "CA", "C", "O"))
    known_flags = [flag for flag in builder.hetero_flags if flag is not None]
    if known_flags and not all(known_flags):
        return True
    return len(backbone) >= 2


def build_residue_records(
    atoms: Iterable[object],
    object_name: str,
    state: int = 1,
    *,
    protein_only: bool = False,
    extra_codes: Optional[Mapping[str, str]] = None,
) -> Tuple[ResidueRecord, ...]:
    """Build ordered residue records from one atom stream.

    Atoms may be ordinary objects (including PyMOL ``Atom`` instances) or
    mappings with ``index``, ``name``, ``segi``, ``chain``, ``resi``, ``resv``,
    ``resn`` and optional ``hetatm`` fields. Alternate locations merge into a
    single residue while preserving every atom index for exact selections.
    """

    parsed_state = as_int(state, minimum=0, name="state")
    builders: "OrderedDict[Tuple[str, str, str], _ResidueBuilder]" = OrderedDict()

    for atom in atoms:
        index = as_int(_atom_value(atom, "index", None), minimum=1, name="atom index")
        name = str(_atom_value(atom, "name", "")).strip().upper()
        segi = str(_atom_value(atom, "segi", "")).strip()
        chain = str(_atom_value(atom, "chain", "")).strip()
        resi = str(_atom_value(atom, "resi", "")).strip()
        resn = str(_atom_value(atom, "resn", "UNK")).strip().upper() or "UNK"
        resv, insertion = parse_residue_identifier(
            resi, _atom_value(atom, "resv", None)
        )
        key = (segi, chain, resi)
        builder = builders.get(key)
        if builder is None:
            builder = _ResidueBuilder(
                object=str(object_name),
                state=parsed_state,
                segi=segi,
                chain=chain,
                resi=resi,
                resv=resv,
                insertion=insertion,
                resn=resn,
                atom_indices=[],
                atom_names=OrderedDict(),
                hetero_flags=[],
            )
            builders[key] = builder
        if index not in builder.atom_indices:
            builder.atom_indices.append(index)
        named_indices = builder.atom_names.setdefault(name, [])
        if index not in named_indices:
            named_indices.append(index)
        raw_hetero = _atom_value(atom, "hetatm", None)
        if raw_hetero is None:
            hetero = None
        else:
            try:
                hetero = as_bool(raw_hetero)
            except ValueError:
                hetero = bool(raw_hetero)
        builder.hetero_flags.append(hetero)

    records = []
    for builder in builders.values():
        if protein_only and not _is_protein_like(builder):
            continue
        records.append(
            ResidueRecord(
                object=builder.object,
                state=builder.state,
                segi=builder.segi,
                chain=builder.chain,
                resi=builder.resi,
                resv=builder.resv,
                insertion=builder.insertion,
                resn=builder.resn,
                one_letter=residue_to_one_letter(builder.resn, extra_codes),
                atom_indices=tuple(builder.atom_indices),
                atom_names={
                    name: tuple(indices)
                    for name, indices in builder.atom_names.items()
                },
            )
        )
    return tuple(records)


def build_sequence_index(records: Iterable[ResidueRecord]) -> SequenceIndex:
    """Group residue records into stable object/state/segment/chain tracks."""

    record_tuple = tuple(records)
    grouped: "OrderedDict[Tuple[str, int, str, str], List[ResidueRecord]]" = OrderedDict()
    for residue in record_tuple:
        key = (residue.object, residue.state, residue.segi, residue.chain)
        grouped.setdefault(key, []).append(residue)
    tracks = tuple(
        SequenceTrack(
            object=key[0],
            state=key[1],
            segi=key[2],
            chain=key[3],
            residues=tuple(group),
        )
        for key, group in grouped.items()
    )
    return SequenceIndex(records=record_tuple, tracks=tracks)


def index_atom_streams(
    streams: Iterable[Tuple[str, int, Iterable[object]]],
    *,
    protein_only: bool = False,
    extra_codes: Optional[Mapping[str, str]] = None,
) -> SequenceIndex:
    """Build one index from ``(object_name, state, atoms)`` streams."""

    records = []
    for object_name, state, atoms in streams:
        records.extend(
            build_residue_records(
                atoms,
                object_name,
                state,
                protein_only=protein_only,
                extra_codes=extra_codes,
            )
        )
    return build_sequence_index(records)


def normalize_query(query: object) -> str:
    """Uppercase a query and remove explicit whitespace and ``-`` gaps."""

    if query is None:
        raise ValueError("sequence query is empty")
    normalized = re.sub(r"[\s-]+", "", str(query)).upper()
    if not normalized:
        raise ValueError("sequence query is empty")
    if not re.fullmatch(r"[A-Z]+", normalized):
        raise ValueError("sequence query must contain only letters, whitespace, or '-'")
    return normalized


def split_queries(queries: object, separator: str = "|") -> Tuple[str, ...]:
    """Split and normalize query text using an explicit separator."""

    if queries is None:
        raise ValueError("no sequence queries were provided")
    if not isinstance(separator, str):
        raise ValueError("query separator must be a string")
    if isinstance(queries, str):
        if separator in (r"\n", r"\t", r"\r"):
            separator = {r"\n": "\n", r"\t": "\t", r"\r": "\r"}[separator]
        if not separator:
            raise ValueError("query separator cannot be empty")
        parts: Sequence[object] = queries.split(separator)
    elif isinstance(queries, Iterable):
        parts = list(queries)
    else:
        parts = (queries,)
    normalized = tuple(
        normalize_query(part)
        for part in parts
        if part is not None and str(part).strip()
    )
    if not normalized:
        raise ValueError("no sequence queries were provided")
    return normalized


def _letter_matches(sequence_letter: str, query_letter: str) -> bool:
    return query_letter == "X" or sequence_letter.upper() == query_letter


def _exact_positions(sequence: str, query: str) -> Iterator[Tuple[int, ...]]:
    if len(query) > len(sequence):
        return
    for start in range(len(sequence) - len(query) + 1):
        candidate = tuple(range(start, start + len(query)))
        if all(
            _letter_matches(sequence[position], query[offset])
            for offset, position in enumerate(candidate)
        ):
            yield candidate


def _bounded_positions(
    sequence: str, query: str, max_gap: int
) -> Iterator[Tuple[int, ...]]:
    if len(query) > len(sequence):
        return
    # Iterative depth-first enumeration avoids recursion failures for long
    # motifs while retaining the deterministic lexicographic order.
    path: List[int] = []
    next_candidates = [0]
    while next_candidates:
        depth = len(path)
        start = 0 if depth == 0 else path[-1] + 1
        stop = (
            len(sequence)
            if depth == 0
            else min(len(sequence), path[-1] + max_gap + 2)
        )
        position = max(start, next_candidates[-1])
        while position < stop and not _letter_matches(
            sequence[position], query[depth]
        ):
            position += 1
        if position >= stop:
            next_candidates.pop()
            if path:
                path.pop()
            continue
        next_candidates[-1] = position + 1
        path.append(position)
        if len(path) == len(query):
            yield tuple(path)
            path.pop()
        else:
            next_candidates.append(position + 1)


def find_sequence_matches(
    index: SequenceIndex,
    queries: Iterable[object],
    *,
    gap_mode: str = "exact",
    max_gap: int = 0,
    occurrence: str = "all",
    chain: Optional[str] = None,
    max_matches: int = 10000,
) -> Tuple[SequenceMatch, ...]:
    """Find deterministic exact or bounded-gap matches across all tracks.

    ``max_gap`` is the maximum count of intervening residues between each pair
    of adjacent query letters.  ``X`` is a one-residue wildcard.  Exact search
    examines every start position, so overlapping matches are retained.

    Matching is streamed. ``occurrence=first`` stops after the first match and
    ``occurrence=unique`` stops as soon as ambiguity is proven by a second
    match. ``occurrence=all`` is bounded by ``max_matches`` across the whole
    request; exceeding the bound raises :class:`SequenceMatchLimitError`
    before an unbounded result can be accumulated.
    """

    if queries is None:
        raise ValueError("no sequence queries were provided")
    if isinstance(queries, str):
        normalized_queries = (normalize_query(queries),)
    else:
        normalized_queries = tuple(normalize_query(query) for query in queries)
    if not normalized_queries:
        raise ValueError("no sequence queries were provided")
    mode = str(gap_mode).strip().lower()
    if mode in ("gap", "gapped"):
        mode = "bounded"
    if mode not in ("exact", "bounded"):
        raise ValueError("gap_mode must be 'exact' or 'bounded'")
    parsed_gap = as_int(max_gap, minimum=0, name="max_gap")
    policy = str(occurrence).strip().lower()
    if policy not in ("all", "first", "unique"):
        raise ValueError("occurrence must be 'all', 'first', or 'unique'")
    parsed_limit = as_int(
        max_matches, minimum=1, maximum=100000, name="max_matches"
    )

    all_matches: List[SequenceMatch] = []
    for query_index, query in enumerate(normalized_queries):
        query_matches: List[SequenceMatch] = []
        first_found = False
        for track in index.tracks:
            if chain is not None and track.chain != chain:
                continue
            if mode == "exact":
                position_sets = _exact_positions(track.sequence, query)
            else:
                position_sets = _bounded_positions(track.sequence, query, parsed_gap)
            for positions in position_sets:
                match = SequenceMatch(
                    query_index=query_index,
                    query=query,
                    object=track.object,
                    state=track.state,
                    segi=track.segi,
                    chain=track.chain,
                    residue_positions=positions,
                    residues=tuple(
                        track.residues[position] for position in positions
                    ),
                )
                if policy == "all":
                    if len(all_matches) >= parsed_limit:
                        raise SequenceMatchLimitError(query, parsed_limit)
                    all_matches.append(match)
                    continue
                query_matches.append(match)
                if policy == "first":
                    first_found = True
                    break
                if len(query_matches) == 2:
                    raise SequenceOccurrenceError(query, 2, at_least=True)
            if first_found:
                break
        if policy == "unique":
            if len(query_matches) != 1:
                raise SequenceOccurrenceError(query, len(query_matches))
            all_matches.extend(query_matches)
        elif policy == "first":
            all_matches.extend(query_matches)
    return tuple(all_matches)
