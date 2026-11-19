"""Data-backed filter value suggestions, cached for the current source."""

from collections import Counter

from .model import COLUMN_TEXT, display_value, infer_column_kind


class FilterValueCatalog:
    def __init__(self, dataset=None):
        self.dataset = dataset
        self._kinds = {spec.name: spec.kind for spec in dataset.column_specs} if dataset else {}
        self._cache = {}

    def _values(self, column, text):
        key = column, text
        if key in self._cache:
            return self._cache[key]
        raw = [entry.metadata.get(column, "") for entry in self.dataset.entries]
        present = [value for value in raw if value.strip()]
        kind = COLUMN_TEXT if text else self._kinds.get(column) or infer_column_kind(present)
        if kind == COLUMN_TEXT:
            values = tuple(value for value, _ in Counter(present).most_common())
        else:
            values = tuple(map(str, sorted({display_value(value, kind) for value in present})))
        self._cache[key] = kind, values
        return kind, values

    def suggestions(self, column, query="", *, text=False, literal=True):
        if self.dataset is None or column not in self.dataset.columns:
            return []
        kind, values = self._values(column, text)
        needle = query.casefold()
        if needle:
            prefix, contains = [], []
            for value in values:
                folded = value.casefold()
                if folded.startswith(needle):
                    prefix.append(value)
                    if len(prefix) == 12:
                        break
                elif kind == COLUMN_TEXT and needle in folded and len(contains) < 12:
                    contains.append(value)
            matches = (prefix + contains)[:12]
        elif kind != COLUMN_TEXT and len(values) > 5:
            last = len(values) - 1
            matches = [values[last * part // 4] for part in range(5)]
        else:
            matches = values[:12]
        return [repr(value) for value in matches] if literal and kind == COLUMN_TEXT else list(matches)
