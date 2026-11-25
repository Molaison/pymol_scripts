"""Mount the SSH worker's POSIX paths on a local test directory."""

from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import posixpath
import sys
import time
from types import SimpleNamespace

from pymolplugins.remote import ssh_worker


REMOTE_ROOT = PurePosixPath("/remote input")
# NTFS normally folds case, so give the upper-case fixture a distinct backing file.
LOCAL_NAMES = {"A.pdb": "upper-A.pdb"} if os.name == "nt" else {}
REMOTE_NAMES = {local: remote for remote, local in LOCAL_NAMES.items()}


def run_worker(directory, delay=0):
    def local(path):
        relative = PurePosixPath(path).relative_to(REMOTE_ROOT)
        return directory.joinpath(*(LOCAL_NAMES.get(part, part) for part in relative.parts))

    @contextmanager
    def scandir(path):
        with os.scandir(local(path)) as entries:
            children = []
            for entry in entries:
                name = REMOTE_NAMES.get(entry.name, entry.name)
                children.append(SimpleNamespace(name=name, path=posixpath.join(path, name)))
            yield children

    # Keep POSIX names in requests, metadata and directory listings. Translate
    # paths only where the real worker accesses the fixture's filesystem.
    ssh_worker.os = SimpleNamespace(
        path=posixpath, fstat=os.fstat, R_OK=os.R_OK, scandir=scandir,
        stat=lambda path: os.stat(local(path)),
        access=lambda path, mode: os.access(local(path), mode),
    )
    ssh_worker.open = lambda path, *args, **kwargs: open(local(path), *args, **kwargs)
    time.sleep(delay)
    ssh_worker.main()


if __name__ == "__main__":
    run_worker(Path(sys.argv[1]), float(sys.argv[2]) if len(sys.argv) > 2 else 0)
