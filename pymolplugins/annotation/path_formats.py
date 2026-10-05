"""Explicit path formats and ordered, declarative CSV path rewrites."""

import ntpath
import re


PATH_FORMATS = ("custom", "windows", "posix", "wsl")
REWRITE_KINDS = ("strip_prefix", "prepend", "regex")


def validate_format(value):
    """Return a supported format name; raise ValueError for other values."""
    if value not in PATH_FORMATS:
        raise ValueError("path format must be custom, windows, posix or wsl")
    return value


def validate_distribution(value):
    """Return an explicit WSL distribution name or the empty unset value."""
    if not isinstance(value, str) or (value and not re.fullmatch(r"[\w.-]+", value)):
        raise ValueError("WSL distribution must be a name, for example Debian")
    return value


def validate_rewrites(values):
    """Return complete ordered rewrite dictionaries after validating inputs."""
    if not isinstance(values, (list, tuple)) or len(values) > 100:
        raise ValueError("rewrites must be an array with at most 100 rules")
    result = []
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) - {"kind", "pattern", "replacement", "enabled"}:
            raise ValueError("rewrite %d has unknown fields or is not an object" % (index + 1))
        kind = value.get("kind")
        pattern, replacement = value.get("pattern", ""), value.get("replacement", "")
        enabled = value.get("enabled", True)
        if kind not in REWRITE_KINDS or type(enabled) is not bool:
            raise ValueError("rewrite %d has an invalid kind or enabled flag" % (index + 1))
        for text in (pattern, replacement):
            if not isinstance(text, str) or len(text) > 4096 or any(c in text for c in "\x00\r\n"):
                raise ValueError("rewrite %d contains invalid text" % (index + 1))
        if kind != "prepend" and not pattern:
            raise ValueError("rewrite %d needs a pattern" % (index + 1))
        if kind == "prepend" and (pattern or not replacement):
            raise ValueError("prepend needs replacement text and an empty pattern")
        if kind == "strip_prefix" and replacement:
            raise ValueError("strip_prefix needs an empty replacement")
        if kind == "regex":
            try:
                re.compile(pattern).sub(replacement, "")
            except (re.error, IndexError) as exc:
                raise ValueError("rewrite %d: %s" % (index + 1, exc)) from exc
        result.append(dict(kind=kind, pattern=pattern, replacement=replacement, enabled=enabled))
    return result


def rewrite_path(path, rules):
    """Apply validated rewrite dictionaries to a path and return its new text."""
    for rule in rules:
        if not rule["enabled"]:
            continue
        kind, pattern, replacement = rule["kind"], rule["pattern"], rule["replacement"]
        if kind == "strip_prefix" and path.startswith(pattern):
            path = path[len(pattern):]
        elif kind == "prepend":
            path = replacement + path
        elif kind == "regex":
            path = re.sub(pattern, replacement, path, count=1)
    return path


def convert_path(path, source_format, target_format, distribution=""):
    """Convert only formats whose root has an unambiguous representation."""
    windows = bool(re.match(r"^[A-Za-z]:[\\/]", path)) or path.startswith(("\\\\", "//"))
    if target_format == "custom":
        return path
    if target_format == "windows":
        if windows:
            return path
        if source_format == "wsl":
            drive = re.match(r"^/mnt/([A-Za-z])(?:/|$)(.*)", path)
            if drive:
                return drive[1].upper() + ":\\" + drive[2].replace("/", "\\")
            if path.startswith("/"):
                if not distribution:
                    raise ValueError("Choose a WSL distribution to open this Linux path from Windows")
                return "\\\\wsl.localhost\\" + distribution + path.replace("/", "\\")
        raise ValueError("A root mapping is required to open this POSIX path on Windows")
    if not windows:
        return path
    if target_format == "wsl":
        drive, tail = ntpath.splitdrive(path.replace("/", "\\"))
        if re.fullmatch(r"[A-Za-z]:", drive):
            return "/mnt/" + drive[0].lower() + tail.replace("\\", "/")
        match = re.match(r"^\\\\wsl(?:\.localhost|\$)\\([^\\]+)$", drive, re.I)
        if match and distribution and match[1].casefold() == distribution.casefold():
            return tail.replace("\\", "/") or "/"
    raise ValueError("A root mapping is required to open this Windows path on Linux")
