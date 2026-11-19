
"""Cluster one protein chain per candidate with MMseqs2 or Foldseek."""
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile


def _tool(name):
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"{name} is missing on the execution host. Install it and add it to PATH.")
    version = subprocess.run([executable, "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, check=True, timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return executable, version.stdout.strip()


def _versioned_tools(method):
    names = {"sequence": ("mmseqs",), "structure": ("foldseek",), "both": ("mmseqs", "foldseek")}
    return {name: _tool(name) for name in names[method]}


def _read_model(path, gemmi):
    path = Path(path).expanduser()
    # Keep the compression and format explicit; remote paths may use mixed-case extensions.
    suffixes = path.name.lower().split(".")
    if suffixes[-1] == "gz":
        with gzip.open(path, "rb") as file:
            data = file.read()
        if suffixes[-2] in {"cif", "mmcif"}:
            return gemmi.make_structure_from_block(gemmi.cif.read_string(data).sole_block())
        return gemmi.read_pdb_string(data)
    return gemmi.read_structure(str(path))


def _protein_chain(path, selected, gemmi):
    structure = _read_model(path, gemmi)
    if not len(structure):
        raise ValueError("No coordinate model")
    chains = {}
    for chain in structure[0]:
        residues = []
        for residue in chain:
            info = gemmi.find_tabulated_residue(residue.name)
            if info.is_amino_acid() and residue.find_atom("CA", "*"):
                letter = info.one_letter_code.upper()
                residues.append((residue, letter if re.fullmatch("[A-Z]", letter) else "X"))
        if residues:
            chains.setdefault(chain.name, []).extend(residues)
    if selected:
        if selected not in chains:
            raise ValueError(f"Protein chain {selected!r} absent; available: {', '.join(chains) or 'none'}")
        return chains[selected]
    if len(chains) != 1:
        raise ValueError("Select a protein chain; available: " + (", ".join(chains) or "none"))
    return next(iter(chains.values()))


def _write_chain(path, residues, gemmi):
    chain = gemmi.Chain("A")
    seen, letters = set(), []
    for residue, letter in residues:
        key = str(residue.seqid)
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > 9999:
            raise ValueError("The selected chain exceeds 9,999 residues")
        normalized = residue.clone()
        normalized.seqid = gemmi.SeqId(len(seen), " ")
        normalized.het_flag = "A"
        chain.add_residue(normalized)
        letters.append(letter)
    structure = gemmi.Structure()
    model = gemmi.Model(1)
    model.add_chain(chain)
    structure.add_model(model)
    structure.remove_alternative_conformations()
    structure.write_pdb(str(path))
    return "".join(letters)


def _sequence(record):
    value = record.get("sequence")
    sequence = "".join(str(value or "").split()).upper().rstrip("*")
    if not re.fullmatch("[A-Z]+", sequence):
        raise ValueError("Sequence must contain amino-acid letters")
    return sequence


def _prepare(request, root, check_cancel):
    structures = request["method"] != "sequence"
    sequences = request["method"] != "structure"
    records = request["records"]
    if structures or any("sequence" not in record for record in records):
        try:
            import gemmi
        except ImportError as error:
            raise RuntimeError("Install Gemmi in Python 3 on the execution host (python3 -m pip install gemmi).") from error
    models = root / "models"
    models.mkdir()
    names, skipped = {}, {}
    with (root / "input.fasta").open("w", encoding="ascii") as fasta:
        for index, record in enumerate(records):
            check_cancel()
            row_id = record["id"]
            name = f"m{index:08d}"
            path = models / f"{name}.pdb"
            try:
                residues = _protein_chain(record["path"], request["chain"], gemmi) if structures or "sequence" not in record else []
                if sequences and "sequence" in record:
                    sequence = _sequence(record)
                else:
                    sequence = ""
                if structures:
                    derived = _write_chain(path, residues, gemmi)
                else:
                    derived = "".join(letter for _, letter in residues)
                if sequences:
                    fasta.write(f">{name}\n{sequence or derived}\n")
            except (OSError, RuntimeError, ValueError) as error:
                if path.is_file():
                    path.unlink()
                skipped[row_id] = str(error)[:240]
            else:
                names[name] = row_id
    if not names:
        reason = next(iter(skipped.values()), "the filter has no candidates")
        raise ValueError("No eligible candidates: " + reason)
    return names, skipped


def _stop(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/pid", str(process.pid), "/t", "/f"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()


def _run(command, root, check_cancel):
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, **flags)
        try:
            while True:
                check_cancel()
                try:
                    code = process.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            if process.poll() is None:
                _stop(process)
            raise
        if code:
            log.seek(max(log.tell() - 4096, 0))
            detail = log.read().decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(f"{Path(command[0]).name} failed (exit {code}): " + " ".join(detail[-5:])[-650:])


def _rows(path, names):
    def normalize(value):
        name = Path(value).name.split(".", 1)[0].split("_", 1)[0]
        if name not in names:
            raise ValueError(f"Clustering engine returned an unknown member {value!r}")
        return name

    groups = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            raw_rep, raw_member = line.rstrip("\r\n").split("\t")
            representative, member = normalize(raw_rep), normalize(raw_member)
            if groups.setdefault(member, representative) != representative:
                raise ValueError("Clustering engine returned conflicting assignments")
    if groups.keys() != names.keys():
        raise ValueError("Clustering engine did not return an assignment for every eligible candidate")
    return groups


def run_cluster(request, check_cancel=lambda: None):
    """Return stable row assignments, skipped input reasons, and engine versions."""
    tools = _versioned_tools(request["method"])
    with tempfile.TemporaryDirectory(prefix="pymol_clustering_") as work:
        root = Path(work)
        names, skipped = _prepare(request, root, check_cancel)
        coverage = ["-c", str(request["coverage"]), "--cov-mode", "0", "--threads", str(request["threads"])]
        partitions = []
        if "mmseqs" in tools:
            _run([tools["mmseqs"][0], "easy-cluster", "input.fasta", "seq", "seq_tmp", "--dbtype", "1",
                  "--alignment-mode", "3", "--min-seq-id", str(request["identity"]), *coverage], root, check_cancel)
            partitions.append(_rows(root / "seq_cluster.tsv", names))
        if "foldseek" in tools:
            _run([tools["foldseek"][0], "easy-cluster", "models", "struct", "struct_tmp",
                  "--alignment-type", "1", "--tmscore-threshold", str(request["tm"]),
                  "--tmscore-threshold-mode", "1", *coverage], root, check_cancel)
            partitions.append(_rows(root / "struct_cluster.tsv", names))
        assignments = {row_id: "/".join(partition[name] for partition in partitions)
                       for name, row_id in names.items()}
        return {"groups": assignments, "skipped": skipped, "versions": {key: value[1] for key, value in tools.items()}}


if __name__ == "__main__":
    json.dump(run_cluster(json.load(sys.stdin)), sys.stdout)
