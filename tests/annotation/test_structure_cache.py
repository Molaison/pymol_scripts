import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from pymolplugins.annotation.cache import (
    CACHE_DIRECTORY_ENVIRONMENT_VARIABLE,
    InvalidSourceError,
    PersistentStructureCache,
    SourceIdentity,
    default_structure_cache_dir,
)
from pymolplugins.annotation.materializer import CopyMaterializer


class _TickClock:
    def __init__(self):
        self.value = 1000
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.value += 1
            return self.value


class PersistentStructureCacheTests(unittest.TestCase):
    @staticmethod
    def _write(path, content):
        with open(path, "wb") as handle:
            handle.write(content)

    def test_default_directory_prefers_localappdata_and_has_portable_fallback(self):
        windows = default_structure_cache_dir(
            environment={"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"},
            platform_name="win32",
        )
        self.assertEqual(
            windows,
            r"C:\Users\Test\AppData\Local\PymolPlugins\structure-cache\v1",
        )
        linux = default_structure_cache_dir(
            environment={"HOME": "/home/test"}, platform_name="linux"
        )
        self.assertEqual(
            linux, "/home/test/.cache/pymolplugins/structure-cache/v1"
        )
        overridden = default_structure_cache_dir(
            environment={
                "HOME": "/home/test",
                CACHE_DIRECTORY_ENVIRONMENT_VARIABLE: "~/special-cache",
            },
            platform_name="linux",
        )
        self.assertEqual(overridden, "/home/test/special-cache")

    def test_identity_key_includes_provider_canonical_source_version_and_optional_hash(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "model.pdb")
            self._write(source, b"AAAA")
            fixed_ns = 1_700_000_000_000_000_000
            os.utime(source, ns=(fixed_ns, fixed_ns))
            plain = SourceIdentity.from_path(
                source,
                provider="sshfs",
                canonical_source="/xcfhome/zpzeng/model.pdb",
            )
            strict_a = SourceIdentity.from_path(
                source,
                provider="sshfs",
                canonical_source="/xcfhome/zpzeng/model.pdb",
                include_sha256=True,
            )
            self._write(source, b"BBBB")
            os.utime(source, ns=(fixed_ns, fixed_ns))
            same_stat = SourceIdentity.from_path(
                source,
                provider="sshfs",
                canonical_source="/xcfhome/zpzeng/model.pdb",
            )
            strict_b = SourceIdentity.from_path(
                source,
                provider="sshfs",
                canonical_source="/xcfhome/zpzeng/model.pdb",
                include_sha256=True,
            )

            self.assertEqual(plain.cache_key, same_stat.cache_key)
            self.assertNotEqual(strict_a.cache_key, strict_b.cache_key)

            version_a = SourceIdentity(
                provider=plain.provider,
                canonical_source=plain.canonical_source,
                size=plain.size,
                mtime_ns=plain.mtime_ns,
                version_token="v1-remote-a",
            )
            version_b = SourceIdentity(
                provider=plain.provider,
                canonical_source=plain.canonical_source,
                size=plain.size,
                mtime_ns=plain.mtime_ns,
                version_token="v1-remote-b",
            )
            self.assertNotEqual(version_a.cache_key, version_b.cache_key)
            self.assertEqual(version_a.as_dict()["version_token"], "v1-remote-a")
            self.assertNotEqual(
                plain.cache_key,
                SourceIdentity(
                    provider="wsl",
                    canonical_source=plain.canonical_source,
                    size=plain.size,
                    mtime_ns=plain.mtime_ns,
                ).cache_key,
            )
            with self.assertRaisesRegex(InvalidSourceError, "source size"):
                SourceIdentity(
                    provider="file",
                    canonical_source=source,
                    size=-1,
                    mtime_ns=plain.mtime_ns,
                )
            self.assertNotEqual(
                plain.cache_key,
                SourceIdentity(
                    provider=plain.provider,
                    canonical_source="/other/model.pdb",
                    size=plain.size,
                    mtime_ns=plain.mtime_ns,
                ).cache_key,
            )

    def test_cold_warm_and_changed_source_have_expected_cache_behavior(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "model.pdb")
            self._write(source, b"MODEL-ONE")
            cache = PersistentStructureCache(os.path.join(root, "cache"))
            materializer = CopyMaterializer(cache)

            cold = materializer.materialize(source)
            warm = materializer.materialize(source)
            self._write(source, b"MODEL-TWO")  # same size; mtime alone is stale
            changed_ns = cold.identity.mtime_ns + 1_000_000_000
            os.utime(source, ns=(changed_ns, changed_ns))
            changed = materializer.materialize(source)

            self.assertFalse(cold.cache_hit)
            self.assertTrue(warm.cache_hit)
            self.assertEqual(cold.path, warm.path)
            self.assertTrue(changed.path != cold.path)
            self.assertFalse(changed.cache_hit)
            with open(changed.path, "rb") as handle:
                self.assertEqual(handle.read(), b"MODEL-TWO")

    def test_failed_writer_leaves_no_partial_or_visible_cache_entry(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source.pdb")
            self._write(source, b"source")
            cache = PersistentStructureCache(os.path.join(root, "cache"))
            identity = SourceIdentity.from_path(source)

            def fail(temporary):
                self._write(temporary, b"partial")
                raise RuntimeError("intentional failure")

            with self.assertRaisesRegex(RuntimeError, "intentional failure"):
                cache.get_or_create(identity, suffix=".pdb", writer=fail)

            self.assertIsNone(cache.lookup(identity))
            partials = []
            for directory, _, filenames in os.walk(cache.root):
                partials.extend(
                    os.path.join(directory, name)
                    for name in filenames
                    if name.endswith(".part")
                )
            self.assertEqual(partials, [])
            self.assertEqual(cache.status()["entry_count"], 0)

    def test_concurrent_same_key_runs_writer_once(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source.pdb")
            self._write(source, b"source")
            cache = PersistentStructureCache(os.path.join(root, "cache"))
            identity = SourceIdentity.from_path(source)
            started = threading.Event()
            release = threading.Event()
            count_lock = threading.Lock()
            calls = []

            def writer(temporary):
                with count_lock:
                    calls.append(temporary)
                started.set()
                self.assertTrue(release.wait(5.0))
                self._write(temporary, b"cached")

            def request():
                return cache.get_or_create(
                    identity, suffix=".pdb", writer=writer
                )

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(request) for _ in range(8)]
                self.assertTrue(started.wait(5.0))
                release.set()
                results = [future.result(timeout=5.0) for future in futures]

            self.assertEqual(len(calls), 1)
            self.assertEqual(len({result.path for result in results}), 1)
            self.assertEqual(sum(not result.cache_hit for result in results), 1)
            self.assertEqual(sum(result.cache_hit for result in results), 7)

    def test_lru_uses_access_time_and_enforces_item_and_byte_limits(self):
        with tempfile.TemporaryDirectory() as root:
            clock = _TickClock()
            cache = PersistentStructureCache(
                os.path.join(root, "item-cache"),
                max_items=2,
                max_bytes=None,
                clock_ns=clock,
            )
            materializer = CopyMaterializer(cache)
            paths = []
            for name in ("a.pdb", "b.pdb", "c.pdb"):
                path = os.path.join(root, name)
                self._write(path, name.encode("ascii"))
                paths.append(path)
            first = materializer.materialize(paths[0])
            second = materializer.materialize(paths[1])
            materializer.materialize(paths[0])  # make a newer than b
            third = materializer.materialize(paths[2])

            self.assertTrue(os.path.exists(first.path))
            self.assertFalse(os.path.exists(second.path))
            self.assertTrue(os.path.exists(third.path))
            self.assertEqual(cache.status()["entry_count"], 2)

            byte_cache = PersistentStructureCache(
                os.path.join(root, "byte-cache"),
                max_items=None,
                max_bytes=6,
                clock_ns=clock,
            )
            byte_materializer = CopyMaterializer(byte_cache)
            one = byte_materializer.materialize(paths[0])
            two = byte_materializer.materialize(paths[1])
            self.assertFalse(os.path.exists(one.path))
            self.assertTrue(os.path.exists(two.path))
            self.assertLessEqual(byte_cache.status()["total_bytes"], 6)

    def test_current_and_preload_pins_survive_prune_and_normal_clear(self):
        with tempfile.TemporaryDirectory() as root:
            cache = PersistentStructureCache(
                os.path.join(root, "cache"), max_items=2, max_bytes=None
            )
            materializer = CopyMaterializer(cache)
            results = []
            for name in ("a.pdb", "b.pdb", "c.pdb"):
                path = os.path.join(root, name)
                self._write(path, name.encode("ascii"))
                results.append(materializer.materialize(path))
                if len(results) == 1:
                    cache.set_navigation_pins(current=results[0])
            cache.set_navigation_pins(current=results[0], preload=[results[2]])

            status = cache.status()
            self.assertEqual(status["entry_count"], 2)
            self.assertTrue(os.path.exists(results[0].path))
            self.assertFalse(os.path.exists(results[1].path))
            self.assertTrue(os.path.exists(results[2].path))
            self.assertEqual(status["pinned_count"], 2)

            normal = cache.clear()
            self.assertEqual(normal["removed_items"], 0)
            self.assertEqual(normal["retained_items"], 2)
            forced = cache.clear(force=True)
            self.assertEqual(forced["removed_items"], 2)
            self.assertEqual(cache.status()["entry_count"], 0)

    def test_manifest_rebuild_recovers_entry_and_startup_removes_partials(self):
        with tempfile.TemporaryDirectory() as root:
            cache_root = os.path.join(root, "cache")
            source = os.path.join(root, "source.pdb")
            self._write(source, b"source")
            original_cache = PersistentStructureCache(cache_root)
            result = CopyMaterializer(original_cache).materialize(source)
            identity = SourceIdentity.from_path(source)
            with open(original_cache.manifest_path, "w", encoding="utf-8") as handle:
                handle.write("not-json")
            abandoned = os.path.join(cache_root, "objects", "abandoned.part")
            self._write(abandoned, b"partial")

            rebuilt = PersistentStructureCache(cache_root)
            recovered = rebuilt.lookup(identity)

            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.path, result.path)
            self.assertTrue(rebuilt.status()["manifest_rebuilt"])
            self.assertFalse(os.path.exists(abandoned))
            with open(rebuilt.manifest_path, "r", encoding="utf-8") as handle:
                self.assertEqual(
                    json.load(handle)["schema"],
                    "pymolplugins.structure-cache/1.0",
                )


if __name__ == "__main__":
    unittest.main()
