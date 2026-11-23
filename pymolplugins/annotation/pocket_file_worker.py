"""Entry point for an isolated Pocket changes file reader and fitter."""

import os
import pickle
import sys
from pathlib import Path


def _serve(source, output):
    from pymol2 import PyMOL
    from pymolplugins.annotation.pocket_analysis import PocketAnalysis
    from pymolplugins.annotation.pocket_jobs import prepare_candidate, snapshot_file
    from pymolplugins.annotation.pocket_variants import PocketTally

    reference, sites, protein, ligand, radius, settings, protein_partner = pickle.load(source)
    tally = PocketTally(reference.protein, sites, reference.ligand if protein_partner else None)
    analysis = PocketAnalysis(reference, sites, protein_partner=protein_partner)
    with PyMOL() as engine:
        api = engine.cmd
        for key, value in settings.items():
            api.set(key, value)
        _send(output, (True, None))
        while True:
            try:
                entry_id, name, path = pickle.load(source)
            except EOFError:
                return
            try:
                snapshot = snapshot_file(api, path, protein, ligand, radius, "_sa_pocket_input",
                                         protein_partner=protein_partner)
                result = prepare_candidate(tally, analysis, entry_id, name, snapshot)
            except Exception as error:
                _send(output, (False, str(error) or type(error).__name__))
            else:
                _send(output, (True, result))


def _send(output, result):
    pickle.dump(result, output, protocol=pickle.HIGHEST_PROTOCOL)
    output.flush()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    # Reserve the original stdout pipe for results before loading PyMOL.
    with os.fdopen(os.dup(sys.stdout.fileno()), "wb") as protocol:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        try:
            _serve(sys.stdin.buffer, protocol)
        except Exception as error:
            _send(protocol, (False, str(error) or type(error).__name__))
