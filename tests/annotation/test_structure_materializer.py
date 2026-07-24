import gzip
import json
import os
import tempfile
import unittest

from pymolplugins.annotation.cache import PersistentStructureCache, SourceIdentity
from pymolplugins.annotation.materializer import (
    CopyMaterializer,
    DirectMaterializer,
    MaterializationError,
)


class StructureMaterializerTests(unittest.TestCase):
    @staticmethod
    def _write(path, content):
        with open(path, "wb") as handle:
            handle.write(content)

    def test_direct_materializer_keeps_host_path_and_does_not_create_cache(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "model.cif")
            self._write(source, b"data_model\n")

            result = DirectMaterializer().materialize(source)

            self.assertEqual(result.path, os.path.abspath(source))
            self.assertEqual(result.source_path, os.path.abspath(source))
            self.assertEqual(result.mode, "direct")
            self.assertIsNone(result.cache_key)
            self.assertFalse(result.cache_hit)

    def test_copy_materializer_preserves_remote_identity_in_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "mapped.pdb")
            self._write(source, b"ATOM\nEND\n")
            cache = PersistentStructureCache(os.path.join(root, "cache"))

            result = CopyMaterializer(cache).materialize(
                source,
                provider="sshfs-win",
                canonical_source="/xcfhome/zpzeng/project/mapped.pdb",
            )

            self.assertEqual(result.mode, "copy")
            self.assertFalse(result.cache_hit)
            self.assertNotEqual(result.path, source)
            with open(result.path, "rb") as handle:
                self.assertEqual(handle.read(), b"ATOM\nEND\n")

            with open(cache.manifest_path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
            source_identity = document["entries"][result.cache_key][
                "source_identity"
            ]
            self.assertEqual(source_identity["provider"], "sshfs-win")
            self.assertEqual(
                source_identity["canonical_source"],
                "/xcfhome/zpzeng/project/mapped.pdb",
            )

    def test_gzip_is_decompressed_once_into_persistent_cache(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "model.pdb.gz")
            content = b"ATOM      1  CA  ALA A   1\nEND\n"
            with gzip.open(source, "wb") as handle:
                handle.write(content)
            cache_root = os.path.join(root, "cache")
            cache = PersistentStructureCache(cache_root)
            materializer = CopyMaterializer(cache)

            cold = materializer.materialize(source)
            warm = materializer.materialize(source)
            reopened = PersistentStructureCache(cache_root)
            persisted = CopyMaterializer(reopened).materialize(source)

            self.assertEqual(cold.mode, "decompress")
            self.assertTrue(cold.path.endswith(".pdb"))
            self.assertFalse(cold.cache_hit)
            self.assertTrue(warm.cache_hit)
            self.assertTrue(persisted.cache_hit)
            self.assertEqual(cold.path, warm.path)
            self.assertEqual(cold.path, persisted.path)
            with open(cold.path, "rb") as handle:
                self.assertEqual(handle.read(), content)

    def test_staged_copy_uses_remote_identity_not_download_file_mtime(self):
        with tempfile.TemporaryDirectory() as root:
            downloaded = os.path.join(root, "downloaded.pdb")
            self._write(downloaded, b"ATOM\nEND\n")
            local_mtime = os.stat(downloaded).st_mtime_ns
            remote_identity = SourceIdentity(
                provider="wsl-stage",
                canonical_source="root-alias/project/model.pdb",
                size=os.path.getsize(downloaded),
                mtime_ns=local_mtime - 123456,
            )
            cache = PersistentStructureCache(os.path.join(root, "cache"))

            result = CopyMaterializer(cache).materialize(
                downloaded, source_identity=remote_identity
            )

            self.assertEqual(result.identity, remote_identity)
            self.assertEqual(result.cache_key, remote_identity.cache_key)
            self.assertNotEqual(result.identity.mtime_ns, local_mtime)
            with open(result.path, "rb") as handle:
                self.assertEqual(handle.read(), b"ATOM\nEND\n")

            mismatched = SourceIdentity(
                provider="wsl-stage",
                canonical_source="root-alias/project/model.pdb",
                size=remote_identity.size + 1,
                mtime_ns=remote_identity.mtime_ns,
            )
            with self.assertRaisesRegex(MaterializationError, "size does not match"):
                CopyMaterializer(cache).materialize(
                    downloaded, source_identity=mismatched
                )

    def test_opaque_staging_name_uses_logical_suffix_and_gzip_transform(self):
        with tempfile.TemporaryDirectory() as root:
            downloaded = os.path.join(root, "opaque-download")
            content = b"ATOM\nEND\n"
            with gzip.open(downloaded, "wb") as handle:
                handle.write(content)
            stat_result = os.stat(downloaded)
            identity = SourceIdentity(
                provider="wsl-stage",
                canonical_source="root-alias/project/model.pdb.gz",
                size=stat_result.st_size,
                mtime_ns=1234,
            )
            cache = PersistentStructureCache(os.path.join(root, "cache"))

            result = CopyMaterializer(cache).materialize(
                downloaded,
                source_identity=identity,
                logical_path="/xcfhome/zpzeng/project/model.pdb.gz",
            )

            self.assertEqual(result.mode, "decompress")
            self.assertTrue(result.path.endswith(".pdb"))
            with open(result.path, "rb") as handle:
                self.assertEqual(handle.read(), content)

    def test_gzip_failure_does_not_publish_partial_output(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "broken.pdb.gz")
            self._write(source, b"not a gzip stream")
            cache = PersistentStructureCache(os.path.join(root, "cache"))

            with self.assertRaises((gzip.BadGzipFile, EOFError)):
                CopyMaterializer(cache).materialize(source)

            self.assertEqual(cache.status()["entry_count"], 0)
            for directory, _, filenames in os.walk(cache.root):
                self.assertFalse(
                    any(name.endswith(".part") for name in filenames), directory
                )


if __name__ == "__main__":
    unittest.main()
