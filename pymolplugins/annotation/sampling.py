"""Deterministic representative sampling for annotation datasets."""

from bisect import bisect_left
from collections import OrderedDict
import math


MISSING_LABEL = "Missing value"


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _value(entry, column):
    return entry.metadata.get(column, "")


def _percentile(sorted_values, fraction):
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def summarize_distribution(all_entries, metric_column):
    """Summarize one numeric column without assigning a quality direction."""

    values = []
    missing = 0
    for entry in all_entries:
        parsed = _number(_value(entry, metric_column))
        if parsed is None:
            missing += 1
        else:
            values.append(parsed)
    values.sort()
    return {
        "column": metric_column,
        "total": len(values) + missing,
        "numeric": len(values),
        "missing": missing,
        "minimum": values[0] if values else None,
        "q1": _percentile(values, 0.25),
        "median": _percentile(values, 0.5),
        "q3": _percentile(values, 0.75),
        "maximum": values[-1] if values else None,
    }


def _format_number(value):
    if value is None:
        return ""
    if value == 0:
        return "0"
    if float(value).is_integer() and abs(value) < 10_000_000:
        return f"{int(value):,}"
    return f"{value:.4g}"


def _metric_boundaries(entries, metric_column):
    if not metric_column:
        return ()
    values = sorted(
        parsed
        for entry in entries
        if (parsed := _number(_value(entry, metric_column))) is not None
    )
    layer_count = min(5, len(set(values)))
    if layer_count < 2:
        return ()
    boundaries = [_percentile(values, index / layer_count) for index in range(1, layer_count)]
    return tuple(dict.fromkeys(boundaries))


def _group_key(entry, group_column):
    if not group_column:
        return None
    value = str(_value(entry, group_column)).strip()
    return (bool(value), value)


def _metric_key(entry, metric_column, boundaries):
    if not metric_column:
        return None
    value = _number(_value(entry, metric_column))
    return None if value is None else bisect_left(boundaries, value)


def _stratum_key(entry, group_column, metric_column, boundaries):
    return (
        _group_key(entry, group_column),
        _metric_key(entry, metric_column, boundaries),
    )


def _metric_label(index, metric_column, boundaries):
    if index is None:
        return f"{metric_column}: {MISSING_LABEL}"
    if not boundaries:
        return f"{metric_column}: all numeric values"
    if index == 0:
        return f"{metric_column} ≤ {_format_number(boundaries[0])}"
    if index == len(boundaries):
        return f"{metric_column} > {_format_number(boundaries[-1])}"
    return (
        f"{_format_number(boundaries[index - 1])} < {metric_column} "
        f"≤ {_format_number(boundaries[index])}"
    )


def _stratum_label(key, group_column, metric_column, boundaries):
    group_key, metric_key = key
    parts = []
    if group_column:
        if group_key and group_key[0]:
            parts.append(f"{group_column}: “{group_key[1]}”")
        else:
            parts.append(f"{group_column}: {MISSING_LABEL}")
    if metric_column:
        parts.append(_metric_label(metric_key, metric_column, boundaries))
    return " · ".join(parts) if parts else "All filtered rows"


def _representative_indices(indices, limit):
    if len(indices) <= limit:
        return indices
    count = len(indices)
    return [indices[int((position + 0.5) * count / limit)] for position in range(limit)]


def _sample_details(entries, group_column, metric_column, per_stratum, all_entries=None):
    entries = tuple(entries)
    boundaries = _metric_boundaries(entries, metric_column)
    buckets = OrderedDict()
    for index, entry in enumerate(entries):
        key = _stratum_key(entry, group_column, metric_column, boundaries)
        buckets.setdefault(key, []).append(index)

    all_counts = {}
    for entry in entries if all_entries is None else all_entries:
        key = _stratum_key(entry, group_column, metric_column, boundaries)
        all_counts[key] = all_counts.get(key, 0) + 1

    selected_indices = set()
    records = []
    for key, indices in buckets.items():
        chosen = _representative_indices(indices, per_stratum)
        selected_indices.update(chosen)
        records.append(
            {
                "label": _stratum_label(key, group_column, metric_column, boundaries),
                "total": all_counts.get(key, 0),
                "filtered": len(indices),
                "selected": len(chosen),
            }
        )
    selected = tuple(entry for index, entry in enumerate(entries) if index in selected_indices)
    return selected, records


def stratified_sample(
    entries, *, group_column="", metric_column="", per_stratum=2
):
    """Select stable, evenly spaced representatives from each stratum.

    Numeric values are divided into at most five quantile layers. Missing
    numeric values form their own layer. Group and numeric strata are crossed
    when both columns are provided. Returned entries retain source order. Each
    record contains the stratum's text label, input total, and selected count.
    """

    if int(per_stratum) < 1:
        raise ValueError("per_stratum must be at least 1")
    selected, details = _sample_details(
        entries,
        str(group_column or ""),
        str(metric_column or ""),
        int(per_stratum),
    )
    records = tuple(
        {"label": record["label"], "total": record["filtered"], "selected": record["selected"]}
        for record in details
    )
    return selected, records


__all__ = ["stratified_sample", "summarize_distribution"]
