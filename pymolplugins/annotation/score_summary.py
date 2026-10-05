"""Cached numeric guidance for the current candidate and filtered cohort."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math
import re

from .review_config import validate_score_config


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def format_score(value):
    parsed = number(value)
    if parsed is None:
        return "—"
    if parsed == 0:
        return "0"
    magnitude = abs(parsed)
    if parsed.is_integer() and magnitude < 10_000_000:
        return f"{int(parsed):,}"
    if magnitude < 0.001 or magnitude >= 10000:
        return f"{parsed:.3g}"
    if magnitude < 1:
        return f"{parsed:.3g}"
    decimals = 2 if magnitude < 100 else 1
    return f"{parsed:,.{decimals}f}".rstrip("0").rstrip(".")


def _normal_name(value):
    return re.sub(r"\W+", "_", value).strip("_").casefold()


def _pass_column(name, columns):
    wanted = {_normal_name(name) + "_pass", "score_" + _normal_name(name) + "_pass"}
    return next((column for column in columns if _normal_name(column) in wanted), "")


def _absolute(value, metadata, rule, pass_column):
    if value is None:
        return "Missing", "neutral"
    if pass_column:
        raw = str(metadata.get(pass_column, "")).strip().casefold()
        if raw in {"true", "1", "1.0", "yes", "pass", "passed"}:
            return "CSV: pass", "good"
        if raw in {"false", "0", "0.0", "no", "fail", "failed"}:
            return "CSV: fail", "bad"
    direction = rule.get("direction", "neutral")
    good, bad = rule.get("good"), rule.get("bad")
    if good is not None and ((direction == "higher" and value >= good) or (direction == "lower" and value <= good)):
        return "Target met", "good"
    if bad is not None and ((direction == "higher" and value <= bad) or (direction == "lower" and value >= bad)):
        return "Outside target", "bad"
    if good is not None or bad is not None:
        return "Between targets", "middle"
    return "No absolute rule", "neutral"


@dataclass(frozen=True)
class ScoreSummary:
    columns: tuple
    distributions: dict
    rules: dict
    pass_columns: dict
    count: int

    def cards(self, entry):
        result = []
        for name in self.columns:
            raw = entry.metadata.get(name)
            value = number(raw)
            distribution = self.distributions[name]
            rule = self.rules.get(name, {})
            direction = rule.get("direction", "neutral")
            percentile = None
            if value is not None and len(distribution) > 1:
                percentile = 50 * (bisect_left(distribution, value) + bisect_right(distribution, value)) / len(distribution)
            relative_tone = "neutral"
            relative = f"n={len(distribution):,}"
            if percentile is not None:
                if direction == "neutral":
                    relative = f"Value P{percentile:.0f} · n={len(distribution):,}"
                else:
                    quality = percentile if direction == "higher" else 100 - percentile
                    relative = f"Relative rank {quality:.0f}/100 · n={len(distribution):,}"
                    relative_tone = "good" if quality >= 80 else "bad" if quality <= 20 else "middle"
            absolute, tone = _absolute(value, entry.metadata, rule, self.pass_columns[name])
            result.append({"column": name, "value": format_score(raw), "raw": "" if raw is None else str(raw),
                           "absolute": absolute, "absolute_tone": tone,
                           "relative": relative, "relative_tone": relative_tone,
                           "direction": direction, "percentile": percentile,
                           "pass_column": self.pass_columns[name]})
        return result


def prepare_score_summary(entries, selected_columns, config=None, *, excluded=()):
    """Build sorted score arrays once; subsequent candidate updates use bisect."""
    config = validate_score_config(config or {})
    candidates = [name for name in selected_columns if name not in set(excluded)]
    if config["columns"] is not None:
        candidates = [name for name in config["columns"] if name in candidates]
    else:
        priorities = ("iptm", "plddt", "ipsae", "ipae", "lis", "weighted_score", "score", "cms")
        candidates.sort(key=lambda name: next((i for i, token in enumerate(priorities) if token in name.casefold()), len(priorities)))
    distributions = {}
    for name in candidates:
        values = []
        for entry in entries:
            parsed = number(entry.metadata.get(name))
            if parsed is not None:
                values.append(parsed)
        if values:
            distributions[name] = tuple(sorted(values))
            if len(distributions) == 6:
                break
    columns = tuple(distributions)
    schema = tuple(entries[0].metadata) if entries else ()
    pass_columns = {}
    for name in columns:
        rule = config["rules"].get(name, {})
        automatic = "" if rule.get("good") is not None or rule.get("bad") is not None else _pass_column(name, schema)
        explicit = rule.get("pass_column")
        if explicit and explicit != "-" and explicit not in schema:
            raise ValueError(f"Unknown CSV pass column: {explicit}")
        pass_columns[name] = "" if explicit == "-" else explicit or automatic
    return ScoreSummary(columns, distributions, config["rules"], pass_columns, len(entries))
