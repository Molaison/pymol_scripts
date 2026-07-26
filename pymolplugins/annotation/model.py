"""Pure-data dataset model shared by Structure Annotator sources.

The classes in this module deliberately have no PyMOL or Qt dependency.  A
source adapter only describes rows and paths; path mapping, materialization,
and loading into PyMOL happen in later layers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
import math
import re
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple


AVAILABILITY_UNCHECKED = "unchecked"
AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_INVALID = "invalid"
AVAILABILITY_MISSING = "missing"
AVAILABILITY_UNREADABLE = "unreadable"
AVAILABILITY_AMBIGUOUS = "ambiguous"
AVAILABILITY_ERROR = "error"

COLUMN_TEXT = "text"
COLUMN_INTEGER = "integer"
COLUMN_NUMBER = "number"

_INTEGER = re.compile(r"^[+-]?\d+$")
_RESOLUTION_FAILURES = {
    AVAILABILITY_INVALID,
    AVAILABILITY_MISSING,
    AVAILABILITY_UNREADABLE,
    AVAILABILITY_AMBIGUOUS,
    AVAILABILITY_ERROR,
}


class DatasetSourceError(ValueError):
    """Raised when a dataset source configuration or document is invalid."""


def _resolution_values(
    resolution: object,
    resolved_path: Optional[str],
    availability: Optional[str],
    error: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if resolved_path is None:
        resolved_path = str(getattr(resolution, "resolved_path", "") or "")
    if availability is None:
        availability = str(
            getattr(resolution, "status", AVAILABILITY_UNCHECKED)
            or AVAILABILITY_UNCHECKED
        )
    if error is not None:
        return resolved_path, availability, error
    raw_error = getattr(resolution, "error", "")
    if raw_error:
        return resolved_path, availability, str(raw_error)
    if availability in _RESOLUTION_FAILURES:
        diagnostics = getattr(resolution, "diagnostics", ()) or ()
        error = "; ".join(str(value) for value in diagnostics if str(value))
    return resolved_path, availability, error


def infer_column_kind(values: Iterable[str]) -> str:
    """Infer a conservative display kind without changing the raw strings.

    Empty cells are ignored.  A column is numeric only when every non-empty
    value is finite and parseable.  Integer spelling is kept distinct so a UI
    can avoid displaying unnecessary decimal places.
    """

    saw_value = False
    all_integer = True
    for value in values:
        raw = str(value)
        stripped = raw.strip()
        if not stripped:
            continue
        saw_value = True
        if _INTEGER.fullmatch(stripped):
            # Python integers are unbounded; do not route an otherwise valid
            # large integer through float where it could become infinity.
            continue
        all_integer = False
        try:
            number = float(stripped)
        except (TypeError, ValueError, OverflowError):
            return COLUMN_TEXT
        if not math.isfinite(number):
            return COLUMN_TEXT
    if not saw_value:
        return COLUMN_TEXT
    return COLUMN_INTEGER if all_integer else COLUMN_NUMBER


def display_value(raw: str, kind: str) -> object:
    """Return a typed value for sorting/display while preserving blanks."""

    text = str(raw)
    stripped = text.strip()
    if not stripped:
        return None
    if kind == COLUMN_INTEGER:
        return int(stripped)
    if kind == COLUMN_NUMBER:
        return float(stripped)
    return text


@dataclass(frozen=True)
class ColumnSpec:
    """Display contract for one source column."""

    name: str
    kind: str = COLUMN_TEXT
    numeric_count: int = 0
    missing_count: int = 0

    def __post_init__(self) -> None:
        if not self.name:
            raise DatasetSourceError("column name is required")
        if self.kind not in {COLUMN_TEXT, COLUMN_INTEGER, COLUMN_NUMBER}:
            raise DatasetSourceError("unsupported column kind: %s" % self.kind)
        if self.numeric_count < 0 or self.missing_count < 0:
            raise DatasetSourceError("column counts cannot be negative")


@dataclass(frozen=True)
class AnnotationEntry:
    """One independently annotatable structure row.

    ``source_path`` is the source's exact path spelling.  In particular, CSV
    adapters do not expand ``~``, normalize separators, or make relative paths
    absolute.  A path resolver may attach its result with
    :meth:`with_resolution` without losing that original value.
    """

    id: str
    ordinal: int
    display_name: str
    source_path: str
    source_kind: str
    source_base: str = ""
    row_number: Optional[int] = None
    metadata: Dict[str, str] = field(default_factory=dict, compare=False)
    raw_values: Tuple[str, ...] = field(default_factory=tuple, compare=False)
    display_metadata: Dict[str, object] = field(default_factory=dict, compare=False)
    resolved_path: str = ""
    path_resolution: Optional[object] = field(default=None, compare=False)
    availability: str = AVAILABILITY_UNCHECKED
    validation_errors: Tuple[str, ...] = ()
    resolution_error: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise DatasetSourceError("entry id is required")
        if int(self.ordinal) < 0:
            raise DatasetSourceError("entry ordinal cannot be negative")
        if self.row_number is not None and int(self.row_number) < 1:
            raise DatasetSourceError("entry row_number must be positive")
        if not self.source_kind:
            raise DatasetSourceError("entry source_kind is required")
        if not self.availability:
            raise DatasetSourceError("entry availability is required")

        # Copy mappings so callers cannot mutate a row through the object they
        # originally passed.  Plain dicts retain CSV header order on every
        # supported Python version.
        object.__setattr__(self, "ordinal", int(self.ordinal))
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "raw_values", tuple(self.raw_values))
        object.__setattr__(self, "display_metadata", dict(self.display_metadata))
        object.__setattr__(
            self,
            "validation_errors",
            tuple(str(value) for value in self.validation_errors if str(value)),
        )

    @property
    def raw_path(self) -> str:
        """Compatibility alias emphasizing that ``source_path`` is untouched."""

        return self.source_path

    @property
    def error(self) -> str:
        """Combined row-validation and path-resolution error text."""

        parts = list(self.validation_errors)
        if self.resolution_error:
            parts.append(self.resolution_error)
        return "; ".join(parts)

    @property
    def status(self) -> str:
        """UI-friendly status preserving path-resolution failure classes.

        CSV shape/identity errors are reported as ``error``.  Resolution
        failures remain ``missing``, ``unreadable``, ``ambiguous`` and so on;
        callers can still display the detailed combined :attr:`error` text.
        Collapsing every resolution diagnostic to ``error`` makes mapping
        previews unable to distinguish a bad row from an unavailable file.
        """

        return AVAILABILITY_ERROR if self.validation_errors else self.availability

    @property
    def is_navigable(self) -> bool:
        """Whether the row has a usable path candidate for later processing."""

        return bool(self.source_path.strip()) and self.availability not in {
            AVAILABILITY_INVALID,
            AVAILABILITY_MISSING,
            AVAILABILITY_UNREADABLE,
            AVAILABILITY_AMBIGUOUS,
            AVAILABILITY_ERROR,
        }

    def with_resolution(
        self,
        resolution: Optional[object] = None,
        *,
        resolved_path: Optional[str] = None,
        availability: Optional[str] = None,
        error: Optional[str] = None,
        path_resolution: Optional[object] = None,
    ) -> "AnnotationEntry":
        """Return a copy carrying a resolver result.

        ``resolution`` is intentionally duck-typed to keep this pure model
        independent from a particular resolver implementation.  Objects with
        ``resolved_path``, ``status``, ``error`` and/or ``diagnostics`` fields
        work directly.  Callers may also supply the keyword fields explicitly.
        """

        if resolution is None and path_resolution is None:
            attached = self.path_resolution
        else:
            attached = resolution if path_resolution is None else path_resolution
        if resolution is not None:
            resolved_path, availability, error = _resolution_values(
                resolution, resolved_path, availability, error
            )
        if resolved_path is None:
            resolved_path = self.resolved_path
        if availability is None:
            availability = self.availability
        if error is None:
            error = self.resolution_error
        return replace(
            self,
            resolved_path=str(resolved_path),
            path_resolution=attached,
            availability=str(availability),
            resolution_error=str(error),
        )


@dataclass(frozen=True)
class AnnotationDataset:
    """Immutable snapshot emitted by a directory or CSV source."""

    source_kind: str
    source_path: str
    source_base: str
    entries: Tuple[AnnotationEntry, ...]
    columns: Tuple[str, ...] = ()
    display_columns: Tuple[str, ...] = ()
    column_specs: Tuple[ColumnSpec, ...] = ()
    path_column: str = ""
    id_column: str = ""
    delimiter: str = ""
    encoding: str = ""

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        ids = [entry.id for entry in entries]
        if len(ids) != len(set(ids)):
            raise DatasetSourceError("entry ids must be unique")
        if len(self.columns) != len(set(self.columns)):
            raise DatasetSourceError("dataset columns must be unique")
        unknown_display = [
            name for name in self.display_columns if name not in self.columns
        ]
        if unknown_display:
            raise DatasetSourceError(
                "unknown display columns: %s" % ", ".join(unknown_display)
            )
        spec_names = [spec.name for spec in self.column_specs]
        if len(spec_names) != len(set(spec_names)):
            raise DatasetSourceError("column specs must be unique")
        if tuple(spec_names) != tuple(self.display_columns):
            raise DatasetSourceError(
                "column specs must match display_columns in the same order"
            )
        if self.path_column and self.path_column not in self.columns:
            raise DatasetSourceError("path_column is not present in dataset columns")
        if self.id_column and self.id_column not in self.columns:
            raise DatasetSourceError("id_column is not present in dataset columns")
        if any(entry.source_kind != self.source_kind for entry in entries):
            raise DatasetSourceError(
                "all entries must have the dataset's source_kind"
            )
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "display_columns", tuple(self.display_columns))
        object.__setattr__(self, "column_specs", tuple(self.column_specs))

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def error_entries(self) -> Tuple[AnnotationEntry, ...]:
        return tuple(entry for entry in self.entries if entry.error)

    def column_spec(self, name: str) -> ColumnSpec:
        for spec in self.column_specs:
            if spec.name == name:
                return spec
        raise KeyError(name)

    def entry_by_id(self, entry_id: str) -> AnnotationEntry:
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(entry_id)


class DatasetSource(ABC):
    """Abstract adapter returning one deterministic dataset snapshot."""

    @abstractmethod
    def load(self) -> AnnotationDataset:
        """Read the source and return a new snapshot."""


def build_column_specs_from_metadata(
    metadata_rows: Sequence[Mapping[str, str]], display_columns: Sequence[str]
) -> Tuple[ColumnSpec, ...]:
    """Infer display specs directly from ordered raw metadata mappings."""

    specs = []
    for name in display_columns:
        values = [metadata.get(name, "") for metadata in metadata_rows]
        kind = infer_column_kind(values)
        missing = sum(not value.strip() for value in values)
        numeric = 0
        if kind in {COLUMN_INTEGER, COLUMN_NUMBER}:
            numeric = len(values) - missing
        specs.append(
            ColumnSpec(
                name=name,
                kind=kind,
                numeric_count=numeric,
                missing_count=missing,
            )
        )
    return tuple(specs)


def build_column_specs(
    entries: Sequence[AnnotationEntry], display_columns: Sequence[str]
) -> Tuple[ColumnSpec, ...]:
    """Infer display specs from raw entry metadata in column order."""

    return build_column_specs_from_metadata(
        tuple(entry.metadata for entry in entries), display_columns
    )


def build_display_metadata(
    metadata: Mapping[str, str], specs: Sequence[ColumnSpec]
) -> Dict[str, object]:
    """Build one ordered, typed display mapping without mutating raw values."""

    return {
        spec.name: display_value(metadata.get(spec.name, ""), spec.kind)
        for spec in specs
    }


def attach_display_metadata(
    entries: Sequence[AnnotationEntry], specs: Sequence[ColumnSpec]
) -> Tuple[AnnotationEntry, ...]:
    """Return entries with an ordered, typed display mapping attached."""

    result = []
    for entry in entries:
        result.append(
            replace(
                entry,
                display_metadata=build_display_metadata(entry.metadata, specs),
            )
        )
    return tuple(result)


__all__ = [
    "AVAILABILITY_AMBIGUOUS",
    "AVAILABILITY_AVAILABLE",
    "AVAILABILITY_ERROR",
    "AVAILABILITY_INVALID",
    "AVAILABILITY_MISSING",
    "AVAILABILITY_UNCHECKED",
    "AVAILABILITY_UNREADABLE",
    "COLUMN_INTEGER",
    "COLUMN_NUMBER",
    "COLUMN_TEXT",
    "AnnotationDataset",
    "AnnotationEntry",
    "ColumnSpec",
    "DatasetSource",
    "DatasetSourceError",
    "attach_display_metadata",
    "build_column_specs",
    "build_column_specs_from_metadata",
    "build_display_metadata",
    "display_value",
    "infer_column_kind",
]
