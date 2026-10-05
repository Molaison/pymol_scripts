"""Pure, explicit fixed-reference and ID-pair planning."""

from dataclasses import dataclass
import fnmatch
import glob
from pathlib import Path
import re
from string import Formatter


@dataclass(frozen=True)
class BatchPair:
    identifier: str
    left: str
    right: str


def natural_key(value):
    """Sort numeric ID components numerically without discarding zero padding."""
    return tuple((0, int(part), len(part)) if part.isdigit() else (1, part.casefold())
                 for part in re.split(r"(\d+)", str(value)) if part)


def select_ids(available, identifiers=""):
    """Select exact IDs or numeric inclusive ranges, retaining requested order."""
    available = tuple(available)
    if not identifiers:
        return tuple(sorted(available, key=natural_key))
    selected, seen = [], set()
    for token in str(identifiers).split(","):
        token = token.strip()
        if not token:
            raise ValueError("IDs must not contain empty items")
        match = re.fullmatch(r"(\d+)-(\d+)", token)
        if match:
            start, end = map(int, match.groups())
            if start > end or end - start >= 100000:
                raise ValueError("ID ranges must be increasing and at most 100000 items")
            values = (str(value) for value in range(start, end + 1))
        else:
            values = (token,)
        for value in values:
            if value in seen:
                raise ValueError("Duplicate requested ID: " + value)
            seen.add(value)
            selected.append(value)
            if len(selected) > 100000:
                raise ValueError("At most 100000 IDs may be requested")
    available_set = set(available)
    missing = [value for value in selected if value not in available_set]
    if missing:
        raise ValueError("Unknown IDs: " + ", ".join(missing))
    return tuple(selected)


def reference_pairs(names, reference):
    """Pair each unique input with one reference, excluding self-comparisons."""
    names = tuple(str(name) for name in names)
    if not reference or any(not name for name in names):
        raise ValueError("A inputs and reference must not be empty")
    if len(names) != len(set(names)):
        raise ValueError("Duplicate A inputs")
    pairs = tuple(BatchPair(str(index), name, str(reference))
                  for index, name in enumerate((n for n in names if n != reference), 1))
    if not pairs:
        raise ValueError("No A inputs remain after excluding the reference")
    return pairs


def _pattern_parts(pattern):
    parts = list(Formatter().parse(str(pattern)))
    fields = [(field, spec, conversion) for _, field, spec, conversion in parts
              if field is not None]
    if fields != [("id", "", None)]:
        raise ValueError("Each pair pattern must contain exactly one {id} and no other fields")
    before, after, found = "", "", False
    for literal, field, _, _ in parts:
        if found:
            after += literal
        else:
            before += literal
        if field is not None:
            found = True
    return before, after


def id_pattern_glob(pattern, match="template"):
    """Build discovery globs without changing literal-template semantics."""
    if match == "glob":
        before, after = _glob_parts(pattern)
        return before + "*" + after
    if match != "template":
        raise ValueError("File patterns must use template or glob matching")
    before, after = _pattern_parts(pattern)
    return glob.escape(before) + "*" + glob.escape(after)


def _glob_parts(pattern):
    if "{id}" in str(pattern):
        return _pattern_parts(pattern)
    stars = [match.start() for match in re.finditer(r"\[[^]]*\]|\*", str(pattern)) if match.group() == "*"]
    if len(stars) != 1:
        raise ValueError("A wildcard pattern needs one * for the ID, or an explicit {id} with other wildcards")
    return str(pattern)[:stars[0]], str(pattern)[stars[0] + 1:]


def _id_regex(pattern, match, case_sensitive):
    flags = 0 if case_sensitive else re.IGNORECASE
    if match == "regex":
        regex = re.compile(pattern, flags)
        if "id" not in regex.groupindex and regex.groups != 1:
            raise ValueError("A regex needs an (?P<id>...) group or exactly one capture group")
        return regex
    if match == "glob":
        before, after = _glob_parts(pattern)
        marker = "PYMOLPLUGINSCAPTUREID"
        while marker in str(pattern):
            marker += "X"
        expression = fnmatch.translate(before + marker + after).replace(marker, r"(?P<id>[^/\\]+)")
    elif match == "template":
        before, after = _pattern_parts(pattern)
        expression = re.escape(before) + r"(?P<id>[^/\\]+)" + re.escape(after)
    else:
        raise ValueError("Pair matching must be template, glob or regex")
    return re.compile(expression, flags)


def _index_ids(names, pattern, side, case_sensitive, match):
    regex = _id_regex(pattern, match, case_sensitive)
    result = {}
    for name in names:
        found = regex.fullmatch(Path(name).name if match == "regex" else str(name))
        if found is None:
            if match == "regex":
                continue
            raise ValueError("%s input does not match its ID pattern: %s" % (side, name))
        identifier = found.group("id" if "id" in regex.groupindex else 1)
        if not identifier:
            raise ValueError("Empty %s ID in %s" % (side, name))
        if identifier in result:
            raise ValueError("Duplicate %s ID %s" % (side, identifier))
        result[identifier] = str(name)
    return result


def id_pairs(left_names, right_names, left_pattern, right_pattern, identifiers="", *, case_sensitive=True,
             match="template"):
    """Pair equal IDs; missing or duplicate partners never become skipped rows."""
    left = _index_ids(left_names, left_pattern, "A", case_sensitive, match)
    right = _index_ids(right_names, right_pattern, "B", case_sensitive, match)
    ids = select_ids(set(left) | set(right), identifiers)
    if not ids:
        raise ValueError("No matching A/B IDs")
    missing_left = [value for value in ids if value not in left]
    missing_right = [value for value in ids if value not in right]
    if missing_left or missing_right:
        raise ValueError("Missing A IDs: %s; missing B IDs: %s" %
                         (", ".join(missing_left) or "none", ", ".join(missing_right) or "none"))
    if any(left[value] == right[value] for value in ids):
        raise ValueError("A and B must be different inputs for each ID")
    return tuple(BatchPair(value, left[value], right[value]) for value in ids)
