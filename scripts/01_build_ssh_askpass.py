"""Build the bundled Windows askpass bridge; end users do not need a compiler.

Run: python scripts/01_build_ssh_askpass.py [--gcc PATH]
"""

import argparse
from pathlib import Path
import shutil
import subprocess


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--gcc", default=shutil.which("gcc") or str(
    Path.home() / "scoop/apps/mingw/current/bin/gcc.exe"))
args = parser.parse_args()
directory = Path(__file__).resolve().parents[1] / "pymolplugins/remote/askpass"
subprocess.run([args.gcc, "-municode", "-O2", "-Wall", "-Wextra", "-Werror", "-static", "-s",
                str(directory / "ssh_askpass.c"), "-o", str(directory / "ssh_askpass.exe")], check=True)
print(directory / "ssh_askpass.exe")
