"""Associate scene objects with the source identity used for their successful load.

These functions only handle memory. The caller records after cmd.load succeeds,
activates after the scene transition succeeds, and owns the object-keyed mapping
alongside its scene transaction state. Never derive a token by reading the source
at annotation time: that could identify bytes the user has not seen.
"""

from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class LoadedSourceVersion:
    entry_id: str
    token: str
    cache_key: str


def source_version_token(logical_source, identity):
    """Portable observation identity, not a content hash when sha256 is absent.

    logical_source is the original CSV source_path or the stable directory entry
    ID. Cache locations and resolved absolute paths are deliberately excluded.
    All fields come from the successful materialization already held in memory.
    """
    fields = ["annotation-source/1", logical_source, identity.provider,
              identity.size, identity.mtime_ns, identity.sha256, identity.version_token]
    encoded = json.dumps(fields, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def record_loaded_source(objects, object_name, entry_id, identity, *, logical_source):
    """Pin an existing MaterializedStructure.identity to a newly loaded object."""
    objects[object_name] = LoadedSourceVersion(
        entry_id, source_version_token(logical_source, identity), identity.cache_key
    )


def activate_loaded_source(store, objects, object_name, entry_id):
    """Project labels for the loaded object, even if a materialization cache changed."""
    return store.bind_source(entry_id, loaded_source_token(objects, object_name, entry_id))


def loaded_source_token(objects, object_name, entry_id):
    """Read the token pinned to this scene object, never a newer cache entry."""
    observed = objects[object_name]
    if observed.entry_id != entry_id:
        raise ValueError("Loaded source version belongs to a different dataset entry")
    return observed.token


def forget_loaded_source(objects, object_name, materialized_paths):
    """An evicted direct file must be materialized again before another load."""
    observed = objects.pop(object_name, None)
    if observed is None:
        return
    cached = materialized_paths.get(observed.entry_id)
    if cached is not None and cached.mode == "direct" and cached.identity.cache_key == observed.cache_key:
        materialized_paths.pop(observed.entry_id)
