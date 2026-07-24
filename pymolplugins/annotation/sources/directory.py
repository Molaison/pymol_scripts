"""Directory-backed Structure Annotator dataset source."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import re
from typing import Dict, List, Pattern, Sequence, Tuple

from pymolplugins.annotation.model import (
    AVAILABILITY_AVAILABLE,
    AnnotationDataset,
    AnnotationEntry,
    DatasetSource,
    DatasetSourceError,
    attach_display_metadata,
    build_column_specs,
)


DEFAULT_STRUCTURE_PATTERN = r"^.+\.(?:pdb|cif)(?:\.gz)?$"
DIRECTORY_COLUMNS = (
    "structure_path",
    "name",
    "relative_path",
    "format",
    "compressed",
)


class DirectorySourceError(DatasetSourceError):
    """Raised when a structure directory cannot be discovered."""


@dataclass(frozen=True)
class DirectorySourceConfig:
    directory: str
    pattern: str = DEFAULT_STRUCTURE_PATTERN
    prefer_gz: bool = True
    display_columns: Tuple[str, ...] = ()


def _dedupe_key(path: str) -> str:
    name = os.path.basename(path).lower()
    if name.endswith(".gz"):
        name = name[:-3]
    for extension in (".pdb", ".cif"):
        if name.endswith(extension):
            return name[: -len(extension)]
    return name


def _format_name(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".gz"):
        lower = lower[:-3]
    if lower.endswith(".pdb"):
        return "pdb"
    if lower.endswith(".cif"):
        return "cif"
    return ""


def _entry_id(relative_path: str) -> str:
    canonical = os.path.normcase(relative_path).replace("\\", "/")
    digest = hashlib.sha256(
        canonical.encode("utf-8", errors="surrogatepass")
    ).hexdigest()
    return "directory:%s" % digest


class DirectoryDatasetSource(DatasetSource):
    """Discover top-level PDB/mmCIF structures with legacy gzip preference."""

    def __init__(
        self,
        directory: str,
        *,
        pattern: str = DEFAULT_STRUCTURE_PATTERN,
        prefer_gz: bool = True,
        display_columns: Sequence[str] = (),
    ) -> None:
        if not str(directory):
            raise DirectorySourceError("directory is required")
        if isinstance(display_columns, str):
            raise DirectorySourceError(
                "display_columns must be a sequence of column names"
            )
        requested_columns = tuple(str(name) for name in display_columns)
        if len(requested_columns) != len(set(requested_columns)):
            raise DirectorySourceError("display_columns must not contain duplicates")
        unknown = [name for name in requested_columns if name not in DIRECTORY_COLUMNS]
        if unknown:
            raise DirectorySourceError(
                "unknown display columns: %s" % ", ".join(repr(name) for name in unknown)
            )
        self.config = DirectorySourceConfig(
            directory=str(directory),
            pattern=str(pattern or DEFAULT_STRUCTURE_PATTERN),
            prefer_gz=bool(prefer_gz),
            display_columns=requested_columns,
        )

    @classmethod
    def from_config(cls, config: DirectorySourceConfig) -> "DirectoryDatasetSource":
        return cls(
            config.directory,
            pattern=config.pattern,
            prefer_gz=config.prefer_gz,
            display_columns=config.display_columns,
        )

    def _compiled_pattern(self) -> Pattern[str]:
        try:
            return re.compile(self.config.pattern, re.IGNORECASE)
        except re.error as exc:
            raise DirectorySourceError("invalid structure pattern: %s" % exc) from exc

    def _discover(self, root: str, pattern: Pattern[str]) -> Tuple[str, ...]:
        try:
            names = sorted(os.listdir(root), key=lambda value: value.lower())
        except OSError as exc:
            raise DirectorySourceError("cannot list directory %s: %s" % (root, exc)) from exc
        selected: Dict[str, str] = {}
        for name in names:
            if not pattern.match(name):
                continue
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            key = _dedupe_key(path)
            existing = selected.get(key)
            if existing is None:
                selected[key] = path
                continue
            candidate_gz = path.lower().endswith(".gz")
            existing_gz = existing.lower().endswith(".gz")
            if self.config.prefer_gz and candidate_gz and not existing_gz:
                selected[key] = path
            elif not self.config.prefer_gz and not candidate_gz and existing_gz:
                selected[key] = path
        return tuple(sorted(selected.values(), key=lambda value: value.lower()))

    def load(self) -> AnnotationDataset:
        root = os.path.abspath(os.path.expanduser(self.config.directory))
        if not os.path.isdir(root):
            raise DirectorySourceError("structure directory does not exist: %s" % root)
        paths = self._discover(root, self._compiled_pattern())
        entries: List[AnnotationEntry] = []
        for ordinal, path in enumerate(paths):
            relative = os.path.relpath(path, root)
            metadata = {
                "structure_path": path,
                "name": os.path.basename(path),
                "relative_path": relative,
                "format": _format_name(path),
                "compressed": "true" if path.lower().endswith(".gz") else "false",
            }
            entries.append(
                AnnotationEntry(
                    id=_entry_id(relative),
                    ordinal=ordinal,
                    display_name=os.path.basename(path),
                    source_path=path,
                    source_kind="directory",
                    source_base=root,
                    metadata=metadata,
                    raw_values=tuple(metadata.values()),
                    resolved_path=path,
                    path_resolution="directory",
                    availability=AVAILABILITY_AVAILABLE,
                )
            )
        specs = build_column_specs(entries, self.config.display_columns)
        attached = attach_display_metadata(entries, specs)
        return AnnotationDataset(
            source_kind="directory",
            source_path=root,
            source_base=root,
            entries=attached,
            columns=DIRECTORY_COLUMNS,
            display_columns=self.config.display_columns,
            column_specs=specs,
            path_column="structure_path",
        )


def load_directory_dataset(directory: str, **kwargs: object) -> AnnotationDataset:
    """Convenience function equivalent to ``DirectoryDatasetSource(...).load()``."""

    return DirectoryDatasetSource(directory, **kwargs).load()


DirectorySource = DirectoryDatasetSource


__all__ = [
    "DEFAULT_STRUCTURE_PATTERN",
    "DIRECTORY_COLUMNS",
    "DirectoryDatasetSource",
    "DirectorySource",
    "DirectorySourceConfig",
    "DirectorySourceError",
    "load_directory_dataset",
]
