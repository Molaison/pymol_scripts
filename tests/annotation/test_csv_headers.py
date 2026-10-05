from io import StringIO
from pathlib import Path
import tempfile
import threading
import unittest

from pymolplugins.annotation.sources.csv_source import (
    CsvDatasetSource, CsvReadCancelled, CsvSourceError,
    _parse_csv_stream, read_csv_header,
)


class CsvHeaderTests(unittest.TestCase):
    def test_header_discovery_does_not_require_path_column_or_valid_data_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scores.tsv"
            path.write_text('\ufeff"structure file"\t"score\nvalue"\n"unfinished', encoding="utf-8")
            header = read_csv_header(str(path))
            self.assertEqual(header["columns"], ("structure file", "score\nvalue"))
            self.assertEqual(header["delimiter"], "\t")

    def test_bad_mapping_is_rejected_before_malformed_or_large_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scores.csv"
            path.write_text('structure_file,score\n"unterminated', encoding="utf-8")
            with self.assertRaisesRegex(CsvSourceError, "path_column.*not an exact"):
                CsvDatasetSource(str(path), "wrong").load()

    def test_parsing_has_cancellation_and_progress_boundaries(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(CsvReadCancelled):
            _parse_csv_stream(StringIO("path,score\na.pdb,1\n"), ",", cancel_event=cancelled)
        progress = []
        header, rows = _parse_csv_stream(StringIO("path,score\na.pdb,1\n"), ",", progress=progress.append)
        self.assertEqual(header, ("path", "score"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(progress[-1], 1)


if __name__ == "__main__":
    unittest.main()
