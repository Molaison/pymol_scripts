"""Strict parsing helpers for values received from the PyMOL command line.

PyMOL passes most command arguments to Python as strings.  In particular,
``bool("0")`` and ``bool("false")`` are both true, so command adapters must
not use Python truthiness to interpret boolean options.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import List, Optional


_TRUE_TOKENS = frozenset(("1", "true", "on", "yes", "y", "t"))
_FALSE_TOKENS = frozenset(("0", "false", "off", "no", "n", "f"))


def as_bool(value: object) -> bool:
    """Return *value* as a strict boolean.

    Boolean values and the numeric values 0/1 are accepted directly.  String
    tokens are case-insensitive and surrounding whitespace is ignored.
    Unknown values raise ``ValueError`` instead of silently becoming true.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
    raise ValueError("expected a boolean token (1/0, true/false, on/off, yes/no)")


def as_int(
    value: object,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
    name: str = "value",
) -> int:
    """Parse an integer and optionally enforce inclusive bounds."""

    if isinstance(value, bool):
        raise ValueError("{} must be an integer, not a boolean".format(name))
    if isinstance(value, int):
        result = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("{} must be an integer".format(name))
        result = int(value)
    elif isinstance(value, str):
        token = value.strip()
        if not re.fullmatch(r"[+-]?\d+", token):
            raise ValueError("{} must be an integer".format(name))
        result = int(token)
    else:
        raise ValueError("{} must be an integer".format(name))

    if minimum is not None and result < minimum:
        raise ValueError("{} must be >= {}".format(name, minimum))
    if maximum is not None and result > maximum:
        raise ValueError("{} must be <= {}".format(name, maximum))
    return result


def as_float(
    value: object,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
    name: str = "value",
) -> float:
    """Parse a finite float and optionally enforce inclusive bounds."""

    if isinstance(value, bool):
        raise ValueError("{} must be a number, not a boolean".format(name))
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("{} must be a number".format(name)) from None
    if not math.isfinite(result):
        raise ValueError("{} must be finite".format(name))
    if minimum is not None and result < minimum:
        raise ValueError("{} must be >= {}".format(name, minimum))
    if maximum is not None and result > maximum:
        raise ValueError("{} must be <= {}".format(name, maximum))
    return result


def split_names(value: object) -> List[str]:
    """Split comma/whitespace separated names while preserving order.

    An iterable of values is also accepted. Empty entries are discarded. The
    function deliberately does not deduplicate names because repeated entries
    can be meaningful to callers validating user input.
    """

    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in re.split(r"[,\s]+", value.strip()) if part]
    if isinstance(value, Iterable):
        result = []
        for item in value:
            if item is None:
                continue
            token = str(item).strip()
            if token:
                result.append(token)
        return result
    token = str(value).strip()
    return [token] if token else []


def sanitize_name(
    value: object,
    fallback: str = "unnamed",
    max_length: Optional[int] = None,
) -> str:
    """Return a conservative PyMOL/filename-safe identifier.

    Invalid runs are replaced with one underscore.  A leading digit receives
    an underscore so the result is safe in selection expressions too.
    """

    text = str(value).strip() if value is not None else ""
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_.-")
    if not text:
        text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(fallback)).strip("_.-")
    if not text:
        text = "unnamed"
    if text[0].isdigit():
        text = "_" + text
    if max_length is not None:
        limit = as_int(max_length, minimum=1, name="max_length")
        text = text[:limit].rstrip("_.-") or "_"[:limit]
    return text
