from dataclasses import asdict
import os
import tempfile
import unittest
from unittest import mock

import pymolplugins.annotation.sources.csv_source as csv_source_module
from pymolplugins.annotation.model import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_INVALID,
    AVAILABILITY_MISSING,
    COLUMN_INTEGER,
    COLUMN_NUMBER,
    COLUMN_TEXT,
    AnnotationDataset,
    AnnotationEntry,
    attach_display_metadata,
    build_column_specs,
    infer_column_kind,
)
from pymolplugins.annotation.sources import (
    CsvDatasetSource,
    CsvSourceError,
    DirectoryDatasetSource,
    DirectorySourceError,
)


class CsvDatasetSourceTests(unittest.TestCase):
    def test_row_ids_are_namespaced_by_portable_csv_identity(self):
        with tempfile.TemporaryDirectory() as root:
            first = os.path.join(root, "first.csv")
            second = os.path.join(root, "second.csv")
            moved = os.path.join(root, "moved", "first.csv")
            os.mkdir(os.path.dirname(moved))
            text = "path,id,score\nmodel.pdb,shared,0.5\n"
            for path in (first, second, moved):
                with open(path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(text)

            first_id = CsvDatasetSource(first, "path", id_column="id").load().entries[0].id
            second_id = CsvDatasetSource(second, "path", id_column="id").load().entries[0].id
            moved_id = CsvDatasetSource(moved, "path", id_column="id").load().entries[0].id

            self.assertNotEqual(first_id, second_id)
            self.assertEqual(first_id, moved_id)

    @staticmethod
    def _write(path, content, encoding="utf-8"):
        with open(path, "w", encoding=encoding, newline="") as handle:
            handle.write(content)

    def test_utf8_bom_auto_delimiter_preserves_order_and_infers_display_values(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "scores.csv")
            self._write(
                path,
                "structure,rank,score,note\n"
                "~/models/a.pdb,01,-1.25e1,first\n"
                "/xcfhome/zpzeng/b.cif,2,3.5,第二\n",
                encoding="utf-8-sig",
            )

            dataset = CsvDatasetSource(path, "structure").load()

            self.assertEqual(dataset.encoding, "utf-8-sig")
            self.assertEqual(dataset.delimiter, ",")
            self.assertEqual(
                dataset.columns, ("structure", "rank", "score", "note")
            )
            self.assertEqual(dataset.display_columns, ("rank", "score", "note"))
            self.assertEqual(dataset.column_spec("rank").kind, COLUMN_INTEGER)
            self.assertEqual(dataset.column_spec("score").kind, COLUMN_NUMBER)
            self.assertEqual(dataset.column_spec("note").kind, COLUMN_TEXT)
            first = dataset.entries[0]
            self.assertEqual(first.source_path, "~/models/a.pdb")
            self.assertEqual(first.raw_path, "~/models/a.pdb")
            self.assertEqual(first.source_base, root)
            self.assertEqual(
                list(first.metadata), ["structure", "rank", "score", "note"]
            )
            self.assertEqual(first.metadata["rank"], "01")
            self.assertEqual(first.metadata["score"], "-1.25e1")
            self.assertEqual(
                first.display_metadata,
                {"rank": 1, "score": -12.5, "note": "first"},
            )
            self.assertEqual(dataset.entries[1].metadata["note"], "第二")

    def test_auto_detects_semicolon_and_explicit_tab_name(self):
        with tempfile.TemporaryDirectory() as root:
            semicolon = os.path.join(root, "semi.csv")
            tab = os.path.join(root, "tab.tsv")
            self._write(semicolon, "path;score\na.pdb;1.5\n")
            self._write(tab, "path\tscore\nb.pdb\t2.5\n")

            semi_dataset = CsvDatasetSource(semicolon, "path").load()
            tab_dataset = CsvDatasetSource(
                tab, "path", delimiter="tab"
            ).load()

            self.assertEqual(semi_dataset.delimiter, ";")
            self.assertEqual(semi_dataset.entries[0].metadata["score"], "1.5")
            self.assertEqual(tab_dataset.delimiter, "\t")
            self.assertEqual(tab_dataset.entries[0].source_path, "b.pdb")

    def test_path_and_column_names_are_exact_not_fuzzy_or_trimmed(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "exact.csv")
            self._write(path, " Structure Path ,Score\na.pdb,1\n")

            with self.assertRaisesRegex(CsvSourceError, "not an exact CSV column"):
                CsvDatasetSource(path, "structure path").load()
            dataset = CsvDatasetSource(path, " Structure Path ").load()
            self.assertEqual(dataset.entries[0].source_path, "a.pdb")

    def test_relative_and_remote_paths_stay_byte_for_byte_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "paths.csv")
            values = (
                "../models/a.pdb",
                "~/models/b.pdb",
                "/xcfhome/zpzeng/models/c.pdb",
                r"K:\models\d.pdb",
            )
            self._write(path, "path\n" + "\n".join(values) + "\n")

            dataset = CsvDatasetSource(path, "path").load()

            self.assertEqual(
                tuple(entry.source_path for entry in dataset.entries), values
            )
            self.assertEqual(dataset.source_base, root)
            self.assertTrue(all(not entry.resolved_path for entry in dataset.entries))

    def test_duplicate_paths_are_independent_and_ids_survive_metadata_changes(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "duplicates.csv")
            self._write(
                path,
                "path,score\nsame.pdb,1\nother.pdb,2\nsame.pdb,3\n",
            )
            first = CsvDatasetSource(path, "path").load()
            self._write(
                path,
                "path,score\nsame.pdb,100\nother.pdb,200\nsame.pdb,300\n",
            )
            second = CsvDatasetSource(path, "path").load()

            self.assertEqual(first.count, 3)
            self.assertEqual(len({entry.id for entry in first.entries}), 3)
            self.assertNotEqual(first.entries[0].id, first.entries[2].id)
            self.assertEqual(
                tuple(entry.id for entry in first.entries),
                tuple(entry.id for entry in second.entries),
            )

    def test_unique_id_column_is_stable_across_reordering(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "ids.csv")
            self._write(path, "uid,path,score\na,a.pdb,1\nb,b.pdb,2\n")
            first = CsvDatasetSource(path, "path", id_column="uid").load()
            self._write(path, "uid,path,score\nb,b.pdb,20\na,a.pdb,10\n")
            second = CsvDatasetSource(path, "path", id_column="uid").load()

            first_ids = {entry.metadata["uid"]: entry.id for entry in first.entries}
            second_ids = {entry.metadata["uid"]: entry.id for entry in second.entries}
            self.assertEqual(first_ids, second_ids)
            self.assertEqual(second.entries[0].display_name, "b")

    def test_duplicate_and_empty_ids_remain_unique_error_rows(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "bad_ids.csv")
            self._write(
                path,
                "uid,path\ndup,a.pdb\ndup,b.pdb\n,c.pdb\n",
            )

            dataset = CsvDatasetSource(path, "path", id_column="uid").load()

            self.assertEqual(len({entry.id for entry in dataset.entries}), 3)
            self.assertIn("duplicate id value", dataset.entries[0].error)
            self.assertIn("duplicate id value", dataset.entries[1].error)
            self.assertIn("id column 'uid' is empty", dataset.entries[2].error)
            self.assertEqual(dataset.entries[2].display_name, "c.pdb")

    def test_missing_path_is_retained_as_invalid_row(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "missing.csv")
            self._write(path, "path,score\n,4.2\nok.pdb,5.0\n")

            dataset = CsvDatasetSource(path, "path").load()

            self.assertEqual(dataset.count, 2)
            missing = dataset.entries[0]
            self.assertEqual(missing.availability, AVAILABILITY_INVALID)
            self.assertFalse(missing.is_navigable)
            self.assertIn("structure path is empty", missing.error)
            self.assertEqual(dataset.error_entries, (missing,))

    def test_quoted_newline_has_physical_start_line_and_raw_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "multiline.csv")
            self._write(
                path,
                'path,note\na.pdb,"first line\nsecond line"\nb.pdb,plain\n',
            )

            dataset = CsvDatasetSource(path, "path").load()

            self.assertEqual(dataset.entries[0].row_number, 2)
            self.assertEqual(dataset.entries[1].row_number, 4)
            self.assertEqual(
                dataset.entries[0].metadata["note"], "first line\nsecond line"
            )

    def test_short_rows_are_padded_and_extra_fields_are_preserved_with_error(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "ragged.csv")
            self._write(path, "path,score,note\na.pdb,1\nb.pdb,2,ok,EXTRA\n")

            dataset = CsvDatasetSource(path, "path").load()

            self.assertEqual(dataset.entries[0].metadata["note"], "")
            self.assertEqual(dataset.entries[0].raw_values, ("a.pdb", "1"))
            self.assertEqual(
                dataset.entries[1].raw_values, ("b.pdb", "2", "ok", "EXTRA")
            )
            self.assertIn("1 extra field", dataset.entries[1].error)

    def test_display_columns_can_be_ordered_hidden_or_empty(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "display.csv")
            self._write(path, "path,a,b\nx.pdb,1,2.5\n")

            ordered = CsvDatasetSource(
                path, "path", display_columns=("b", "a")
            ).load()
            hidden = CsvDatasetSource(path, "path", display_columns=()).load()

            self.assertEqual(ordered.display_columns, ("b", "a"))
            self.assertEqual(list(ordered.entries[0].display_metadata), ["b", "a"])
            self.assertEqual(hidden.display_columns, ())
            self.assertEqual(hidden.entries[0].display_metadata, {})
            with self.assertRaisesRegex(CsvSourceError, "unknown display columns"):
                CsvDatasetSource(path, "path", display_columns=("missing",)).load()
            with self.assertRaisesRegex(CsvSourceError, "must be a sequence"):
                CsvDatasetSource(path, "path", display_columns="a")

    def test_csv_constructs_each_final_entry_once_after_column_inference(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "single construction.csv")
            self._write(
                path,
                "uid,path,score,note\n"
                "dup,a.pdb,1.5,first\n"
                "dup,b.pdb,2.5,second,EXTRA\n"
                ",,3.5,third\n",
            )
            constructed_ids = []
            original_post_init = AnnotationEntry.__post_init__

            def counting_post_init(entry):
                constructed_ids.append(entry.id)
                original_post_init(entry)

            with mock.patch.object(
                AnnotationEntry, "__post_init__", counting_post_init
            ):
                dataset = CsvDatasetSource(
                    path,
                    "path",
                    id_column="uid",
                    display_columns=("score", "note"),
                ).load()

            self.assertEqual(
                constructed_ids, [entry.id for entry in dataset.entries]
            )
            self.assertEqual(dataset.count, 3)
            self.assertEqual(
                dataset.entries[0].display_metadata,
                {"score": 1.5, "note": "first"},
            )
            self.assertIn("duplicate id value", dataset.entries[0].error)
            self.assertIn("1 extra field", dataset.entries[1].error)
            self.assertIn("structure path is empty", dataset.entries[2].error)
            self.assertIn("id column 'uid' is empty", dataset.entries[2].error)

    def test_single_construction_matches_legacy_attach_pipeline(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "legacy parity.csv")
            self._write(
                path,
                "uid,path,score,note\n"
                "dup,a.pdb,1,alpha\n"
                ",b.pdb,,beta\n"
                "dup,c.pdb,2.5,gamma,EXTRA\n",
            )
            source = CsvDatasetSource(
                path,
                "path",
                id_column="uid",
                display_columns=("score", "note"),
            )
            actual = source.load()
            _header, rows = source._read_rows(path, ",")
            legacy_entries = csv_source_module._build_entries(
                rows,
                dataset_namespace=csv_source_module._dataset_namespace(path),
                path_column="path",
                id_column="uid",
                source_base=root,
                column_specs=(),
            )
            legacy_specs = build_column_specs(
                legacy_entries, ("score", "note")
            )
            legacy_entries = attach_display_metadata(legacy_entries, legacy_specs)
            legacy = AnnotationDataset(
                source_kind="csv",
                source_path=os.path.abspath(path),
                source_base=root,
                entries=legacy_entries,
                columns=("uid", "path", "score", "note"),
                display_columns=("score", "note"),
                column_specs=legacy_specs,
                path_column="path",
                id_column="uid",
                delimiter=",",
                encoding="utf-8-sig",
            )

            self.assertEqual(asdict(actual), asdict(legacy))

    def test_empty_duplicate_headers_and_malformed_csv_fail_atomically(self):
        with tempfile.TemporaryDirectory() as root:
            empty = os.path.join(root, "empty.csv")
            duplicate = os.path.join(root, "duplicate.csv")
            blank = os.path.join(root, "blank.csv")
            malformed = os.path.join(root, "malformed.csv")
            self._write(empty, "")
            self._write(duplicate, "path,path\na.pdb,b.pdb\n")
            self._write(blank, "path,\na.pdb,1\n")
            self._write(malformed, 'path,note\na.pdb,"never closed\n')

            with self.assertRaisesRegex(CsvSourceError, "empty or has no header"):
                CsvDatasetSource(empty, "path").load()
            with self.assertRaisesRegex(CsvSourceError, "duplicate columns"):
                CsvDatasetSource(duplicate, "path", delimiter=",").load()
            with self.assertRaisesRegex(CsvSourceError, "empty column name"):
                CsvDatasetSource(blank, "path").load()
            with self.assertRaisesRegex(CsvSourceError, "invalid CSV record"):
                CsvDatasetSource(malformed, "path", delimiter=",").load()

    def test_invalid_delimiter_and_missing_id_column_are_clear(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "simple.csv")
            self._write(path, "path\na.pdb\n")

            with self.assertRaisesRegex(CsvSourceError, "delimiter must"):
                CsvDatasetSource(path, "path", delimiter="::")
            with self.assertRaisesRegex(CsvSourceError, "id_column.*not an exact"):
                CsvDatasetSource(path, "path", id_column="uid").load()


