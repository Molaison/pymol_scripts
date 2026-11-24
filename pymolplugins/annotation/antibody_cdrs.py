"""Local IMGT numbering and cartoon-only annotations for the current binder."""

from collections import OrderedDict, defaultdict
from contextlib import redirect_stdout
from dataclasses import dataclass
from threading import Lock
import json
import os
from pathlib import Path
import re
import subprocess
import sys


CDR_COLORS = {"CDR1": "marine", "CDR2": "orange", "CDR3": "magenta"}
_CDR_RANGES = ((27, 38), (56, 65), (105, 117))
_MIN_DOMAIN_LENGTH = 60


@dataclass(frozen=True)
class AntibodyRequest:
    binder: tuple
    target: tuple

    @classmethod
    def from_entry(cls, entry):
        metadata = entry.metadata if entry is not None else {}
        binder = metadata.get("binder_chains") or metadata.get("binder_chain", "")
        target = metadata.get("target_chains") or metadata.get("target_chain") or metadata.get("ipsae_target_chain", "")
        return cls(_chain_names(binder), _chain_names(target))


def _chain_names(value):
    return tuple(part for part in re.split(r"[,;+\s]+", str(value).strip()) if part)


@dataclass(frozen=True)
class ObservedChain:
    model: str
    segment: str
    chain: str
    sequence: str
    residues: tuple
    identity: str


def capture_chains(api, source, request):
    from pymol.exporting import _resn_to_aa
    from .reference_track import molecule_identity

    chains = []
    for model in api.get_object_list(f"%{source}"):
        state = api.get_object_state(model) or min(api.get_state(), api.count_states(f"%{model}"))
        atoms = api.get_model(f"%{model} and polymer.protein and name CA", state).atom
        groups = defaultdict(dict)
        for atom in atoms:
            if request.binder and atom.chain not in request.binder:
                continue
            if not request.binder and atom.chain in request.target:
                continue
            groups[atom.segi, atom.chain].setdefault(atom.resi, atom)
        for (segment, chain), residues in groups.items():
            if len(residues) >= _MIN_DOMAIN_LENGTH:
                sequence = "".join(_resn_to_aa.get(atom.resn, "M" if atom.resn == "MSE" else "X")
                                   for atom in residues.values())
                chains.append(ObservedChain(model, segment, chain, sequence,
                                            tuple((atom.index, atom.resn, atom.resi) for atom in residues.values()),
                                            molecule_identity(api, model)))
    return tuple(chains)


def _numbering_python():
    override = os.environ.get("PYMOLPLUGINS_ANTIBODY_PYTHON", "").strip()
    directory, executable = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    default = Path.home() / ".pymolplugins" / "antibody-numbering" / directory / executable
    return Path(override).expanduser() if override else default


_NUMBERING_CACHE_SIZE = 256
_numbering_cache = OrderedDict()
_numbering_lock = Lock()


def _run_numbering(python, sequences):
    if not python.is_file():
        raise RuntimeError("IMGT numbering needs the local ANARCII environment. See the plugin installation guide.")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run([str(python), "-I", str(Path(__file__).resolve())],
                            input=json.dumps(sequences), capture_output=True, encoding="utf-8",
                            timeout=45, check=False, **options)
    if result.returncode:
        details = result.stderr.strip().splitlines()
        raise RuntimeError(f"ANARCII could not number the binder: {details[-1] if details else result.returncode}")
    return json.loads(result.stdout)


def number_chains(chains):
    if not chains:
        return {}
    sequences = tuple(dict.fromkeys(chain.sequence for chain in chains))
    python = _numbering_python()
    with _numbering_lock:
        missing = tuple(seq for seq in sequences if (python, seq) not in _numbering_cache)
        new_results = _run_numbering(python, missing) if missing else {}
        numbered = {}
        for sequence in sequences:
            key = python, sequence
            domains = new_results[sequence] if sequence in new_results else _numbering_cache[key]
            numbered[sequence] = domains
            _numbering_cache[key] = domains
            _numbering_cache.move_to_end(key)
        while len(_numbering_cache) > _NUMBERING_CACHE_SIZE:
            _numbering_cache.popitem(last=False)
        return numbered


