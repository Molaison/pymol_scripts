"""Build a clustering request and rank its groups by table scores."""

import ast
import ctypes
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from functools import lru_cache
from importlib.util import find_spec
import json
import math
import operator
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import tempfile
import threading

from pymolplugins.remote.ssh import SshClient, local_source_file, parse_ssh_path

from .filtering import _column_name, _quoted_columns


_ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.Div: operator.truediv}


def compile_cluster_score(expression, columns):
    """Compile column arithmetic. Missing or invalid numeric values sort last."""
    source = expression.strip()
    if not source:
        return None
    try:
        text, aliases = _quoted_columns(source)
        tree = ast.parse(text, mode="eval")
        evaluator = _score_node(tree.body, set(columns), aliases)
    except (SyntaxError, RecursionError, OverflowError) as error:
        raise ValueError("Enter a score column or a numerical expression using +, -, *, /.") from error

    def score(row):
        try:
            result = evaluator(row)
            return result if math.isfinite(result) else None
        except (ValueError, TypeError, ArithmeticError):
            return None

    return score


def _score_node(node, columns, aliases):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        value = float(node.value)
        return lambda row: value
    if isinstance(node, (ast.Name, ast.Attribute)):
        name = _column_name(node)
        column = aliases.get(name, name)
        if column not in columns:
            raise ValueError(f"Unknown score column {column!r}")
        return lambda row: float(row.get(column, ""))
    if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
        value = _score_node(node.operand, columns, aliases)
        sign = -1 if isinstance(node.op, ast.USub) else 1
        return lambda row: sign * value(row)
    if isinstance(node, ast.BinOp) and type(node.op) in _ARITHMETIC:
        left, right = (_score_node(child, columns, aliases) for child in (node.left, node.right))
        combine = _ARITHMETIC[type(node.op)]
        return lambda row: combine(left(row), right(row))
    raise ValueError("Use column names, numbers, parentheses and +, -, *, / in a score expression.")


@dataclass(frozen=True)
class RankedCluster:
    key: str
    label: str
    members: tuple
    score: float | None


def rank_clusters(entries, assignments, score, higher=True):
    """Retain the original IDs and rank ties by their source row order."""
    groups = defaultdict(list)
    values = {}
    for entry in entries:
        if entry.id in assignments:
            groups[assignments[entry.id]].append(entry)
            values[entry.id] = score(entry.metadata) if score else None

    def key(entry):
        value = values[entry.id]
        return (value is None, (0 if value is None else (-value if higher else value)), entry.ordinal)

    result = []
    for group, members in groups.items():
        rows = sorted(members, key=key)
        result.append((key(rows[0]), RankedCluster(group, rows[0].display_name,
                       tuple(entry.id for entry in rows), values[rows[0].id])))
    return [cluster for _, cluster in sorted(result, key=lambda row: row[0])]


def cluster_on_source(entries, options, *, remote=None, cancel_event):
    """Run the supplied subset without moving remote structures to the desktop."""
    need_file = options["method"] != "sequence" or not options["sequence_column"]
    records, skipped = [], {}
    with ExitStack() as opened:
        for entry in entries:
            _check_cancel(cancel_event)
            path = entry.resolved_path or entry.source_path
            record = {"id": entry.id, "path": ""}
            if options["sequence_column"]:
                record["sequence"] = entry.metadata.get(options["sequence_column"], "")
            if need_file:
                try:
                    if remote is not None:
                        location = parse_ssh_path(path)
                        if location.at(remote.path) != remote:
                            raise ValueError("Path is on a different SSH profile")
                        path = location.path
                    else:
                        path = opened.enter_context(local_source_file(path, cancel_event=cancel_event))
                except (OSError, ValueError, RuntimeError) as error:
                    _check_cancel(cancel_event)
                    skipped[entry.id] = str(error)
                    continue
                record["path"] = os.fspath(path)
            records.append(record)
        if not records:
            first_error = next(iter(skipped.values()), "No eligible rows")
            raise ValueError(first_error)
        request = {key: options[key] for key in ("method", "chain", "identity", "tm", "coverage", "threads")}
        request["records"] = records
        if remote is not None:
            result = SshClient(remote, timeout=3600).cluster(request, cancel_event=cancel_event)
        else:
            result = _run_local(request, need_file, cancel_event)
        result["skipped"].update(skipped)
        return result


