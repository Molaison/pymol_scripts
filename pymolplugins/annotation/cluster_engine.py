
"""Cluster one protein chain or a whole protein complex per candidate."""
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
    executable = os.environ.get("PYMOLPLUGINS_CLUSTER_" + name.upper()) or shutil.which(name)
    if not executable:
        raise RuntimeError(f"{name} is missing on the execution host. Install it and add it to PATH.")
    version = subprocess.run([executable, "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, check=True, timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return executable, version.stdout.strip()


def required_tools(method):
    return {"sequence": ("mmseqs",), "structure": ("foldseek",), "both": ("mmseqs", "foldseek"),
            "complex": ("foldseek",)}[method]


FOLDSEEK_COMPLEX_HELP_TOKENS = (
    "--multimer-tm-threshold", "--chain-tm-threshold", "--interface-lddt-threshold",
    "--min-aligned-chains", "--min-assigned-chains-ratio", "--multimer-report-mode")


def _validate_foldseek_complex(executable):
    """Require the native multimer command and its clustering options."""
    result = subprocess.run([executable, "easy-multimercluster", "-h"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    detail = result.stdout + result.stderr
    missing = [option for option in FOLDSEEK_COMPLEX_HELP_TOKENS if option not in detail]
    if missing:
        raise RuntimeError("Foldseek does not provide the required easy-multimercluster interface: "
                           + ", ".join(missing))


def _versioned_tools(method):
    tools = {name: _tool(name) for name in required_tools(method)}
    if method == "complex":
        _validate_foldseek_complex(tools["foldseek"][0])
    return tools


def check_dependencies(method, need_file):
    available, missing = {}, []
    for name in required_tools(method):
        try:
            executable, version = _tool(name)
            if name == "foldseek" and method == "complex":
                _validate_foldseek_complex(executable)
            available[name] = version
        except (OSError, RuntimeError, subprocess.SubprocessError):
            missing.append(name)
    if need_file:
        try:
            import gemmi

            available["gemmi"] = gemmi.__version__
        except (ImportError, OSError):
            missing.append("gemmi")
    return {"ready": not missing, "available": available, "missing": missing,
            "environment": sys.executable,
            "paths": {name: os.environ.get("PYMOLPLUGINS_CLUSTER_" + name.upper()) or shutil.which(name)
                      for name in available if name != "gemmi"}}


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
    if os.name == "nt" and str(path).startswith(chr(92) * 2 + "?" + chr(92)):
        data = path.read_bytes()
        if suffixes[-1] in {"cif", "mmcif"}:
            return gemmi.make_structure_from_block(gemmi.cif.read_string(data).sole_block())
        return gemmi.read_pdb_string(data)
    return gemmi.read_structure(str(path))


def _protein_chains(path, gemmi):
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
    return chains


def _protein_chain(path, selected, gemmi):
    chains = _protein_chains(path, gemmi)
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


def _write_complex(path, chains, gemmi):
    """Copy every protein chain while normalizing residue indices, not coordinates."""
    if len(chains) < 2:
        raise ValueError("Protein complex clustering needs at least two protein chains; available: "
                         + (", ".join(chains) or "none"))
    preserved = {name for name in chains if re.fullmatch("[A-Za-z0-9]", name)}
    used = set(preserved)
    next_generated = 0
    structure = gemmi.Structure()
    model = gemmi.Model(1)
    for source_name, residues in chains.items():
        chain_name = source_name
        if source_name not in preserved:
            while f"c{next_generated:03d}" in used:
                next_generated += 1
            chain_name = f"c{next_generated:03d}"
            next_generated += 1
            used.add(chain_name)
        chain = gemmi.Chain(chain_name)
        seen = set()
        for residue, _letter in residues:
            key = str(residue.seqid)
            if key in seen:
                continue
            seen.add(key)
            normalized = residue.clone()
            normalized.seqid = gemmi.SeqId(len(seen), " ")
            normalized.het_flag = "A"
            chain.add_residue(normalized)
        if seen:
            model.add_chain(chain)
    structure.add_model(model)
    structure.remove_alternative_conformations()
    structure.make_mmcif_document().write_file(str(path))


def _sequence(record):
    value = record.get("sequence")
    sequence = "".join(str(value or "").split()).upper().rstrip("*")
    if not re.fullmatch("[A-Z]+", sequence):
        raise ValueError("Sequence must contain amino-acid letters")
    return sequence


def _prepare(request, root, check_cancel):
    method = request["method"]
    structures = method in {"structure", "both", "complex"}
    sequences = method in {"sequence", "both"}
    multimer = method == "complex"
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
            path = models / (f"{name}.cif" if multimer else f"{name}.pdb")
            try:
                if multimer:
                    chains = _protein_chains(record["path"], gemmi)
                    residues = []
                else:
                    chains = None
                    residues = (_protein_chain(record["path"], request["chain"], gemmi)
                                if structures or "sequence" not in record else [])
                if sequences and "sequence" in record:
                    sequence = _sequence(record)
                else:
                    sequence = ""
                if multimer:
                    _write_complex(path, chains, gemmi)
                    derived = ""
                elif structures:
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
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                       creationflags=subprocess.CREATE_NO_WINDOW)
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


def _cluster_sequences(executable, request, root, coverage, check_cancel):
    options = ["--alignment-mode", "3", "--min-seq-id", str(request["identity"]), *coverage]
    if os.name == "nt":
        # easy-cluster also creates representative FASTA files. The Windows
        # build can crash in that step after the cluster assignments are ready.
        _run([executable, "createdb", "input.fasta", "seqdb", "--dbtype", "1"], root, check_cancel)
        _run([executable, "cluster", "seqdb", "seqclu", "seq_tmp", *options], root, check_cancel)
        _run([executable, "createtsv", "seqdb", "seqdb", "seqclu", "seq_cluster.tsv"], root, check_cancel)
    else:
        _run([executable, "easy-cluster", "input.fasta", "seq", "seq_tmp", "--dbtype", "1", *options],
             root, check_cancel)


def run_cluster(request, check_cancel=lambda: None):
    """Return stable row assignments, skipped input reasons, and engine versions."""
    method = request["method"]
    tools = _versioned_tools(method)
    with tempfile.TemporaryDirectory(prefix="pymol_clustering_") as work:
        root = Path(work)
        names, skipped = _prepare(request, root, check_cancel)
        coverage = ["-c", str(request["coverage"]), "--cov-mode", "0", "--threads", str(request["threads"])]
        partitions = []
        if "mmseqs" in tools:
            _cluster_sequences(tools["mmseqs"][0], request, root, coverage, check_cancel)
            partitions.append(_rows(root / "seq_cluster.tsv", names))
        if "foldseek" in tools:
            foldseek = tools["foldseek"][0]
            if method == "complex":
                _run([foldseek, "easy-multimercluster", "models", "complex", "complex_tmp",
                      "--alignment-type", "2",
                      "--multimer-tm-threshold", str(request["tm"]),
                      "--chain-tm-threshold", "0", "--interface-lddt-threshold", "0",
                      "--min-aligned-chains", "2", "--min-assigned-chains-ratio", "1.0",
                      "--multimer-report-mode", "0", *coverage], root, check_cancel)
                partitions.append(_rows(root / "complex_cluster.tsv", names))
            else:
                _run([foldseek, "easy-cluster", "models", "struct", "struct_tmp",
                      "--alignment-type", "1", "--tmscore-threshold", str(request["tm"]),
                      "--tmscore-threshold-mode", "1", *coverage], root, check_cancel)
                partitions.append(_rows(root / "struct_cluster.tsv", names))
        assignments = {row_id: "/".join(partition[name] for partition in partitions)
                       for name, row_id in names.items()}
        return {"groups": assignments, "skipped": skipped, "versions": {key: value[1] for key, value in tools.items()}}


if __name__ == "__main__":
    json.dump(run_cluster(json.load(sys.stdin)), sys.stdout)