class DirectoryDatasetSourceTests(unittest.TestCase):
    @staticmethod
    def _touch(path, content="END\n"):
        with open(path, "w", encoding="ascii") as handle:
            handle.write(content)

    def test_discovers_structures_prefers_gzip_and_ignores_nested_files(self):
        with tempfile.TemporaryDirectory() as root:
            self._touch(os.path.join(root, "model.pdb"))
            self._touch(os.path.join(root, "model.pdb.gz"))
            self._touch(os.path.join(root, "OTHER.CIF"))
            self._touch(os.path.join(root, "notes.txt"))
            nested = os.path.join(root, "nested")
            os.mkdir(nested)
            self._touch(os.path.join(nested, "hidden.pdb"))

            dataset = DirectoryDatasetSource(root).load()

            self.assertEqual(dataset.source_kind, "directory")
            self.assertEqual(
                tuple(entry.display_name for entry in dataset.entries),
                ("model.pdb.gz", "OTHER.CIF"),
            )
            self.assertTrue(
                all(entry.availability == AVAILABILITY_AVAILABLE for entry in dataset.entries)
            )
            self.assertTrue(
                all(entry.resolved_path == entry.source_path for entry in dataset.entries)
            )

    def test_uncompressed_preference_and_stable_relative_ids(self):
        with (
            tempfile.TemporaryDirectory() as first_root,
            tempfile.TemporaryDirectory() as second_root,
        ):
            for root in (first_root, second_root):
                self._touch(os.path.join(root, "same.pdb"))
                self._touch(os.path.join(root, "same.pdb.gz"))

            first = DirectoryDatasetSource(first_root, prefer_gz=False).load()
            second = DirectoryDatasetSource(second_root, prefer_gz=False).load()

            self.assertEqual(first.entries[0].display_name, "same.pdb")
            self.assertEqual(first.entries[0].id, second.entries[0].id)
            self.assertEqual(first.entries[0].metadata["compressed"], "false")

    def test_directory_display_metadata_and_errors(self):
        with tempfile.TemporaryDirectory() as root:
            self._touch(os.path.join(root, "x.pdb"))
            dataset = DirectoryDatasetSource(
                root, display_columns=("format", "compressed")
            ).load()
            self.assertEqual(
                dataset.entries[0].display_metadata,
                {"format": "pdb", "compressed": "false"},
            )
            with self.assertRaisesRegex(DirectorySourceError, "unknown display"):
                DirectoryDatasetSource(root, display_columns=("score",))
            with self.assertRaisesRegex(DirectorySourceError, "invalid structure pattern"):
                DirectoryDatasetSource(root, pattern="[").load()
        with self.assertRaisesRegex(DirectorySourceError, "does not exist"):
            DirectoryDatasetSource("/definitely/not/a/dataset").load()


