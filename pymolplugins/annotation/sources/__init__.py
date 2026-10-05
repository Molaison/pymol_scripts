"""Dataset source adapters for Structure Annotator."""

from .csv_source import (
    AUTO_DELIMITER,
    CSVSource,
    DEFAULT_ENCODING,
    CsvDatasetSource,
    CsvSourceConfig,
    CsvSourceError,
    load_csv_dataset,
)
from .directory import (
    DEFAULT_STRUCTURE_PATTERN,
    DIRECTORY_COLUMNS,
    DirectoryDatasetSource,
    DirectorySource,
    DirectorySourceConfig,
    DirectorySourceError,
    directory_entry_id,
    load_directory_dataset,
)


__all__ = [
    "AUTO_DELIMITER",
    "CSVSource",
    "DEFAULT_ENCODING",
    "DEFAULT_STRUCTURE_PATTERN",
    "DIRECTORY_COLUMNS",
    "CsvDatasetSource",
    "CsvSourceConfig",
    "CsvSourceError",
    "DirectoryDatasetSource",
    "DirectorySource",
    "DirectorySourceConfig",
    "DirectorySourceError",
    "directory_entry_id",
    "load_csv_dataset",
    "load_directory_dataset",
]
