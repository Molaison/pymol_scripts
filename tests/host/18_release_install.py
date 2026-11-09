"""Install the published release without touching an existing PyMOL process.

Use bundled Windows Python with --stage install --version 0.18.0.
Run --stage verify after fetching the release tag to compare installed source.
The native updater owns checksum validation, replacement and rollback.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "output/18_release"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--stage", choices=("install", "verify"), required=True)
parser.add_argument("--version", required=True)
args = parser.parse_args()
OUTPUT.mkdir(parents=True, exist_ok=True)
startup = Path("D:/Softwares/Pymol3.1/Lib/site-packages/pmg_tk/startup").resolve()
installation = startup / "pymol_plugins"
backup = startup / ".pymol_plugins-backup"
report = {"status": "running", "stage": args.stage, "requested_version": args.version}


def pymol_processes():
    # Read process identity only; no command lines, window activation or messages.
    query = (
        "$items = @(Get-CimInstance Win32_Process | "
        "Where-Object ExecutablePath -Like '*Pymol*' | "
        "Select-Object ProcessId,Name,ExecutablePath,CreationDate); "
        "ConvertTo-Json -InputObject $items -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
        capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    rows = json.loads(result.stdout)
    assert isinstance(rows, list)
    return [row for row in rows if row["ProcessId"] != os.getpid()]


try:
    # Check exact absolute targets before the updater's recursive backup removal.
    for path in (installation, backup):
        assert path.resolve().parent == startup, path
        if path.exists():
            assert not os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, path
    updater_path = installation / "pymolplugins/host/updater.py"
    updater = runpy.run_path(str(updater_path))
    assert updater["installation_root"](str(updater_path), str(installation / "__init__.py")) == installation
    current = updater["_package_version"](installation / "pymolplugins/__init__.py")
    report.update(installation=str(installation), before_version=current)
    report["processes_before"] = pymol_processes()
    if args.stage == "install":
        release = updater["check_release"](current)
        assert release["version"] == args.version, release["version"]
        report["release"] = release
        if current != args.version:
            report["result"] = updater["install_release"](
                release, str(updater_path), str(installation / "__init__.py")
            )
        else:
            report["result"] = {"version": current, "already_installed": True, "restart_required": True}
    installed = updater["_package_version"](installation / "pymolplugins/__init__.py")
    assert installed == args.version
    report["installed_version"] = installed
    report["backup_version"] = (
        updater["_package_version"](backup / "pymolplugins/__init__.py") if backup.exists() else None
    )
    if args.stage == "verify":
        # Git's release blobs match Linux CI line endings, unlike a Windows checkout.
        report["release_files"] = {}
        paths = [
            "pymolplugins/__init__.py", "pymolplugins/navigation/commands.py",
            "pymolplugins/annotation/plugin.py", "pymolplugins/annotation/command_templates.py",
            "pymolplugins/annotation/ai_commands.py", "pymolplugins/annotation/ai_widgets.py",
        ]
        for relative in paths:
            released = subprocess.check_output(
                ["git", "show", f"v{args.version}:{relative}"], cwd=ROOT
            )
            actual = (installation / relative).read_bytes()
            assert actual == released, relative
            report["release_files"][relative] = hashlib.sha256(actual).hexdigest()
    report["processes_after"] = pymol_processes()
    for previous in report["processes_before"]:
        assert previous in report["processes_after"], previous
    report["status"] = "passed"
except Exception:
    report.update(status="failed", traceback=traceback.format_exc())
finally:
    path = OUTPUT / ("03_install.json" if args.stage == "install" else "04_installed_files.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True), flush=True)
sys.exit(0 if report["status"] == "passed" else 1)
