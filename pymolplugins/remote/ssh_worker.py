"""Read-only operations sent to the selected SSH host's Python 3 interpreter."""

import base64
import csv
import io
import json
import os
import stat
import struct
import sys
import traceback
import zlib


def metadata(path, value):
    return {"path": path, "size": value.st_size, "mtime_ns": value.st_mtime_ns,
            "ctime_ns": value.st_ctime_ns, "device": value.st_dev,
            "inode": value.st_ino, "directory": stat.S_ISDIR(value.st_mode),
            "file": stat.S_ISREG(value.st_mode)}


def inspect_path(path):
    path = os.path.abspath(os.path.expanduser(path))
    try:
        value = metadata(path, os.stat(path))
        if value["file"] and not os.access(path, os.R_OK):
            return dict(value, status="unreadable", error="Remote file is not readable: " + path)
        return dict(value, status="available")
    except FileNotFoundError as exc:
        return {"path": path, "status": "missing", "error": str(exc)}
    except PermissionError as exc:
        return {"path": path, "status": "unreadable", "error": str(exc)}


def send(value):
    sys.stdout.buffer.write(json.dumps(value, ensure_ascii=True).encode("ascii") + b"\n")
    sys.stdout.buffer.flush()


def reply(value):
    send({"ok": True, "result": value, "stream": False})


def failure(exc):
    return {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}


def read_file(path, maximum, compression="none"):
    path = os.path.abspath(os.path.expanduser(path))
    with open(path, "rb") as handle:
        before = metadata(path, os.fstat(handle.fileno()))
        if not before["file"]:
            raise ValueError("Not a regular file: " + path)
        if before["size"] > maximum:
            raise ValueError("File exceeds the transfer limit: " + path)
        send({"ok": True, "result": before, "stream": True, "compression": compression})
        try:
            total = 0
            while True:
                data = handle.read(256 * 1024)
                if not data:
                    break
                total += len(data)
                if total > maximum:
                    raise ValueError("File grew beyond the transfer limit: " + path)
                if compression == "zlib":
                    data = zlib.compress(data, level=1)
                sys.stdout.buffer.write(struct.pack("!I", len(data)))
                sys.stdout.buffer.write(data)
            if metadata(path, os.fstat(handle.fileno())) != before or total != before["size"]:
                raise RuntimeError("File changed during download: " + path)
            outcome = {"ok": True}
        except Exception as exc:
            outcome = failure(exc)
        sys.stdout.buffer.write(struct.pack("!I", 0))
        send(outcome)


def small_files(paths, maximum):
    result = []
    for path in paths:
        before = inspect_path(path)
        if before["status"] == "missing":
            result.append(before)
            continue
        if before["status"] != "available":
            raise OSError(before["error"])
        if not before["file"] or before["size"] > maximum:
            raise ValueError("Not a bounded regular file: " + path)
        with open(before["path"], "rb") as handle:
            data = handle.read(maximum + 1)
        if len(data) > maximum or inspect_path(path) != before:
            raise RuntimeError("File changed during read: " + path)
        result.append(dict(before, content=base64.b64encode(data).decode("ascii")))
    return result


def list_directory(path, maximum):
    path = os.path.abspath(os.path.expanduser(path))
    entries = []
    with os.scandir(path) as iterator:
        for entry in iterator:
            if len(entries) >= maximum:
                raise ValueError("Directory exceeds the entry limit: " + path)
            value = inspect_path(entry.path)
            entries.append(dict(value, name=entry.name))
    entries.sort(key=lambda value: (not value.get("directory", False), value["name"]))
    return {"path": path, "entries": entries}


def table_header(path, encoding, delimiter):
    """Read a sample and the first record, without transferring table rows."""
    path = os.path.abspath(os.path.expanduser(path))
    with open(path, encoding=encoding, newline="") as handle:
        before = metadata(path, os.fstat(handle.fileno()))
        if delimiter == "auto":
            sample = handle.read(65536)
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
            except csv.Error:
                sizes = []
                for candidate in (",", "\t", ";", "|"):
                    try:
                        header = next(csv.reader(io.StringIO(sample), delimiter=candidate, strict=True), [])
                    except csv.Error:
                        header = []
                    sizes.append((len(header), candidate))
                delimiter = max(sizes, key=lambda value: value[0])[1]
            handle.seek(0)
        columns = next(csv.reader(handle, delimiter=delimiter, strict=True), [])
        if metadata(path, os.fstat(handle.fileno())) != before:
            raise RuntimeError("Table changed while reading columns: " + path)
    return {"columns": columns, "delimiter": delimiter, "encoding": encoding}


def dispatch(request):
    operation = request["operation"]
    if operation == "read":
        read_file(request["path"], request["maximum"], request.get("compression", "none"))
    elif operation == "stat":
        reply([inspect_path(path) for path in request["paths"]])
    elif operation == "small_files":
        reply(small_files(request["paths"], request["maximum"]))
    elif operation == "list":
        reply(list_directory(request["path"], request["maximum"]))
    elif operation == "header":
        reply(table_header(request["path"], request["encoding"], request["delimiter"]))
    else:
        raise ValueError("Unknown SSH read operation: " + str(operation))


def main():
    reply({"ready": True})
    while True:
        line = sys.stdin.buffer.readline(4 * 1024 * 1024 + 1)
        if not line:
            return
        try:
            if len(line) > 4 * 1024 * 1024:
                raise ValueError("SSH request exceeds the metadata limit")
            dispatch(json.loads(line))
        except Exception as exc:
            send(failure(exc))


if __name__ == "__main__":
    main()
