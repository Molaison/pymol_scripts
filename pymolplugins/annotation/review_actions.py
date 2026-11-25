"""Validated multi-field annotation saves, independent of PyMOL and Qt."""

from .review_config import decode_value, encode_value, missing_required, validate_fields


def save_annotations(store, entry_ids, updates, fields, *, resolve_versions=None, cancel_event=None):
    fields = validate_fields(fields)
    known = {field["name"] for field in fields}
    if set(updates) - known:
        raise ValueError("Updates contain an unknown annotation field")
    by_name = {field["name"]: field for field in fields}
    updates = {name: encode_value(by_name[name], decode_value(by_name[name], value))
               for name, value in updates.items()}
    entry_ids = tuple(dict.fromkeys(entry_ids))
    if not entry_ids:
        raise ValueError("Select at least one candidate")
    unbound = store.unbound_entries(entry_ids) if resolve_versions is not None else ()
    observed = resolve_versions(unbound, cancel_event=cancel_event) if unbound else {}
    changes, incomplete = [], []
    # The snapshot, required-field check, and append share the store's existing
    # per-path lock so another in-process writer cannot invalidate validation.
    with store._lock:
        store._refresh_if_changed_unlocked()
        versions = {entry_id: observed[entry_id] for entry_id in store.unbound_entries(unbound)}
        for entry_id in entry_ids:
            version = {"source_version": versions[entry_id]} if entry_id in versions else {}
            current = store.annotations_for(entry_id, refresh=False, **version)
            merged = dict(current)
            for label, value in updates.items():
                if value is None:
                    merged.pop(label, None)
                else:
                    merged[label] = value
                if current.get(label) != value:
                    changes.append({"entry_id": entry_id, "label": label, "value": value, **version})
            missing = missing_required(fields, merged)
            if missing:
                incomplete.append((entry_id, missing))
        if incomplete:
            names = sorted({name for _entry, labels in incomplete for name in labels})
            raise ValueError(f"Complete required fields for {len(incomplete)} selected candidate(s): {', '.join(names)}")
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Annotation save was cancelled")
        result = store.set_many(changes) if changes else {"seq": None, "changes": []}
        for entry_id, token in versions.items():
            store.bind_source(entry_id, token)
        result.update(status="saved" if changes else "unchanged", entry_ids=entry_ids)
        return result


def undo_annotations(store, sequence, *, cancel_event=None):
    result = store.undo_batch(sequence)
    result.update(status="undone", entry_ids=tuple(dict.fromkeys(change["entry_id"] for change in result["changes"])))
    return result