def _check_cancel(cancel_event):
    if cancel_event.is_set():
        raise InterruptedError("Clustering cancelled.")


@lru_cache(maxsize=8)
def _wsl_mount(drive):
    return subprocess.check_output(["wsl.exe", "-e", "wslpath", "-u", drive + "/"],
        encoding="utf-8", timeout=15, creationflags=subprocess.CREATE_NO_WINDOW).strip()


def _wsl_path(path):
    native = PureWindowsPath(os.path.abspath(path))
    if len(native.drive) != 2 or not native.drive.endswith(":"):
        raise ValueError("Local WSL clustering needs files on a Windows drive. Use SSH compute for remote files.")
    return _wsl_mount(native.drive).rstrip("/") + "/" + "/".join(native.parts[1:])


def _run_local(request, need_file, cancel_event):
    from .cluster_engine import run_cluster

    tools = {"sequence": ("mmseqs",), "structure": ("foldseek",),
             "both": ("mmseqs", "foldseek")}[request["method"]]
    if os.name != "nt" or (all(shutil.which(tool) for tool in tools) and (not need_file or find_spec("gemmi"))):
        return run_cluster(request, lambda: _check_cancel(cancel_event))
    if not shutil.which("wsl.exe"):
        raise RuntimeError("Install the clustering tools and Gemmi, enable WSL, or run on the source SSH host.")
    with tempfile.TemporaryDirectory(prefix="pymol_cluster_input_") as staged:
        for i, record in enumerate(request["records"]):
            _check_cancel(cancel_event)
            if record["path"]:
                record["path"] = _wsl_input(record["path"], staged, i)
        return _run_wsl(request, cancel_event)


def _wsl_input(path, staged, index):
    source = Path(path)
    drive = source.absolute().drive
    if len(drive) != 2 or ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4:
        fallback = Path(staged) / (f"input_{index}" + "".join(source.suffixes[-2:]))
        if source.is_file():
            shutil.copyfile(source, fallback)
        source = fallback
    return _wsl_path(source)


def _run_wsl(request, cancel_event):
    launch = ('prefix="${XDG_DATA_HOME:-$HOME/.local/share}/pymolplugins/clustering"; '
              'export PATH="$prefix/mmseqs/bin:$prefix/foldseek/bin:$PATH"; '
              'if [ -x "$prefix/python/bin/python" ]; then exec "$prefix/python/bin/python" "$@"; fi; '
              'exec python3 "$@"')
    entry = (
        "import json, runpy, sys\n"
        "run_cluster = runpy.run_path(sys.argv[1])['run_cluster']\n"
        "channel = runpy.run_path(sys.argv[2])['check_cluster_channel']\n"
        "try:\n print(json.dumps(run_cluster(json.loads(sys.stdin.buffer.readline()), check_cancel=channel)))\n"
        "except Exception as error:\n sys.exit(str(error))\n"
    )
    engine = Path(__file__).with_name("cluster_engine.py")
    ssh_worker = engine.parents[1] / "remote" / "ssh_worker.py"
    command = ["wsl.exe", "-e", "sh", "-c", launch, "cluster", "-c", entry,
               _wsl_path(engine), _wsl_path(ssh_worker)]
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        body = json.dumps(request).encode("utf-8") + b"\n"
        writer = threading.Thread(target=_write_request, args=(process, body), daemon=True)
        writer.start()
        try:
            while process.poll() is None:
                if cancel_event.wait(0.15):
                    raise InterruptedError("Clustering cancelled.")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            writer.join()
            process.stdin.close()
        if process.returncode:
            stderr.seek(0)
            raise RuntimeError(stderr.read().decode("utf-8", "replace").strip()[-1200:] or "Local WSL clustering failed.")
        stdout.seek(0)
        return json.load(stdout)


def _write_request(process, body):
    try:
        process.stdin.write(body)
        process.stdin.flush()
    except OSError:
        pass
