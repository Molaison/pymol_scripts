"""Build a clustering request and rank its groups by table scores."""

import ast
import ctypes
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from functools import lru_cache
import json
import math
import operator
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import tempfile
import threading

from pymolplugins.core.windows_paths import windows_long_path
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


def cluster_on_source(entries, options, *, remote=None, backend="auto", cancel_event):
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
            result = _run_local(request, need_file, cancel_event, backend=backend)
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


def cluster_dependencies(method, need_file, *, remote, backend, install, cancel_event):
    if remote is not None:
        return SshClient(remote, timeout=1800).cluster_dependencies(
            method, need_file=need_file, install=install, cancel_event=cancel_event)
    from .cluster_engine import check_dependencies

    request = {"method": method, "need_file": need_file, "check_dependencies": True}
    if backend == "wsl":
        return _run_wsl(request, cancel_event)
    if os.name != "nt":
        return check_dependencies(method, need_file)
    result = _run_windows(request, cancel_event)
    if not install or result["ready"]:
        return result
    from .cluster_windows import install_windows

    install_windows(result["missing"], lambda: _check_cancel(cancel_event))
    return _run_windows(request, cancel_event)


def _run_local(request, need_file, cancel_event, *, backend="auto"):
    from .cluster_engine import run_cluster

    if os.name != "nt":
        return run_cluster(request, lambda: _check_cancel(cancel_event))
    if backend != "wsl":
        status = cluster_dependencies(request["method"], need_file, remote=None, backend="windows",
                                      install=False, cancel_event=cancel_event)
        if status["ready"]:
            for record in request["records"]:
                if record["path"]:
                    record["path"] = windows_long_path(record["path"])
            return _run_windows(request, cancel_event)
        if backend == "windows" or not shutil.which("wsl.exe"):
            missing = ", ".join(status["missing"])
            raise RuntimeError(f"Missing Windows dependencies: {missing}. Use Install dependencies, WSL, or SSH.")
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
        if os.path.isfile(windows_long_path(source)):
            shutil.copyfile(windows_long_path(source), fallback)
        source = fallback
    return _wsl_path(source)


def _run_windows(request, cancel_event):
    from .cluster_windows import console_python, native_paths, tools_root
    from pymolplugins.remote.cluster_environment import discover

    need_file = request.get("need_file", any(row.get("path") for row in request.get("records", ())))
    executable, env = discover(request["method"], need_file, private=tools_root(), default_python=console_python())
    _, python = native_paths()
    engine = Path(__file__).with_name("cluster_engine.py")
    command = [executable, "-I", "-c", _CLUSTER_ENTRY, str(engine), "", str(python)]
    return _run_cluster_process(command, request, cancel_event, env=env)


_CLUSTER_ENTRY = """
import json, runpy, sys
if len(sys.argv) > 3:
    sys.path.insert(0, sys.argv[3])
engine = runpy.run_path(sys.argv[1])
channel = runpy.run_path(sys.argv[2])['check_cluster_channel'] if sys.argv[2] else lambda: None
request = json.loads(sys.stdin.buffer.readline())
try:
    if request.get('check_dependencies'):
        result = engine['check_dependencies'](request['method'], request['need_file'])
    else:
        result = engine['run_cluster'](request, check_cancel=channel)
    print(json.dumps(result))
except Exception as error:
    sys.exit(str(error))
"""


def _run_wsl(request, cancel_event):
    launch = ('prefix="${XDG_DATA_HOME:-$HOME/.local/share}/pymolplugins/clustering"; '
              'export PATH="$prefix/mmseqs/bin:$prefix/foldseek/bin:$PATH"; '
              'if [ -x "$prefix/python/bin/python" ]; then exec "$prefix/python/bin/python" "$@"; fi; '
              'exec python3 "$@"')
    engine = Path(__file__).with_name("cluster_engine.py")
    worker = engine.parents[1] / "remote" / "ssh_worker.py"
    command = ["wsl.exe", "-e", "sh", "-c", launch, "cluster", "-c", _CLUSTER_ENTRY,
               _wsl_path(engine), _wsl_path(worker)]
    return _run_cluster_process(command, request, cancel_event)


def _run_cluster_process(command, request, cancel_event, *, env=None):
    from .cluster_engine import _stop

    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                   creationflags=subprocess.CREATE_NO_WINDOW, env=env)
        body = json.dumps(request).encode("utf-8") + b"\n"
        writer = threading.Thread(target=_write_request, args=(process, body), daemon=True)
        writer.start()
        try:
            while process.poll() is None:
                if cancel_event.wait(0.15):
                    raise InterruptedError("Clustering cancelled.")
        finally:
            if process.poll() is None:
                _stop(process)
            process.wait()
            writer.join()
            process.stdin.close()
        if process.returncode:
            stderr.seek(0)
            raise RuntimeError(stderr.read().decode("utf-8", "replace").strip()[-1200:] or "Local clustering process failed.")
        stdout.seek(0)
        return json.load(stdout)


def _write_request(process, body):
    try:
        process.stdin.write(body)
        process.stdin.flush()
    except OSError:
        pass
