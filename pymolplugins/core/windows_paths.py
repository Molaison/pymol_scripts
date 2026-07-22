"""Windows path helpers which remain importable outside PyMOL."""

import os
import sys
from typing import Callable, Optional


def windows_long_path(
    path: str,
    *,
    platform_name: Optional[str] = None,
    abspath: Callable[[str], str] = os.path.abspath,
    threshold: int = 240,
) -> str:
    r"""Return a valid extended-length Windows path when it is needed.

    Drive paths use ``\\?\C:\...`` while UNC paths must use the distinct
    ``\\?\UNC\server\share\...`` spelling.  Short paths are intentionally
    returned unchanged so PyMOL messages keep the user-facing path.
    """
    text = os.fspath(path) if path is not None else ""
    if (platform_name or sys.platform) != "win32" or not text:
        return text
    if text.startswith("\\\\?\\") or text.startswith("\\\\.\\"):
        return text

    absolute = abspath(text)
    if len(absolute) < int(threshold):
        return text
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute
