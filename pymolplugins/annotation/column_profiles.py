"""Summarize metadata columns for the current Scores rows."""

from collections import Counter
from dataclasses import dataclass
import math

from .model import COLUMN_INTEGER, COLUMN_TEXT, display_value, infer_column_kind


@dataclass(frozen=True)
class ColumnProfile:
    total: int
    missing: int
    distinct: int
    categories: tuple = ()
    other: int = 0
    bins: tuple = ()
    minimum: object = None
    maximum: object = None


def _histogram(values, minimum, maximum, integer, count=12):
    if minimum == maximum:
        return (len(values),)
    span = maximum - minimum
    bins = [0] * count
    if integer:
        for value in values:
            bins[min(count - 1, (value - minimum) * count // span)] += 1
    else:
        # Halve only when subtracting the two finite endpoints overflows.
        scale = 1.0 if math.isfinite(span) else 0.5
        left = minimum * scale
        span = maximum * scale - left
        for value in values:
            index = int((value * scale - left) / span * count)
            bins[min(count - 1, index)] += 1
    return tuple(bins)


def build_column_profiles(rows, columns, kinds, *, preview=False, cancelled=None):
    """Compute exact proportions and histograms over the table's row snapshot."""
    metadata = [row.get("metadata") or {} for row in rows] if preview else [row.metadata for row in rows]
    profiles = []
    for column in columns:
        if cancelled is not None and cancelled.is_set():
            break
        cells = [row.get(column, "") for row in metadata]
        present = [value for value in cells if value.strip()]
        total, missing = len(cells), len(cells) - len(present)
        kind = kinds.get(column) or infer_column_kind(present)
        if not present or kind == COLUMN_TEXT:
            counts = Counter(present)
            categories = tuple(counts.most_common(3))
            other = len(present) - sum(count for _, count in categories)
            profiles.append(ColumnProfile(total, missing, len(counts), categories, other))
            continue
        values = [display_value(value, kind) for value in present]
        minimum, maximum = min(values), max(values)
        bins = _histogram(values, minimum, maximum, kind == COLUMN_INTEGER)
        profiles.append(ColumnProfile(total, missing, len(set(values)), bins=bins, minimum=minimum, maximum=maximum))
    return profiles


def format_proportion(count, total):
    if not count or not total:
        return "0%"
    percent = 100 * count / total
    if percent < 1:
        return "<1%"
    if percent > 99 and count < total:
        return ">99%"
    return f"{percent:.0f}%"
