"""CSV-backed Structure Annotator dataset source."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import ntpath
import os
import re
from typing import Dict, List, Optional, Sequence, TextIO, Tuple

from pymolplugins.annotation.model import (
    AVAILABILITY_INVALID,
    AVAILABILITY_UNCHECKED,
    AnnotationDataset,
    AnnotationEntry,
    ColumnSpec,
    DatasetSource,
    DatasetSourceError,
    build_column_specs_from_metadata,
    build_display_metadata,
)


AUTO_DELIMITER = "auto"
DEFAULT_ENCODING = "utf-8-sig"
_AUTO_DELIMITERS = (",", "\t", ";", "|")
_DELIMITER_NAMES = {
    "comma": ",",
    "tab": "\t",
    "semicolon": ";",
    "pipe": "|",
}


class CsvSourceError(DatasetSourceError):
    """Raised when a CSV dataset cannot satisfy its declared contract."""


@dataclass(frozen=True)
class CsvSourceConfig:
    """Serializable configuration for a CSV dataset source."""

    csv_path: str
    path_column: str
    id_column: str = ""
    display_columns: Optional[Tuple[str, ...]] = None
    encoding: str = DEFAULT_ENCODING
    delimiter: str = AUTO_DELIMITER
    source_base: str = ""


@dataclass(frozen=True)
class _RawRow:
    ordinal: int
    row_number: int
    metadata: Dict[str, str]
    raw_values: Tuple[str, ...]
    extra_count: int


def _normalize_delimiter(value: str) -> str:
    requested = str(value or AUTO_DELIMITER)
    named = _DELIMITER_NAMES.get(requested.lower())
    if named is not None:
        return named
    if requested.lower() == AUTO_DELIMITER:
        return AUTO_DELIMITER
    if len(requested) != 1 or requested in {"\r", "\n", "\0"}:
        raise CsvSourceError(
            "delimiter must be 'auto', a supported name, or one character"
        )
    return requested


def _header_from_sample(sample: str, delimiter: str) -> Tuple[str, ...]:
    try:
        reader = csv.reader(
            io.StringIO(sample, newline=""), delimiter=delimiter, strict=True
        )
        return tuple(next(reader))
    except (StopIteration, csv.Error):
        return ()


def _detect_delimiter(sample: str, path_column: str) -> str:
    """Choose a delimiter whose parsed header contains the exact path column."""

    sniffed = ""
    try:
        sniffed = csv.Sniffer().sniff(
            sample, delimiters="".join(_AUTO_DELIMITERS)
        ).delimiter
    except csv.Error:
        pass

    candidates: List[str] = []
    for delimiter in (sniffed,) + _AUTO_DELIMITERS:
        if delimiter and delimiter not in candidates:
            candidates.append(delimiter)
    matches = [
        delimiter
        for delimiter in candidates
        if path_column in _header_from_sample(sample, delimiter)
    ]
    if sniffed in matches:
        return sniffed
    if len(matches) == 1:
        return matches[0]
    if matches:
        # A one-column document looks identical under every candidate.  Comma
        # is the least surprising persisted value in that case.
        return "," if "," in matches else matches[0]
    return sniffed or ","


def _validate_header(
    header: Tuple[str, ...],
    *,
    path_column: str,
    id_column: str,
    display_columns: Optional[Tuple[str, ...]],
) -> Tuple[str, ...]:
    if not header:
        raise CsvSourceError("CSV document is empty or has no header")
    if any(name == "" for name in header):
        raise CsvSourceError("CSV header contains an empty column name")
    duplicates = sorted({name for name in header if header.count(name) > 1})
    if duplicates:
        raise CsvSourceError(
            "CSV header contains duplicate columns: %s" % ", ".join(duplicates)
        )
    if path_column not in header:
        raise CsvSourceError(
            "path_column %r is not an exact CSV column; available: %s"
            % (path_column, ", ".join(repr(name) for name in header))
        )
    if id_column and id_column not in header:
        raise CsvSourceError(
            "id_column %r is not an exact CSV column; available: %s"
            % (id_column, ", ".join(repr(name) for name in header))
        )
    if display_columns is None:
        return tuple(name for name in header if name != path_column)
    if len(display_columns) != len(set(display_columns)):
        raise CsvSourceError("display_columns must not contain duplicates")
    unknown = [name for name in display_columns if name not in header]
    if unknown:
        raise CsvSourceError(
            "unknown display columns: %s" % ", ".join(repr(name) for name in unknown)
        )
    return tuple(display_columns)


def _dataset_namespace(source_path: str) -> str:
    """Return a portable namespace which separates ordinary CSV datasets.

    The basename is intentional: moving a CSV together with its hidden
    sidecars keeps row identities stable across ``K:``, UNC and WSL aliases.
    Runtime generation checks still isolate the rare case of two simultaneously
    visited CSV files with the same basename.
    """

    basename = (
        ntpath.basename(source_path)
        if ntpath.splitdrive(source_path)[0] or source_path.startswith("\\\\")
        else os.path.basename(source_path)
    ).casefold()
    return hashlib.sha256(
        basename.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:16]


def _stable_id(
    dataset_namespace: str, kind: str, value: str, occurrence: int = 0
) -> str:
    payload = "%s\0%s\0%s\0%d" % (
        dataset_namespace,
        kind,
        value,
        occurrence,
    )
    digest = hashlib.sha256(
        payload.encode("utf-8", errors="surrogatepass")
    ).hexdigest()
    return "csv:%s" % digest


def _path_display_name(path: str, row_number: int) -> str:
    value = str(path)
    parts = re.split(r"[/\\]", value.rstrip("/\\"))
    if parts and parts[-1]:
        return parts[-1]
    return "row %d" % row_number


def _parse_csv_stream(
    handle: TextIO, delimiter: str
) -> Tuple[Tuple[str, ...], Tuple[_RawRow, ...]]:
    reader = csv.reader(handle, delimiter=delimiter, strict=True)
    try:
        header = tuple(next(reader))
    except StopIteration:
        return (), ()
    rows: List[_RawRow] = []
    ordinal = 0
    while True:
        row_number = reader.line_num + 1
        try:
            raw = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise CsvSourceError(
                "invalid CSV record near line %d: %s" % (row_number, exc)
            ) from exc
        values = list(raw[: len(header)])
        if len(values) < len(header):
            values.extend([""] * (len(header) - len(values)))
        metadata = {name: values[index] for index, name in enumerate(header)}
        rows.append(
            _RawRow(
                ordinal=ordinal,
                row_number=row_number,
                metadata=metadata,
                raw_values=tuple(raw),
                extra_count=max(0, len(raw) - len(header)),
            )
        )
        ordinal += 1
    return header, tuple(rows)


def _nonempty_id_counts(
    rows: Sequence[_RawRow], id_column: str
) -> Dict[str, int]:
    result: Dict[str, int] = {}
    if not id_column:
        return result
    for row in rows:
        raw_id = row.metadata[id_column]
        if raw_id.strip():
            result[raw_id] = result.get(raw_id, 0) + 1
    return result


def _next_occurrence(counts: Dict[str, int], value: str) -> int:
    occurrence = counts.get(value, 0)
    counts[value] = occurrence + 1
    return occurrence


def _row_errors(
    row: _RawRow,
    *,
    raw_path: str,
    raw_id: str,
    id_column: str,
    id_counts: Dict[str, int],
) -> Tuple[str, ...]:
    errors = []
    if not raw_path.strip():
        errors.append("structure path is empty")
    if row.extra_count:
        errors.append("row has %d extra field(s)" % row.extra_count)
    if id_column and not raw_id.strip():
        errors.append("id column %r is empty" % id_column)
    if id_column and raw_id.strip() and id_counts[raw_id] > 1:
        errors.append("duplicate id value %r in column %r" % (raw_id, id_column))
    return tuple(errors)


def _entry_from_row(
    row: _RawRow,
    *,
    entry_id: str,
    raw_path: str,
    raw_id: str,
    id_column: str,
    source_base: str,
    validation_errors: Tuple[str, ...],
    column_specs: Sequence[ColumnSpec],
) -> AnnotationEntry:
    return AnnotationEntry(
        id=entry_id,
        ordinal=row.ordinal,
        display_name=(
            raw_id
            if id_column and raw_id.strip()
            else _path_display_name(raw_path, row.row_number)
        ),
        source_path=raw_path,
        source_kind="csv",
        source_base=source_base,
        row_number=row.row_number,
        metadata=row.metadata,
        raw_values=row.raw_values,
        display_metadata=build_display_metadata(row.metadata, column_specs),
        availability=(
            AVAILABILITY_UNCHECKED if raw_path.strip() else AVAILABILITY_INVALID
        ),
        validation_errors=validation_errors,
    )


def _build_entries(
    rows: Sequence[_RawRow],
    *,
    dataset_namespace: str,
    path_column: str,
    id_column: str,
    source_base: str,
    column_specs: Sequence[ColumnSpec],
) -> Tuple[AnnotationEntry, ...]:
    id_counts = _nonempty_id_counts(rows, id_column)
    id_occurrences: Dict[str, int] = {}
    path_occurrences: Dict[str, int] = {}
    entries: List[AnnotationEntry] = []
    for row in rows:
        raw_path = row.metadata[path_column]
        raw_id = row.metadata.get(id_column, "") if id_column else ""
        if id_column and raw_id.strip():
            occurrence = _next_occurrence(id_occurrences, raw_id)
            entry_id = _stable_id(dataset_namespace, "id", raw_id, occurrence)
        else:
            occurrence = _next_occurrence(path_occurrences, raw_path)
            entry_id = _stable_id(dataset_namespace, "path", raw_path, occurrence)
        errors = _row_errors(
            row,
            raw_path=raw_path,
            raw_id=raw_id,
            id_column=id_column,
            id_counts=id_counts,
        )
        entries.append(
            _entry_from_row(
                row,
                entry_id=entry_id,
                raw_path=raw_path,
                raw_id=raw_id,
                id_column=id_column,
                source_base=source_base,
                validation_errors=errors,
                column_specs=column_specs,
            )
        )
    return tuple(entries)


class CsvDatasetSource(DatasetSource):
    """Load one CSV row per independently annotatable structure entry."""

    def __init__(
        self,
        csv_path: str,
        path_column: str,
        *,
        id_column: str = "",
        display_columns: Optional[Sequence[str]] = None,
        encoding: str = DEFAULT_ENCODING,
        delimiter: str = AUTO_DELIMITER,
        source_base: str = "",
    ) -> None:
        if not str(csv_path):
            raise CsvSourceError("csv_path is required")
        if not str(path_column):
            raise CsvSourceError("path_column is required")
        if display_columns is not None and isinstance(display_columns, str):
            raise CsvSourceError("display_columns must be a sequence of column names")
        configured_columns = (
            None
            if display_columns is None
            else tuple(str(name) for name in display_columns)
        )
        self.config = CsvSourceConfig(
            csv_path=str(csv_path),
            path_column=str(path_column),
            id_column=str(id_column or ""),
            display_columns=configured_columns,
            encoding=str(encoding or DEFAULT_ENCODING),
            delimiter=_normalize_delimiter(delimiter),
            source_base=str(source_base or ""),
        )

    @classmethod
    def from_config(cls, config: CsvSourceConfig) -> "CsvDatasetSource":
        return cls(
            config.csv_path,
            config.path_column,
            id_column=config.id_column,
            display_columns=config.display_columns,
            encoding=config.encoding,
            delimiter=config.delimiter,
            source_base=config.source_base,
        )

    def _read_sample(self, source_path: str) -> str:
        try:
            with open(
                source_path,
                "r",
                encoding=self.config.encoding,
                newline="",
            ) as handle:
                return handle.read(65536)
        except (OSError, UnicodeError, LookupError) as exc:
            raise CsvSourceError("cannot read CSV %s: %s" % (source_path, exc)) from exc

    def _read_rows(
        self, source_path: str, delimiter: str
    ) -> Tuple[Tuple[str, ...], Tuple[_RawRow, ...]]:
        try:
            with open(
                source_path,
                "r",
                encoding=self.config.encoding,
                newline="",
            ) as handle:
                return _parse_csv_stream(handle, delimiter)
        except CsvSourceError:
            raise
        except (OSError, UnicodeError, LookupError, csv.Error) as exc:
            raise CsvSourceError("cannot parse CSV %s: %s" % (source_path, exc)) from exc

    def load(self) -> AnnotationDataset:
        """Parse the CSV and infer typed display values for selected columns."""

        # abspath is intentional, but expanduser is not: a literal ~/ prefix in
        # a source path belongs to the later remote-path resolver.
        source_path = os.path.abspath(self.config.csv_path)
        sample = self._read_sample(source_path)
        if not sample:
            raise CsvSourceError("CSV document is empty or has no header")
        delimiter = self.config.delimiter
        if delimiter == AUTO_DELIMITER:
            delimiter = _detect_delimiter(sample, self.config.path_column)
        header, rows = self._read_rows(source_path, delimiter)
        display_columns = _validate_header(
            header,
            path_column=self.config.path_column,
            id_column=self.config.id_column,
            display_columns=self.config.display_columns,
        )
        source_base = self.config.source_base or os.path.dirname(source_path)
        specs = build_column_specs_from_metadata(
            tuple(row.metadata for row in rows), display_columns
        )
        entries = _build_entries(
            rows,
            dataset_namespace=_dataset_namespace(source_path),
            path_column=self.config.path_column,
            id_column=self.config.id_column,
            source_base=source_base,
            column_specs=specs,
        )
        return AnnotationDataset(
            source_kind="csv",
            source_path=source_path,
            source_base=source_base,
            entries=entries,
            columns=header,
            display_columns=display_columns,
            column_specs=specs,
            path_column=self.config.path_column,
            id_column=self.config.id_column,
            delimiter=delimiter,
            encoding=self.config.encoding,
        )


def load_csv_dataset(
    csv_path: str,
    path_column: str,
    **kwargs: object,
) -> AnnotationDataset:
    """Convenience function equivalent to ``CsvDatasetSource(...).load()``."""

    return CsvDatasetSource(csv_path, path_column, **kwargs).load()


CSVSource = CsvDatasetSource


__all__ = [
    "AUTO_DELIMITER",
    "CSVSource",
    "DEFAULT_ENCODING",
    "CsvDatasetSource",
    "CsvSourceConfig",
    "CsvSourceError",
    "load_csv_dataset",
]
