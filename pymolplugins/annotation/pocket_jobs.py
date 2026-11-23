"""Prepare candidate pocket results away from the active PyMOL scene."""

import os
import pickle
import subprocess
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor
from importlib.util import find_spec
from pathlib import Path
from threading import Lock, Thread, local

from pymol import setting

from .pocket_analysis import capture_snapshot

WORKERS = min(4, os.cpu_count() or 1)
# Process startup pays off once several files can share the workers.
MIN_PARALLEL_FILES = 5
_PYTHON = Path(sys.executable)
if _PYTHON.name.casefold() == "pythonw.exe":
    _PYTHON = _PYTHON.with_name("python.exe")
FILE_WORKERS_AVAILABLE = (_PYTHON.name.casefold().startswith("python") and _PYTHON.is_file()
                          and WORKERS > 1 and find_spec("pymol2") is not None)
_READER_SETTINGS = (
    "auto_discrete", "cif_use_auth", "connect_cutoff", "connect_mode", "ignore_pdb_segi",
    "pdb_hetatm_sort", "pdb_ignore_conect", "retain_order",
)


def new_executor():
    return ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="pymol-pocket")


def reader_settings(api):
    available = setting.get_name_list()
    return {key: api.get(key) for key in _READER_SETTINGS if key in available}


def snapshot_file(api, path, protein, ligand, radius, name, *, protein_partner=False):
    try:
        api.load(path, name, quiet=1, zoom=0)
        api.disable(name)
        return capture_snapshot(api, f"%{name} and ({protein})", f"%{name} and ({ligand})", radius,
                                protein_partner=protein_partner)
    finally:
        api.delete(name)


def prepare_snapshot(request, candidate, snapshot):
    if request.cancel.is_set():
        raise CancelledError()
    return prepare_candidate(request.tally, request.analysis, candidate.id, candidate.display_name, snapshot)


def prepare_candidate(tally, analysis, entry_id, name, snapshot):
    alignment, observations = tally.prepare(snapshot.protein, snapshot.ligand if analysis.protein_partner else None)
    contribution = analysis.prepare(entry_id, name, snapshot, *alignment)
    return observations, contribution


class WorkerStartupError(RuntimeError):
    pass


class FileWorkers:
    """Keep one isolated process per analysis thread for the file batch."""

    def __init__(self):
        self._lock = Lock()
        self._current = local()
        self._children = set()
        self._closed = False

    def prepare(self, request, candidate, path):
        if request.cancel.is_set():
            raise CancelledError()
        process = getattr(self._current, "process", None)
        if process is None or process.poll() is not None:
            try:
                process = self._start(request)
            except (OSError, ValueError, RuntimeError) as error:
                if request.cancel.is_set():
                    raise CancelledError() from error
                raise WorkerStartupError(str(error)) from error
            self._current.process = process
        try:
            result = _exchange(process, (candidate.id, candidate.display_name, path))
        except (ValueError, RuntimeError) as error:
            if request.cancel.is_set():
                raise CancelledError() from error
            raise
        if request.cancel.is_set():
            raise CancelledError()
        return result

    def _start(self, request):
        with self._lock:
            if self._closed:
                raise CancelledError()
            worker = Path(__file__).with_name("pocket_file_worker.py")
            process = subprocess.Popen([str(_PYTHON), "-I", "-u", str(worker)], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._children.add(process)
        try:
            _exchange(process, (request.reference, request.tally.sites, request.protein, request.ligand,
                                request.radius, request.reader_settings, request.protein_partner))
        except Exception:
            with self._lock:
                self._children.discard(process)
            if process.poll() is None:
                process.terminate()
            _reap((process,))
            raise
        return process

    def close(self, *, cancel=True):
        with self._lock:
            self._closed = True
            children = tuple(self._children)
            self._children.clear()
        for process in children:
            if not cancel:
                process.stdin.close()
            elif process.poll() is None:
                process.terminate()
        if children:
            Thread(target=_reap, args=(children,), daemon=True).start()


def _exchange(process, value):
    try:
        pickle.dump(value, process.stdin, protocol=pickle.HIGHEST_PROTOCOL)
        process.stdin.flush()
        success, result = pickle.load(process.stdout)
    except (EOFError, OSError, pickle.PickleError) as error:
        raise RuntimeError("Pocket changes file worker exited before returning a result.") from error
    if not success:
        raise ValueError(result)
    return result


def _reap(children):
    for process in children:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()
