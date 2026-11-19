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
    bin_ranges: tuple = ()
    minimum: object = None
    maximum: object = None

    def selected_range(self, first, last):
        """Return the observed bounds in an inclusive selection of bins."""
        bounds = [pair for pair in self.bin_ranges[first:last + 1] if pair is not None]
        return (bounds[0][0], bounds[-1][1]) if bounds else None


def _histogram(values, minimum, maximum, integer, count=12):
    if minimum == maximum:
        return (len(values),), ((minimum, maximum),)
    span = maximum - minimum
    bins = [0] * count
    ranges = [None] * count
    if integer:
        indices = ((value - minimum) * count // span for value in values)
    else:
        # Halve only when subtracting the two finite endpoints overflows.
        scale = 1.0 if math.isfinite(span) else 0.5
        left = minimum * scale
        span = maximum * scale - left
        indices = (int((value * scale - left) / span * count) for value in values)
    for value, index in zip(values, indices):
        index = min(count - 1, index)
        bins[index] += 1
        previous = ranges[index]
        ranges[index] = (min(previous[0], value), max(previous[1], value)) if previous else (value, value)
    return tuple(bins), tuple(ranges)


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
        bins, ranges = _histogram(values, minimum, maximum, kind == COLUMN_INTEGER)
        profiles.append(ColumnProfile(total, missing, len(set(values)), bins=bins, bin_ranges=ranges,
                                      minimum=minimum, maximum=maximum))
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
