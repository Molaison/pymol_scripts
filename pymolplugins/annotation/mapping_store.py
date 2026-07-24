"""Global, versioned path-mapping profiles for Structure Annotator.

The store intentionally contains configuration only.  Resolving a structure
path, testing candidates, and materializing remote files belong to the path
resolver and cache layers respectively.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
import threading
from typing import Dict, List, Mapping, Optional, Sequence, Union

from pymolplugins.annotation.state_store import mark_hidden_on_windows
from pymolplugins.core.windows_paths import windows_long_path


MAPPING_SCHEMA = "pymolplugins.structure-annotator-path-mappings/1.0"
MAPPING_ENVIRONMENT_VARIABLE = "PYMOLPLUGINS_ANNOTATOR_MAPPINGS"
MAPPING_DIRECTORY = ".pymolplugins"
MAPPING_FILENAME = "path_mappings.json"
DEFAULT_PROFILE_NAME = "x_k"

MAX_MAPPING_BYTES = 1024 * 1024
MAX_PROFILES = 100
MAX_RULES_PER_PROFILE = 100
MAX_PROFILE_NAME_LENGTH = 128
MAX_DESCRIPTION_LENGTH = 4096
MAX_PREFIX_LENGTH = 4096
MAX_PROVIDER_LENGTH = 128
MIN_PRIORITY = -1_000_000
MAX_PRIORITY = 1_000_000

_RULE_FIELDS = {
    "source_prefix",
    "target_prefix",
    "priority",
    "enabled",
    "case_sensitive",
    "require_exists",
    "provider",
}
_PROFILE_FIELDS = {"name", "description", "rules"}
_DOCUMENT_FIELDS = {"schema", "updated_at_utc", "profiles"}
_STORE_LOCK = threading.RLock()


class MappingStoreError(ValueError):
    """Raised when a mapping document or profile violates the store contract."""


def utc_now() -> str:
    """Return a timezone-aware UTC timestamp suitable for persisted metadata."""

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def global_mapping_path() -> str:
    """Return the global mapping path, honoring the deployment override."""

    override = os.environ.get(MAPPING_ENVIRONMENT_VARIABLE, "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(
        os.path.abspath(os.path.expanduser("~")),
        MAPPING_DIRECTORY,
        MAPPING_FILENAME,
    )


def _default_profile() -> Dict[str, object]:
    # The literal ``~/`` rule must not be expanded on the host.  It denotes the
    # remote user's home as written in an imported CSV.
    return {
        "name": DEFAULT_PROFILE_NAME,
        "description": "Remote x home mounted as the Windows K: drive",
        "rules": [
            {
                "source_prefix": "/xcfhome/zpzeng/mount_local",
                "target_prefix": "K:\\",
                "priority": 300,
                "enabled": True,
                "case_sensitive": True,
                "require_exists": True,
                "provider": "sshfs-win",
            },
            {
                "source_prefix": "/xcfhome/zpzeng",
                "target_prefix": "K:\\",
                "priority": 200,
                "enabled": True,
                "case_sensitive": True,
                "require_exists": True,
                "provider": "sshfs-win",
            },
            {
                "source_prefix": "~/",
                "target_prefix": "K:\\",
                "priority": 100,
                "enabled": True,
                "case_sensitive": True,
                "require_exists": True,
                "provider": "sshfs-win",
            },
        ],
    }


def _initial_document() -> Dict[str, object]:
    return {
        "schema": MAPPING_SCHEMA,
        "updated_at_utc": utc_now(),
        "profiles": [_default_profile()],
    }


def _clone(value):
    """Return a JSON-value defensive copy without exposing internal objects."""

    return json.loads(json.dumps(value, ensure_ascii=False))


def _validate_string(
    value: object,
    context: str,
    maximum: int,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise MappingStoreError(f"{context} must be a string")
    if len(value) > maximum:
        raise MappingStoreError(f"{context} exceeds {maximum} characters")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise MappingStoreError(f"{context} contains forbidden control characters")
    if value != value.strip():
        raise MappingStoreError(f"{context} must not have surrounding whitespace")
    if not value and not allow_empty:
        raise MappingStoreError(f"{context} must not be empty")
    return value


def _validate_rule(raw: object, context: str) -> Dict[str, object]:
    if not isinstance(raw, Mapping):
        raise MappingStoreError(f"{context} must be a JSON object")
    missing = _RULE_FIELDS.difference(raw)
    unknown = set(raw).difference(_RULE_FIELDS)
    if missing:
        raise MappingStoreError(
            f"{context} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise MappingStoreError(
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}"
        )

    priority = raw["priority"]
    if type(priority) is not int:
        raise MappingStoreError(f"{context}.priority must be an integer")
    if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
        raise MappingStoreError(
            f"{context}.priority must be in {MIN_PRIORITY}..{MAX_PRIORITY}"
        )
    booleans: Dict[str, bool] = {}
    for field in ("enabled", "case_sensitive", "require_exists"):
        value = raw[field]
        if type(value) is not bool:
            raise MappingStoreError(f"{context}.{field} must be a boolean")
        booleans[field] = value

    return {
        "source_prefix": _validate_string(
            raw["source_prefix"], f"{context}.source_prefix", MAX_PREFIX_LENGTH
        ),
        "target_prefix": _validate_string(
            raw["target_prefix"], f"{context}.target_prefix", MAX_PREFIX_LENGTH
        ),
        "priority": priority,
        "enabled": booleans["enabled"],
        "case_sensitive": booleans["case_sensitive"],
        "require_exists": booleans["require_exists"],
        "provider": _validate_string(
            raw["provider"], f"{context}.provider", MAX_PROVIDER_LENGTH
        ),
    }


def _validate_profile(raw: object, context: str = "profile") -> Dict[str, object]:
    if not isinstance(raw, Mapping):
        raise MappingStoreError(f"{context} must be a JSON object")
    missing = {"name", "rules"}.difference(raw)
    unknown = set(raw).difference(_PROFILE_FIELDS)
    if missing:
        raise MappingStoreError(
            f"{context} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise MappingStoreError(
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    name = _validate_string(raw["name"], f"{context}.name", MAX_PROFILE_NAME_LENGTH)
    description = _validate_string(
        raw.get("description", ""),
        f"{context}.description",
        MAX_DESCRIPTION_LENGTH,
        allow_empty=True,
    )
    rules = raw["rules"]
    if not isinstance(rules, list):
        raise MappingStoreError(f"{context}.rules must be a JSON array")
    if len(rules) > MAX_RULES_PER_PROFILE:
        raise MappingStoreError(
            f"{context}.rules contains more than {MAX_RULES_PER_PROFILE} entries"
        )
    return {
        "name": name,
        "description": description,
        "rules": [
            _validate_rule(rule, f"{context}.rules[{index}]")
            for index, rule in enumerate(rules)
        ],
    }


def _validate_document(raw: object) -> Dict[str, object]:
    if not isinstance(raw, Mapping):
        raise MappingStoreError("mapping document must be a JSON object")
    missing = _DOCUMENT_FIELDS.difference(raw)
    unknown = set(raw).difference(_DOCUMENT_FIELDS)
    if missing:
        raise MappingStoreError(
            f"mapping document is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise MappingStoreError(
            f"mapping document contains unknown fields: {', '.join(sorted(unknown))}"
        )
    if raw.get("schema") != MAPPING_SCHEMA:
        raise MappingStoreError(
            "unsupported mapping schema: {!r}".format(raw.get("schema"))
        )
    updated_at = _validate_string(
        raw.get("updated_at_utc", ""),
        "updated_at_utc",
        128,
        allow_empty=True,
    )
    profiles = raw.get("profiles")
    if not isinstance(profiles, list):
        raise MappingStoreError("mapping profiles must be a JSON array")
    if len(profiles) > MAX_PROFILES:
        raise MappingStoreError(
            f"mapping document contains more than {MAX_PROFILES} profiles"
        )
    clean_profiles: List[Dict[str, object]] = []
    names = set()
    for index, profile in enumerate(profiles):
        clean = _validate_profile(profile, f"profiles[{index}]")
        folded = str(clean["name"]).casefold()
        if folded in names:
            raise MappingStoreError(
                f"duplicate mapping profile name: {clean['name']!r}"
            )
        names.add(folded)
        clean_profiles.append(clean)
    return {
        "schema": MAPPING_SCHEMA,
        "updated_at_utc": updated_at,
        "profiles": clean_profiles,
    }


def _serialize_document(document: Mapping[str, object]) -> str:
    clean = _validate_document(dict(document))
    serialized = json.dumps(
        clean, indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n"
    encoded_size = len(serialized.encode("utf-8"))
    if encoded_size > MAX_MAPPING_BYTES:
        raise MappingStoreError(
            f"mapping document exceeds {MAX_MAPPING_BYTES} bytes: {encoded_size}"
        )
    return serialized


def _write_document(document: Mapping[str, object]) -> str:
    path = global_mapping_path()
    directory = os.path.dirname(path)
    resolved_directory = windows_long_path(directory)
    os.makedirs(resolved_directory, exist_ok=True)
    mark_hidden_on_windows(directory)
    serialized = _serialize_document(document)

    descriptor, temporary = tempfile.mkstemp(
        prefix=".path_mappings.", suffix=".tmp", dir=resolved_directory
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, windows_long_path(path))
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def _read_document() -> Dict[str, object]:
    path = global_mapping_path()
    resolved = windows_long_path(path)
    try:
        size = os.path.getsize(resolved)
    except OSError as exc:
        raise MappingStoreError(f"cannot inspect mapping file: {exc}") from exc
    if size > MAX_MAPPING_BYTES:
        raise MappingStoreError(
            f"mapping file exceeds {MAX_MAPPING_BYTES} bytes: {path}"
        )
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MappingStoreError(f"cannot read mapping file: {exc}") from exc
    return _validate_document(payload)


def _quarantine_invalid_store() -> Optional[str]:
    path = global_mapping_path()
    resolved = windows_long_path(path)
    if not os.path.exists(resolved):
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    for index in range(100):
        suffix = f".corrupt-{stamp}" if index == 0 else f".corrupt-{stamp}-{index}"
        candidate = path + suffix
        resolved_candidate = windows_long_path(candidate)
        if os.path.exists(resolved_candidate):
            continue
        os.replace(resolved, resolved_candidate)
        return candidate
    raise MappingStoreError("cannot choose a quarantine path for mapping store")


def _load_or_initialize() -> Dict[str, object]:
    path = global_mapping_path()
    if not os.path.isfile(windows_long_path(path)):
        document = _initial_document()
        _write_document(document)
        return document
    try:
        return _read_document()
    except MappingStoreError:
        _quarantine_invalid_store()
        document = _initial_document()
        _write_document(document)
        return document


def read_mapping_store() -> Dict[str, object]:
    """Read a defensive copy, creating defaults or quarantining corruption."""

    with _STORE_LOCK:
        return _clone(_load_or_initialize())


def read_mapping_store_readonly() -> Dict[str, object]:
    """Read mappings without creating, quarantining, or rewriting any file.

    A missing store exposes the built-in defaults in memory.  An existing but
    invalid store raises :class:`MappingStoreError` so preview operations can
    remain genuinely non-mutating instead of silently moving user data aside.
    """

    with _STORE_LOCK:
        path = global_mapping_path()
        document = (
            _read_document()
            if os.path.isfile(windows_long_path(path))
            else _initial_document()
        )
        return _clone(document)


def list_mapping_profiles() -> List[Dict[str, object]]:
    """Return all profiles in persisted order as defensive copies."""

    document = read_mapping_store()
    profiles = document["profiles"]
    assert isinstance(profiles, list)
    return profiles


def get_mapping_profile(name: str) -> Optional[Dict[str, object]]:
    """Return one case-insensitively named profile, or ``None`` when absent."""

    clean_name = _validate_string(name, "profile name", MAX_PROFILE_NAME_LENGTH)
    folded = clean_name.casefold()
    for profile in list_mapping_profiles():
        if str(profile["name"]).casefold() == folded:
            return profile
    return None


def get_mapping_profile_readonly(name: str) -> Optional[Dict[str, object]]:
    """Return one profile without producing mapping-store side effects."""

    clean_name = _validate_string(name, "profile name", MAX_PROFILE_NAME_LENGTH)
    folded = clean_name.casefold()
    document = read_mapping_store_readonly()
    profiles = document["profiles"]
    assert isinstance(profiles, list)
    for profile in profiles:
        if str(profile["name"]).casefold() == folded:
            return profile
    return None


def upsert_mapping_profile(
    profile: Union[str, Mapping[str, object]],
    rules: Optional[Sequence[Mapping[str, object]]] = None,
    *,
    description: Optional[str] = None,
) -> Dict[str, object]:
    """Insert or replace a profile and return the validated persisted value.

    Callers may pass a complete profile mapping, or ``name`` plus ``rules``.
    Profile identity is case-insensitive so changing only capitalization
    replaces the existing entry rather than creating an ambiguous duplicate.
    """

    if isinstance(profile, str):
        if rules is None:
            raise MappingStoreError("rules are required when profile is a name")
        candidate: Mapping[str, object] = {
            "name": profile,
            "description": description or "",
            "rules": list(rules),
        }
    else:
        if rules is not None or description is not None:
            raise MappingStoreError(
                "rules and description cannot accompany a complete profile"
            )
        candidate = profile
    clean = _validate_profile(candidate)

    with _STORE_LOCK:
        document = _load_or_initialize()
        existing = document["profiles"]
        assert isinstance(existing, list)
        folded = str(clean["name"]).casefold()
        profiles: List[Dict[str, object]] = []
        replaced = False
        for item in existing:
            if str(item["name"]).casefold() == folded:
                profiles.append(clean)
                replaced = True
            else:
                profiles.append(item)
        if not replaced and len(profiles) >= MAX_PROFILES:
            raise MappingStoreError(
                f"mapping store cannot contain more than {MAX_PROFILES} profiles"
            )
        if not replaced:
            profiles.append(clean)
        document["profiles"] = profiles
        document["updated_at_utc"] = utc_now()
        _write_document(document)
    return _clone(clean)


def remove_mapping_profile(name: str) -> bool:
    """Remove one case-insensitively named profile and report whether it existed."""

    clean_name = _validate_string(name, "profile name", MAX_PROFILE_NAME_LENGTH)
    folded = clean_name.casefold()
    with _STORE_LOCK:
        document = _load_or_initialize()
        existing = document["profiles"]
        assert isinstance(existing, list)
        profiles = [
            item
            for item in existing
            if str(item["name"]).casefold() != folded
        ]
        if len(profiles) == len(existing):
            return False
        document["profiles"] = profiles
        document["updated_at_utc"] = utc_now()
        _write_document(document)
        return True


def mapping_store_status() -> Dict[str, object]:
    """Return bounded diagnostics for commands and the Qt settings panel."""

    with _STORE_LOCK:
        document = _load_or_initialize()
        path = global_mapping_path()
        profiles = document["profiles"]
        assert isinstance(profiles, list)
        names = [str(profile["name"]) for profile in profiles]
        rule_count = sum(len(profile["rules"]) for profile in profiles)
        try:
            size = os.path.getsize(windows_long_path(path))
        except OSError:
            size = 0
        return {
            "schema": MAPPING_SCHEMA,
            "path": path,
            "exists": os.path.isfile(windows_long_path(path)),
            "environment_override": bool(
                os.environ.get(MAPPING_ENVIRONMENT_VARIABLE, "").strip()
            ),
            "profile_count": len(profiles),
            "rule_count": rule_count,
            "profiles": names,
            "default_profile_present": any(
                name.casefold() == DEFAULT_PROFILE_NAME.casefold() for name in names
            ),
            "updated_at_utc": document["updated_at_utc"],
            "size_bytes": size,
        }


# Compact aliases keep command/controller call sites readable while the longer
# names make standalone imports self-documenting.
list_profiles = list_mapping_profiles
get_profile = get_mapping_profile
upsert_profile = upsert_mapping_profile
remove_profile = remove_mapping_profile
store_status = mapping_store_status


__all__ = [
    "DEFAULT_PROFILE_NAME",
    "MAPPING_DIRECTORY",
    "MAPPING_ENVIRONMENT_VARIABLE",
    "MAPPING_FILENAME",
    "MAPPING_SCHEMA",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_MAPPING_BYTES",
    "MAX_PREFIX_LENGTH",
    "MAX_PROFILES",
    "MAX_PROFILE_NAME_LENGTH",
    "MAX_PROVIDER_LENGTH",
    "MAX_RULES_PER_PROFILE",
    "MappingStoreError",
    "get_mapping_profile",
    "get_mapping_profile_readonly",
    "get_profile",
    "global_mapping_path",
    "list_mapping_profiles",
    "list_profiles",
    "mapping_store_status",
    "read_mapping_store",
    "read_mapping_store_readonly",
    "remove_mapping_profile",
    "remove_profile",
    "store_status",
    "upsert_mapping_profile",
    "upsert_profile",
]
