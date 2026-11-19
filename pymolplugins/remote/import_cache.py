"""Bounded local snapshots of recent SSH imports, revalidated against remote metadata."""

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from .ssh import SshClient, is_ssh_path, parse_ssh_path, ssh_state_root


MAX_SOURCES = 5
MAX_RECORD_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


def _path():
    return Path(ssh_state_root()) / "recent-imports-v1.sqlite3"


@contextmanager
def _database():
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=3)) as db, db:
        if db.execute("PRAGMA user_version").fetchone()[0] == 0:
            db.executescript("""
                PRAGMA auto_vacuum = FULL;
                CREATE TABLE IF NOT EXISTS snapshot (
                    source TEXT NOT NULL, kind TEXT NOT NULL, version TEXT NOT NULL,
                    payload BLOB NOT NULL, accessed INTEGER NOT NULL,
                    PRIMARY KEY (source, kind)
                );
                CREATE TABLE IF NOT EXISTS generation (id INTEGER PRIMARY KEY, epoch INTEGER NOT NULL);
                INSERT OR IGNORE INTO generation VALUES (1, 0);
                PRAGMA user_version = 1;
            """)
        yield db


def _version(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _read(source, kind, version):
    try:
        with _database() as db:
            epoch = db.execute("SELECT epoch FROM generation WHERE id = 1").fetchone()[0]
            row = db.execute("SELECT payload FROM snapshot WHERE source=? AND kind=? AND version=?",
                             (source, kind, version)).fetchone()
            if row:
                db.execute("UPDATE snapshot SET accessed=? WHERE source=? AND kind=? AND version=?",
                           (time.time_ns(), source, kind, version))
            return (row[0] if row else None), epoch
    except (OSError, sqlite3.Error) as exc:
        print(f"Remote import cache unavailable: {exc}")
        return None, None


def _write(source, kind, version, payload, epoch):
    if epoch is None or len(payload) > MAX_RECORD_BYTES:
        return
    try:
        with _database() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT epoch FROM generation WHERE id = 1").fetchone()[0] != epoch:
                return
            db.execute("INSERT OR REPLACE INTO snapshot VALUES (?, ?, ?, ?, ?)",
                       (source, kind, version, payload, time.time_ns()))
            recent = db.execute("""SELECT source, SUM(LENGTH(payload)) FROM snapshot
                                   GROUP BY source
                                   ORDER BY MAX(kind IN ('table', 'directory')) DESC, MAX(accessed) DESC""").fetchall()
            retained = size = 0
            for key, length in recent:
                if retained < MAX_SOURCES and size + length <= MAX_TOTAL_BYTES:
                    retained += 1
                    size += length
                else:
                    db.execute("DELETE FROM snapshot WHERE source=?", (key,))
    except (OSError, sqlite3.Error) as exc:
        print(f"Could not save remote import cache: {exc}")


def clear_remote_imports(source=None):
    """Clear cached copies and prevent older in-flight requests from republishing them."""
    if not _path().exists():
        return 0
    where, parameters = (" WHERE source=?", (parse_ssh_path(source).uri,)) if source else ("", ())
    with _database() as db:
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("SELECT COUNT(DISTINCT source) FROM snapshot" + where, parameters).fetchone()[0]
        db.execute("DELETE FROM snapshot" + where, parameters)
        db.execute("UPDATE generation SET epoch=epoch+1 WHERE id=1")
    return count


def _cached_json(source, kind, stamp, fetch):
    version = _version(stamp)
    payload, epoch = _read(source, kind, version)
    if payload is not None:
        try:
            return json.loads(payload)
        except ValueError as exc:
            print(f"Could not read cached remote {kind}: {exc}")
    result = fetch()
    _write(source, kind, version, json.dumps(result, ensure_ascii=True).encode("ascii"), epoch)
    return result


def _one_path(location, kind, read, *, options=(), cancel_event=None):
    client = SshClient(location)
    observed = client.stat(cancel_event=cancel_event)

    def fetch():
        value = read(client)
        if client.stat(cancel_event=cancel_event) != observed:
            raise RuntimeError(f"Remote {kind} changed during import: {location.path}")
        return value

    return _cached_json(location.uri, kind, (observed, options), fetch)


def recent_header(path, *, encoding, delimiter, cancel_event=None):
    location = parse_ssh_path(path)
    return _one_path(location, "table header", lambda client: client.read_header(
        encoding=encoding, delimiter=delimiter, cancel_event=cancel_event
    ), options=(encoding, delimiter), cancel_event=cancel_event)


def recent_directory(location, *, cancel_event=None):
    return _one_path(location, "directory", lambda client: client.list_directory(
        cancel_event=cancel_event
    ), cancel_event=cancel_event)


def recent_review_files(location, paths, *, cancel_event=None):
    client = SshClient(location)
    observed = client.stat_many(paths, cancel_event=cancel_event)

    def fetch():
        values = client.read_small_files(paths, cancel_event=cancel_event)
        signatures = [{key: item for key, item in value.items() if key != "content"} for value in values]
        if signatures != observed:
            raise RuntimeError(f"Remote review configuration changed during import: {location.path}")
        return values

    return _cached_json(location.uri, "review files", observed, fetch)


@contextmanager
def recent_table(path, *, cancel_event=None):
    """Yield an import-owned copy so eviction cannot affect a table being parsed."""
    if not is_ssh_path(path):
        yield path
        return
    location = parse_ssh_path(path)
    client = SshClient(location)
    observed = client.stat(cancel_event=cancel_event)
    if not observed["file"]:
        raise ValueError(f"Remote table is not a regular file: {location.path}")
    fingerprint = {key: value for key, value in observed.items() if key != "status"}
    version = _version(fingerprint)
    payload, epoch = _read(location.uri, "table", version)
    with tempfile.TemporaryDirectory(prefix="pymol_ssh_table_") as folder:
        destination = Path(folder) / ("table" + Path(location.path).suffix)
        if payload is not None:
            destination.write_bytes(payload)
            del payload
        else:
            downloaded = client.download(destination, cancel_event=cancel_event)
            if downloaded != fingerprint:
                raise RuntimeError(f"Remote table changed during download: {location.path}")
            if epoch is not None and downloaded["size"] <= MAX_RECORD_BYTES:
                _write(location.uri, "table", version, destination.read_bytes(), epoch)
        yield str(destination)
