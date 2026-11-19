"""Interpret pasted paths and file URLs at source intake."""

import re
from urllib.parse import unquote, urlsplit


_PASTE_WRAPPERS = {'"': '"', "'": "'", "“": "”", "‘": "’", "<": ">", "`": "`"}
_DRIVE = re.compile(r"^[A-Za-z][:\|]$")


def clean_pasted_path(path):
    text = str(path).strip()
    while len(text) > 1 and _PASTE_WRAPPERS.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    return text


def is_file_url(path):
    return str(path).lower().startswith("file:")


def file_url_path(path):
    """Convert a file URL to a drive, UNC, or POSIX path. Decode URL escapes once."""
    if not is_file_url(path):
        return path
    parsed = urlsplit(path.replace("\\", "/"))
    authority, location = unquote(parsed.netloc), unquote(parsed.path)
    if _DRIVE.fullmatch(authority):
        location = authority[0] + ":" + (location or "/")
    elif authority and authority.casefold() != "localhost":
        if any(char in authority for char in "/\\:@") or not location.strip("/"):
            raise ValueError("The file link needs a server and share name.")
        location = "//" + authority + "/" + location.lstrip("/")
    elif re.match(r"^/?[A-Za-z][:\|](?:/|$)", location):
        location = location.removeprefix("/")
        location = location[0] + ":" + (location[2:] or "/")
    if not location or any(char in location for char in "\x00\r\n"):
        raise ValueError("The file link needs a valid file or folder path.")
    return location