class AnnotationModelTests(unittest.TestCase):
    def test_numeric_inference_is_conservative_and_finite(self):
        self.assertEqual(infer_column_kind(["", "01", "-2"]), COLUMN_INTEGER)
        self.assertEqual(infer_column_kind(["9" * 400]), COLUMN_INTEGER)
        self.assertEqual(infer_column_kind(["1", "2.5", "3e2"]), COLUMN_NUMBER)
        self.assertEqual(infer_column_kind(["1", "nan"]), COLUMN_TEXT)
        self.assertEqual(infer_column_kind(["1", "inf"]), COLUMN_TEXT)
        self.assertEqual(infer_column_kind(["", "  "]), COLUMN_TEXT)
        self.assertEqual(infer_column_kind(["1", "bad"]), COLUMN_TEXT)

    def test_with_resolution_accepts_duck_typed_result_without_losing_row_error(self):
        class Resolution:
            resolved_path = "K:\\models\\a.pdb"
            status = AVAILABILITY_MISSING
            diagnostics = ("mapped candidate does not exist",)

        entry = AnnotationEntry(
            id="entry",
            ordinal=0,
            display_name="a",
            source_path="~/a.pdb",
            source_kind="csv",
            validation_errors=("duplicate id",),
        )

        resolved = entry.with_resolution(Resolution())

        self.assertEqual(resolved.source_path, "~/a.pdb")
        self.assertEqual(resolved.resolved_path, r"K:\models\a.pdb")
        self.assertEqual(resolved.availability, AVAILABILITY_MISSING)
        self.assertIn("duplicate id", resolved.error)
        self.assertIn("mapped candidate does not exist", resolved.error)
        self.assertIsInstance(resolved.path_resolution, Resolution)
        self.assertFalse(resolved.is_navigable)
        self.assertIs(resolved.with_resolution().path_resolution, resolved.path_resolution)


if __name__ == "__main__":
    unittest.main()
