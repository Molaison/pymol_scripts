"""Strict parsing helpers for values received from the PyMOL command line.

PyMOL passes most command arguments to Python as strings.  In particular,
``bool("0")`` and ``bool("false")`` are both true, so command adapters must
not use Python truthiness to interpret boolean options.
"""

from __future__ import annotations

import math
import re
import inspect
from collections.abc import Iterable
from functools import wraps
from typing import Any, Callable, List, Optional


_TRUE_TOKENS = frozenset(("1", "true", "on", "yes", "y", "t"))
_FALSE_TOKENS = frozenset(("0", "false", "off", "no", "n", "f"))
_CONSOLE_ADAPTER_MARKER = "_pymolplugins_console_adapter"


def _literal_fields(text: str):
    """Split command arguments, treating quoted text before bracket nesting."""
    parts, stack = [], []
    quote, start, index = "", 0, 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    while index < len(text):
        char = text[index]
        if quote:
            if text.startswith(quote, index):
                index += len(quote)
                quote = ""
                continue
        elif char in "\"'":
            quote = "'''" if text.startswith("'''", index) else char
            index += len(quote)
            continue
        elif char in pairs:
            stack.append(pairs[char])
        elif char in ")]}":
            if not stack or stack.pop() != char:
                raise ValueError("Unmatched bracket in command arguments")
        elif not stack and char in ",;":
            parts.append(text[start:index].strip())
            if char == ";":
                return parts, text[index + 1:].strip()
            start = index + 1
        index += 1
    if quote or stack:
        raise ValueError("Unclosed quote or bracket in command arguments")
    if text[start:].strip() or parts:
        parts.append(text[start:].strip())
    return parts, ""


def _literal_console_command(command_api, function):
    adapted = console_command(function)
    signature = inspect.signature(function)

    def literal(text=""):
        parts, following = _literal_fields(text)
        args, kwargs = [], {}
        for part in parts:
            named = re.match(r"^([A-Za-z_]\w*)\s*=(.*)$", part, re.DOTALL)
            if named:
                key, value = named.groups()
                if key in kwargs:
                    raise TypeError("Repeated argument: " + key)
                kwargs[key] = value.strip()
            else:
                args.append(part if part else None)
        bound = signature.bind(*args, **kwargs)
        result = adapted(*bound.args, **bound.kwargs)
        if following:
            command_api.do(following)
        return result

    literal.__name__ = function.__name__
    literal.__doc__ = function.__doc__
    setattr(literal, _CONSOLE_ADAPTER_MARKER, True)
    literal._pymolplugins_literal_target = function
    return literal


def console_text(value: str) -> str:
    """Remove exactly one quote-syntax layer from a PyMOL console value.

    PyMOL's STRICT parser preserves surrounding single-, double-, and
    triple-single-quote delimiters for functions registered with
    ``cmd.extend``.  The quotes are syntax at that boundary, not part of a
    command template, selection, regular expression, URL, or Windows path.
    This helper deliberately performs no escape decoding and leaves
    mismatched quotes untouched.
    """

    if len(value) >= 6 and value.startswith("'''") and value.endswith("'''"):
        return value[3:-3]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def console_command(function: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap one ``cmd.extend`` callback with STRICT-parser normalization.

    Only explicitly supplied string arguments are normalized.  Defaults and
    direct Python calls to *function* retain their original semantics.
    ``functools.wraps`` preserves the public signature PyMOL inspects while
    binding console arguments.
    """

    if getattr(function, _CONSOLE_ADAPTER_MARKER, False):
        return function

    @wraps(function)
    def adapted(*args: Any, **kwargs: Any) -> Any:
        normalized_args = tuple(
            console_text(value) if isinstance(value, str) else value
            for value in args
        )
        normalized_kwargs = {
            key: console_text(value) if isinstance(value, str) else value
            for key, value in kwargs.items()
        }
        return function(*normalized_args, **normalized_kwargs)

    setattr(adapted, _CONSOLE_ADAPTER_MARKER, True)
    return adapted


def extend_command(
    command_api: Any, name: str, function: Callable[..., Any], *, literal: bool = False
) -> Callable[..., Any]:
    """Register a canonical PyMOL command through :func:`console_command`."""

    if literal:
        function._pymolplugins_literal_arguments = True
    if getattr(function, "_pymolplugins_literal_arguments", False):
        adapted = _literal_console_command(command_api, function)
        command_api.extend(name, adapted)
        # PyMOL parsing.LITERAL: preserve the complete quoted regex and template.
        command_api.keyword[name][4] = 20
        return adapted
    adapted = console_command(function)
    command_api.extend(name, adapted)
    return adapted


def is_console_command(function: object) -> bool:
    """Return whether *function* carries the canonical console adapter."""

    return bool(getattr(function, _CONSOLE_ADAPTER_MARKER, False))


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