def _annotate_domains(engine, sequence):
    """Map model output only when its numbered residues match the observed sequence."""
    pending, domains = [(0, sequence)], []
    while pending:
        offset, segment = pending.pop()
        if len(segment) < _MIN_DOMAIN_LENGTH:
            continue
        result = engine.number({"binder": segment})["binder"]
        if result.get("error") or result.get("chain_type") not in {"H", "K", "L"} or not result.get("numbering"):
            continue
        numbered = [(position, insertion.strip(), residue) for (position, insertion), residue in result["numbering"]
                    if residue != "-"]
        start = result["query_start"]
        amino_acids = "".join(residue for _, _, residue in numbered)
        end = start + len(numbered)
        if not numbered or start < 0 or segment[start:end] != amino_acids:
            raise ValueError("ANARCII numbering does not align to the observed residues")
        positions = [f"{position}{insertion}" for position, insertion, _ in numbered]
        domains.append((offset + start, result["chain_type"], positions))
        pending.extend(((offset, segment[:start]), (offset + end, segment[end:])))
    return sorted(domains)


def _worker():
    sequences = json.load(sys.stdin)
    with redirect_stdout(sys.stderr):
        from anarcii import Anarcii

        engine = Anarcii(cpu=True, ncpu=1, mode="speed", batch_size=1)
        result = {sequence: _annotate_domains(engine, sequence) for sequence in sequences}
    json.dump(result, sys.stdout)


def _selection(model, ids):
    return f"%{model} and index " + "+".join(map(str, ids))


def _cartoon_colors(api, selection):
    values = {}
    api.iterate(selection, "values[index] = s.cartoon_color", space={"values": values})
    return values


def _cdr(position):
    position = int(re.match(r"\d+", position).group())
    return next((f"CDR{number}" for number, (low, high) in enumerate(_CDR_RANGES, 1) if low <= position <= high), "")


class AntibodyCartoon:
    def __init__(self):
        self.rows = []
        self._applied = []

    def clear(self, api):
        from .reference_track import molecule_identity

        existing = set(api.get_names("objects"))
        for model, identity, expected, before in self._applied:
            if model not in existing or molecule_identity(api, model) != identity:
                continue
            current = _cartoon_colors(api, _selection(model, before))
            restore = defaultdict(list)
            for index, old in before.items():
                if current.get(index) == expected:
                    # Object-level colors are returned as RGB; unsetting restores that inheritance.
                    restore[int(old) if isinstance(old, int) else None].append(index)
            for color, ids in restore.items():
                atom_selection = _selection(model, ids)
                if color is None:
                    api.unset("cartoon_color", atom_selection)
                else:
                    api.set("cartoon_color", color, atom_selection)
        self._applied.clear()
        self.rows.clear()

    def draw(self, api, chains, numbered):
        from .reference_track import molecule_identity

        self.clear(api)
        highlights = defaultdict(list)
        existing = set(api.get_names("objects"))
        for chain in chains:
            if chain.model not in existing or molecule_identity(api, chain.model) != chain.identity:
                continue
            for domain, (offset, kind, positions) in enumerate(numbered.get(chain.sequence, ()), 1):
                for (index, resn, resi), position in zip(chain.residues[offset:], positions):
                    cdr = _cdr(position)
                    self.rows.append((chain.model, chain.segment, chain.chain, resi, resn, str(domain), kind, position, cdr))
                    if cdr:
                        highlights[chain.model, cdr].append(index)
        for (model, cdr), ids in highlights.items():
            selection = f"(byres ({_selection(model, ids)})) and name CA"
            color = api.get_color_index(CDR_COLORS[cdr])
            self._applied.append((model, molecule_identity(api, model), color, _cartoon_colors(api, selection)))
            api.set("cartoon_color", color, selection)


if __name__ == "__main__":
    _worker()
