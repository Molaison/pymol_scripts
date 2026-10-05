"""Platform-independent path resolution for Structure Annotator datasets.

The Structure Annotator runs inside Windows PyMOL even when its CSV files were
created on Linux or opened through WSL.  This module therefore uses ``ntpath``
and ``posixpath`` explicitly instead of interpreting paths according to the
Python process which happens to import it.  It has no PyMOL or Qt dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import ntpath
import os
import posixpath
import re
from typing import Callable, Dict, Mapping, Optional, Sequence, Tuple, Union

from .path_formats import convert_path, rewrite_path, validate_distribution, validate_format, validate_rewrites


STATUS_AVAILABLE = "available"
STATUS_UNCHECKED = "unchecked"
STATUS_MISSING = "missing"
STATUS_UNREADABLE = "unreadable"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_INVALID = "invalid"
STATUS_ERROR = "error"

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_WINDOWS_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


class PathResolverError(ValueError):
    """Raised when resolver configuration is invalid."""


@dataclass(frozen=True)
class PathMappingRule:
    """Map one absolute Windows, UNC or POSIX root to another root.

    ``~/`` is a literal remote-home alias.  It is deliberately never passed
    through :func:`os.path.expanduser`.
    """

    source_prefix: str
    target_prefix: str
    priority: int = 0
    enabled: bool = True
    case_sensitive: bool = True
    require_exists: bool = True
    provider: str = "mapped"
    name: str = ""

    def __post_init__(self) -> None:
        source = _normalize_source_prefix(_mapping_root(self.source_prefix))
        target = _normalize_absolute(_mapping_root(self.target_prefix), "target_prefix")
        if type(self.priority) is not int:
            raise PathResolverError("mapping priority must be an integer")
        for field_name in ("enabled", "case_sensitive", "require_exists"):
            if type(getattr(self, field_name)) is not bool:
                raise PathResolverError(
                    "mapping %s must be a boolean" % field_name
                )
        provider = str(self.provider).strip()
        if not provider:
            raise PathResolverError("mapping provider must not be empty")
        name = str(self.name).strip()
        if "\x00" in provider or "\x00" in name:
            raise PathResolverError("mapping metadata contains a NUL byte")
        object.__setattr__(self, "source_prefix", source)
        object.__setattr__(self, "target_prefix", target)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "name", name)

    @property
    def identifier(self) -> str:
        """Stable human-readable rule identifier used in diagnostics."""

        return self.name or self.source_prefix

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PathMappingRule":
        """Build a rule from the JSON-compatible mapping-store shape."""

        return cls(
            source_prefix=str(value.get("source_prefix", "")),
            target_prefix=str(value.get("target_prefix", "")),
            priority=value.get("priority", 0),  # type: ignore[arg-type]
            enabled=value.get("enabled", True),  # type: ignore[arg-type]
            case_sensitive=value.get("case_sensitive", True),  # type: ignore[arg-type]
            require_exists=value.get("require_exists", True),  # type: ignore[arg-type]
            provider=str(value.get("provider", "mapped")),
            name=str(value.get("name", "")),
        )

    def to_dict(self) -> Dict[str, object]:
        """Return the JSON-compatible mapping-store rule shape.

        ``name`` is resolver-only diagnostic metadata.  The persisted store
        identifies a rule by its source prefix and intentionally has no name
        field, so it is omitted here.
        """

        return {
            "source_prefix": self.source_prefix,
            "target_prefix": self.target_prefix,
            "priority": self.priority,
            "enabled": self.enabled,
            "case_sensitive": self.case_sensitive,
            "require_exists": self.require_exists,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class PathCandidate:
    """One deterministic Host-path candidate considered by the resolver."""

    path: str
    provider: str
    method: str
    matched_rule: str = ""
    priority: int = 0
    require_exists: bool = True
    exists: Optional[bool] = None
    regular_file: Optional[bool] = None
    readable: Optional[bool] = None
    diagnostic: str = ""

    @property
    def status(self) -> str:
        if self.diagnostic:
            return STATUS_ERROR
        if not self.require_exists:
            return STATUS_UNCHECKED
        if not self.exists:
            return STATUS_MISSING
        if self.regular_file is False:
            return STATUS_INVALID
        if not self.readable:
            return STATUS_UNREADABLE
        return STATUS_AVAILABLE

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "provider": self.provider,
            "method": self.method,
            "matched_rule": self.matched_rule or None,
            "priority": self.priority,
            "require_exists": self.require_exists,
            "exists": self.exists,
            "regular_file": self.regular_file,
            "readable": self.readable,
            "status": self.status,
            "diagnostic": self.diagnostic or None,
        }


@dataclass(frozen=True)
class PathResolution:
    """Resolution result which preserves source spelling and diagnostics."""

    raw_path: str
    resolved_path: str = ""
    status: str = STATUS_INVALID
    provider: str = ""
    method: str = ""
    matched_rule: str = ""
    candidates: Tuple[PathCandidate, ...] = ()
    diagnostics: Tuple[str, ...] = ()
    rewritten_path: str = ""

    @property
    def is_resolved(self) -> bool:
        return bool(self.resolved_path) and self.status in {
            STATUS_AVAILABLE,
            STATUS_UNCHECKED,
        }

    @property
    def error(self) -> str:
        if self.status in {STATUS_AVAILABLE, STATUS_UNCHECKED}:
            return ""
        return "; ".join(self.diagnostics)

    def to_dict(self) -> Dict[str, object]:
        return {
            "raw_path": self.raw_path,
            "rewritten_path": self.rewritten_path or self.raw_path,
            "resolved_path": self.resolved_path or None,
            "status": self.status,
            "provider": self.provider or None,
            "method": self.method or None,
            "matched_rule": self.matched_rule or None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "diagnostics": list(self.diagnostics),
        }


RuleInput = Union[PathMappingRule, Mapping[str, object]]


def _contains_nul(value: str) -> bool:
    return "\x00" in value


def _mapping_root(value: object) -> str:
    text = str(value).strip()
    return text + "\\" if re.fullmatch(r"[A-Za-z]:", text) else text


def _normalize_source_prefix(value: object) -> str:
    text = str(value).strip()
    if not text:
        raise PathResolverError("mapping source_prefix must not be empty")
    if _contains_nul(text):
        raise PathResolverError("mapping source_prefix contains a NUL byte")
    if _is_windows_absolute(text):
        return _normalize_windows_absolute(text, "source_prefix")
    if "\\" in text:
        raise PathResolverError("mapping source_prefix must use POSIX separators")
    if text in {"~", "~/"}:
        return "~/"
    if not text.startswith("/"):
        raise PathResolverError(
            "mapping source_prefix must be an absolute POSIX path or literal ~/"
        )
    if ".." in [part for part in text.split("/") if part]:
        raise PathResolverError("mapping source_prefix contains parent traversal")
    return posixpath.normpath(text)


def _is_windows_absolute(value: str) -> bool:
    return bool(_WINDOWS_DRIVE_ABSOLUTE.match(value)) or value.startswith(
        ("\\\\", "//")
    )


def _normalize_windows_absolute(value: object, label: str = "path") -> str:
    text = str(value).strip()
    if not text:
        raise PathResolverError("%s must not be empty" % label)
    if _contains_nul(text):
        raise PathResolverError("%s contains a NUL byte" % label)
    if not _is_windows_absolute(text):
        raise PathResolverError("%s must be an absolute Windows or UNC path" % label)
    windows_text = text.replace("/", "\\")
    _, raw_tail = ntpath.splitdrive(windows_text)
    if _relative_components_escape(raw_tail.lstrip("\\"), windows=True):
        raise PathResolverError("%s escapes its Windows root" % label)
    normalized = ntpath.normpath(windows_text)
    drive, tail = ntpath.splitdrive(normalized)
    is_unc_root = drive.startswith("\\\\") and tail == ""
    if not drive or (not tail.startswith("\\") and not is_unc_root):
        raise PathResolverError("%s is not a complete Windows or UNC path" % label)
    return normalized


def _normalize_absolute(value: object, label: str = "path") -> str:
    text = str(value).strip()
    if _is_windows_absolute(text):
        return _normalize_windows_absolute(text, label)
    if not text.startswith("/") or _contains_nul(text) or "\\" in text:
        raise PathResolverError("%s must be an absolute Windows, UNC or POSIX path" % label)
    if _relative_components_escape(text.lstrip("/"), windows=False):
        raise PathResolverError("%s escapes its POSIX root" % label)
    return posixpath.normpath(text)


def _relative_components_escape(value: str, *, windows: bool) -> bool:
    normalized = value.replace("\\", "/") if windows else value
    depth = 0
    for component in normalized.split("/"):
        if component in {"", "."}:
            continue
        if component == "..":
            if depth == 0:
                return True
            depth -= 1
        else:
            depth += 1
    return False


def _safe_relative_join(base: str, relative: str) -> Tuple[str, str]:
    """Join below *base*, returning ``(path, windows-or-posix)``."""

    if _is_windows_absolute(base):
        normalized_base = _normalize_windows_absolute(base, "source_base")
        if _relative_components_escape(relative, windows=True):
            raise PathResolverError("relative path escapes source_base")
        joined = ntpath.normpath(
            ntpath.join(normalized_base, relative.replace("/", "\\"))
        )
        try:
            common = ntpath.commonpath((normalized_base, joined))
        except ValueError as exc:
            raise PathResolverError("relative path changes Windows drive") from exc
        if ntpath.normcase(common) != ntpath.normcase(normalized_base):
            raise PathResolverError("relative path escapes source_base")
        return joined, "windows"

    literal_home = base in {"~", "~/"} or base.startswith("~/")
    if not literal_home and not base.startswith("/"):
        raise PathResolverError(
            "source_base must be an absolute Windows, UNC, POSIX, or literal ~/ path"
        )
    normalized_base = base.rstrip("/") if literal_home else posixpath.normpath(base)
    if _relative_components_escape(relative, windows=True):
        raise PathResolverError("relative path escapes source_base")
    posix_relative = relative.replace("\\", "/")
    joined = posixpath.normpath(posixpath.join(normalized_base, posix_relative))
    if literal_home:
        if not (joined == normalized_base or joined.startswith(normalized_base + "/")):
            raise PathResolverError("relative path escapes source_base")
    else:
        try:
            if posixpath.commonpath((normalized_base, joined)) != normalized_base:
                raise PathResolverError("relative path escapes source_base")
        except ValueError as exc:
            raise PathResolverError("relative path escapes source_base") from exc
    return joined, "posix"


def _posix_prefix_match(path: str, rule: PathMappingRule) -> Optional[str]:
    prefix = "~" if rule.source_prefix == "~/" else rule.source_prefix
    windows = _is_windows_absolute(prefix)
    if windows:
        if not _is_windows_absolute(path):
            return None
        path, prefix = path.replace("\\", "/"), prefix.replace("\\", "/").rstrip("/")
    compared_path = path if rule.case_sensitive else path.casefold()
    compared_prefix = prefix if rule.case_sensitive else prefix.casefold()
    if compared_path == compared_prefix:
        return ""
    if prefix == "/" and compared_path.startswith("/"):
        return path[1:]
    boundary = compared_prefix + "/"
    if compared_path.startswith(boundary):
        return path[len(prefix) + 1 :]
    return None


def _coerce_rules(rules: Sequence[RuleInput]) -> Tuple[PathMappingRule, ...]:
    result = []
    for rule in rules:
        if isinstance(rule, PathMappingRule):
            result.append(rule)
        elif isinstance(rule, Mapping):
            result.append(PathMappingRule.from_mapping(rule))
        else:
            raise PathResolverError("mapping rules must be mappings or PathMappingRule")
    return tuple(result)


def x_k_mapping_rules() -> Tuple[PathMappingRule, ...]:
    """Return the built-in remote-home to Windows ``K:`` profile."""

    return (
        PathMappingRule(
            name="x_k_mount_local",
            source_prefix="/xcfhome/zpzeng/mount_local",
            target_prefix="K:\\",
            priority=300,
            provider="sshfs-win",
        ),
        PathMappingRule(
            name="x_k_xcfhome",
            source_prefix="/xcfhome/zpzeng",
            target_prefix="K:\\",
            priority=200,
            provider="sshfs-win",
        ),
        PathMappingRule(
            name="x_k_home_alias",
            source_prefix="~/",
            target_prefix="K:\\",
            priority=100,
            provider="sshfs-win",
        ),
    )


DEFAULT_X_K_RULES = x_k_mapping_rules()


class PathResolver:
    """Resolve CSV structure paths without relying on the importing OS."""

    def __init__(
        self,
        rules: Optional[Sequence[RuleInput]] = None,
        *,
        exists: Callable[[str], bool] = os.path.exists,
        readable: Callable[[str], bool] = lambda path: os.access(path, os.R_OK),
        is_file: Optional[Callable[[str], bool]] = None,
        source_format: str = "custom",
        target_format: str = "windows",
        rewrites: Sequence[Mapping[str, object]] = (),
        wsl_distribution: str = "",
    ) -> None:
        self.rules = _coerce_rules(DEFAULT_X_K_RULES if rules is None else rules)
        self.source_format = validate_format(source_format)
        self.target_format = validate_format(target_format)
        self.rewrites = validate_rewrites(rewrites)
        self.wsl_distribution = validate_distribution(wsl_distribution)
        if not callable(exists) or not callable(readable):
            raise PathResolverError("exists and readable must be callable")
        if is_file is not None and not callable(is_file):
            raise PathResolverError("is_file must be callable or None")
        self._exists = exists
        self._readable = readable
        # Custom/fake existence callbacks historically described usable files.
        # Production defaults additionally prove that a candidate is regular.
        self._is_file = (
            os.path.isfile
            if is_file is None and exists is os.path.exists
            else is_file
        )
        # One resolver represents one immutable import/preview snapshot.  Keep
        # expensive K:/UNC/FUSE probes proportional to unique physical
        # candidates rather than CSV rows, without carrying stale results into
        # a later reload (which constructs a fresh resolver).
        self._evaluation_cache: Dict[
            str,
            Tuple[Optional[bool], Optional[bool], Optional[bool], str],
        ] = {}

    @classmethod
    def from_profile(cls, profile, **kwargs):
        """Build from a saved profile, allowing explicit per-import overrides."""
        options = {"source_format": profile.get("source_format", "custom"),
                   "target_format": profile.get("target_format", "windows"),
                   "rewrites": profile.get("rewrites", []),
                   "wsl_distribution": profile.get("wsl_distribution", "")}
        options.update(kwargs)
        return cls(profile.get("rules", []), **options)

    @staticmethod
    def _evaluation_key(path: str) -> str:
        return ntpath.normcase(ntpath.normpath(path)) if _is_windows_absolute(path) else posixpath.normpath(path)

    @staticmethod
    def _apply_evaluation(
        candidate: PathCandidate,
        outcome: Tuple[Optional[bool], Optional[bool], Optional[bool], str],
    ) -> PathCandidate:
        exists, regular_file, readable, diagnostic = outcome
        return replace(
            candidate,
            exists=exists,
            regular_file=regular_file,
            readable=readable,
            diagnostic=diagnostic,
        )

    def _evaluate(self, candidate: PathCandidate) -> PathCandidate:
        if not candidate.require_exists:
            return candidate
        key = self._evaluation_key(candidate.path)
        cached = self._evaluation_cache.get(key)
        if cached is not None:
            return self._apply_evaluation(candidate, cached)
        try:
            exists = bool(self._exists(candidate.path))
        except Exception as exc:
            evaluated = replace(
                candidate, diagnostic="existence check failed: %s" % exc
            )
            self._evaluation_cache[key] = (
                evaluated.exists,
                evaluated.regular_file,
                evaluated.readable,
                evaluated.diagnostic,
            )
            return evaluated
        if not exists:
            evaluated = replace(
                candidate, exists=False, regular_file=False, readable=False
            )
            self._evaluation_cache[key] = (False, False, False, "")
            return evaluated
        if self._is_file is not None:
            try:
                regular_file = bool(self._is_file(candidate.path))
            except Exception as exc:
                evaluated = replace(
                    candidate,
                    exists=True,
                    diagnostic="regular-file check failed: %s" % exc,
                )
                self._evaluation_cache[key] = (
                    evaluated.exists,
                    evaluated.regular_file,
                    evaluated.readable,
                    evaluated.diagnostic,
                )
                return evaluated
            if not regular_file:
                evaluated = replace(
                    candidate,
                    exists=True,
                    regular_file=False,
                    readable=False,
                )
                self._evaluation_cache[key] = (True, False, False, "")
                return evaluated
        try:
            readable = bool(self._readable(candidate.path))
        except Exception as exc:
            evaluated = replace(
                candidate,
                exists=True,
                diagnostic="readability check failed: %s" % exc,
            )
            self._evaluation_cache[key] = (
                evaluated.exists,
                evaluated.regular_file,
                evaluated.readable,
                evaluated.diagnostic,
            )
            return evaluated
        evaluated = replace(
            candidate, exists=True, regular_file=True, readable=readable
        )
        self._evaluation_cache[key] = (True, True, readable, "")
        return evaluated

    def _mapping_candidates(
        self,
        source_path: str,
        *,
        method: str,
        require_exists: Optional[bool],
    ) -> Tuple[PathCandidate, ...]:
        matches = []
        for index, rule in enumerate(self.rules):
            if not rule.enabled:
                continue
            tail = _posix_prefix_match(source_path, rule)
            if tail is not None:
                match_prefix = "~" if rule.source_prefix == "~/" else rule.source_prefix
                matches.append((len(match_prefix), rule.priority, index, rule, tail))
        if not matches:
            return ()
        longest = max(value[0] for value in matches)
        matches = [value for value in matches if value[0] == longest]
        highest_priority = max(value[1] for value in matches)
        matches = [value for value in matches if value[1] == highest_priority]
        matches.sort(key=lambda value: value[2])

        candidates = []
        for _, _, _, rule, tail in matches:
            if _relative_components_escape(tail, windows=True):
                raise PathResolverError(
                    "mapped path escapes source prefix %s" % rule.source_prefix
                )
            resolved = rule.target_prefix
            if tail:
                resolved, _ = _safe_relative_join(rule.target_prefix, tail)
            resolved = self._target_path(resolved)
            required = rule.require_exists if require_exists is None else require_exists
            candidates.append(
                self._evaluate(
                    PathCandidate(
                        path=resolved,
                        provider=rule.provider,
                        method=method,
                        matched_rule=rule.identifier,
                        priority=rule.priority,
                        require_exists=required,
                    )
                )
            )
        # Equal-precedence aliases which map to the same path are not ambiguous.
        unique = []
        seen = set()
        for candidate in candidates:
            key = self._evaluation_key(candidate.path)
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        return tuple(unique)

    def _target_path(self, path):
        return _normalize_absolute(convert_path(path, self.source_format, self.target_format, self.wsl_distribution))

    def resolve(self, raw_path, *, source_base=None, require_exists=None):
        """Apply rewrites and root/format rules while preserving the raw path."""
        raw = "" if raw_path is None else str(raw_path)
        if _contains_nul(raw):
            return _invalid(raw, "path contains a NUL byte")
        rewritten = rewrite_path(raw.strip(), self.rewrites)
        try:
            result = self._resolve(rewritten, source_base=source_base, require_exists=require_exists)
        except ValueError as exc:
            result = _invalid(raw, str(exc))
        return replace(result, raw_path=raw, rewritten_path=rewritten)

    def _resolve(
        self,
        raw_path: object,
        *,
        source_base: Optional[object] = None,
        require_exists: Optional[bool] = None,
    ) -> PathResolution:
        """Resolve one rewritten CSV path to a candidate on this computer.

        ``require_exists=None`` honors each mapping rule's setting and checks
        direct/CSV-relative Windows paths.  A boolean explicitly overrides all
        candidates, which is useful for a fast mapping preview.
        """

        raw = "" if raw_path is None else str(raw_path)
        if _contains_nul(raw):
            return _invalid(raw, "path contains a NUL byte")
        value = raw.strip()
        if not value:
            return _invalid(raw, "path is empty")
        if require_exists is not None and type(require_exists) is not bool:
            return _invalid(raw, "require_exists must be a boolean or None")

        if _DRIVE_PREFIX.match(value) and not _WINDOWS_DRIVE_ABSOLUTE.match(value):
            return _invalid(raw, "drive-relative Windows paths are not supported")
        if value.startswith("\\") and not value.startswith("\\\\"):
            return _invalid(raw, "root-relative Windows paths are not supported")

        if _is_windows_absolute(value):
            try:
                path = _normalize_windows_absolute(value)
                mapped = self._mapping_candidates(path, method="mapping", require_exists=require_exists)
                if mapped:
                    return _resolution_from_candidates(raw, mapped)
                path = self._target_path(path)
            except PathResolverError as exc:
                return _invalid(raw, str(exc))
            required = True if require_exists is None else require_exists
            candidate = self._evaluate(
                PathCandidate(
                    path=path,
                    provider="windows-host" if _is_windows_absolute(path) else "local",
                    method="direct",
                    require_exists=required,
                )
            )
            return _resolution_from_candidates(raw, (candidate,))

        source_path = value
        method = "mapping"
        if not (value.startswith("/") or value == "~" or value.startswith("~/")):
            if source_base is None or not str(source_base).strip():
                return _invalid(raw, "relative path requires source_base")
            base = str(source_base).strip()
            if _contains_nul(base):
                return _invalid(raw, "source_base contains a NUL byte")
            try:
                source_path, style = _safe_relative_join(base, value)
            except PathResolverError as exc:
                return _invalid(raw, str(exc))
            if style == "windows":
                mapped = self._mapping_candidates(source_path, method="csv-relative-mapping", require_exists=require_exists)
                if mapped:
                    return _resolution_from_candidates(raw, mapped)
                source_path = self._target_path(source_path)
                required = True if require_exists is None else require_exists
                candidate = self._evaluate(
                    PathCandidate(
                        path=source_path,
                        provider="windows-host" if _is_windows_absolute(source_path) else "local",
                        method="csv-relative",
                        require_exists=required,
                    )
                )
                return _resolution_from_candidates(raw, (candidate,))
            method = "csv-relative-mapping"

        # A mapped POSIX path containing parent components is rejected before
        # normalization.  Otherwise a value such as ``~/../secret`` could
        # leave the configured remote-home root before the prefix check.
        if ".." in [part for part in source_path.replace("\\", "/").split("/") if part]:
            return _invalid(raw, "mapped source path contains parent traversal")
        if source_path.startswith("/"):
            source_path = posixpath.normpath(source_path)
        elif source_path.startswith("~/"):
            source_path = "~/" + posixpath.normpath(source_path[2:]).lstrip("/")

        try:
            candidates = self._mapping_candidates(
                source_path,
                method=method,
                require_exists=require_exists,
            )
        except PathResolverError as exc:
            return _invalid(raw, str(exc))
        if not candidates:
            if source_path.startswith("/") and (self.target_format != "windows" or self.source_format == "wsl"):
                path = self._target_path(source_path)
                return _resolution_from_candidates(raw, (self._evaluate(PathCandidate(
                    path=path, provider="windows-host" if _is_windows_absolute(path) else "local",
                    method="format" if path != source_path else "direct",
                    require_exists=True if require_exists is None else require_exists,
                )),))
            return PathResolution(
                raw_path=raw,
                status=STATUS_MISSING,
                diagnostics=("no enabled path mapping rule matched %r" % source_path,),
            )
        return _resolution_from_candidates(raw, candidates)


def _invalid(raw_path: str, diagnostic: str) -> PathResolution:
    return PathResolution(
        raw_path=raw_path,
        status=STATUS_INVALID,
        diagnostics=(diagnostic,),
    )


def _resolution_from_candidates(
    raw_path: str, candidates: Tuple[PathCandidate, ...]
) -> PathResolution:
    if len(candidates) > 1:
        available = [value for value in candidates if value.status == STATUS_AVAILABLE]
        usable = available or [
            value for value in candidates if value.status == STATUS_UNCHECKED
        ]
        if len(usable) == 1:
            selected = usable[0]
            return _resolution_from_candidate(raw_path, selected, candidates)
        rules = ", ".join(value.matched_rule or value.path for value in candidates)
        return PathResolution(
            raw_path=raw_path,
            status=STATUS_AMBIGUOUS,
            candidates=candidates,
            diagnostics=("multiple equal-precedence path candidates: %s" % rules,),
        )
    return _resolution_from_candidate(raw_path, candidates[0], candidates)


def _resolution_from_candidate(
    raw_path: str,
    candidate: PathCandidate,
    candidates: Tuple[PathCandidate, ...],
) -> PathResolution:
    status = candidate.status
    diagnostics = []
    if status == STATUS_MISSING:
        diagnostics.append("path does not exist: %s" % candidate.path)
    elif status == STATUS_UNREADABLE:
        diagnostics.append("path is not readable: %s" % candidate.path)
    elif status == STATUS_INVALID and candidate.regular_file is False:
        diagnostics.append("path is not a regular file: %s" % candidate.path)
    elif status == STATUS_ERROR:
        diagnostics.append("%s: %s" % (candidate.path, candidate.diagnostic))
    return PathResolution(
        raw_path=raw_path,
        resolved_path=candidate.path,
        status=status,
        provider=candidate.provider,
        method=candidate.method,
        matched_rule=candidate.matched_rule,
        candidates=candidates,
        diagnostics=tuple(diagnostics),
    )


def resolve_path(
    raw_path: object,
    *,
    source_base: Optional[object] = None,
    rules: Optional[Sequence[RuleInput]] = None,
    require_exists: Optional[bool] = None,
    exists: Callable[[str], bool] = os.path.exists,
    readable: Callable[[str], bool] = lambda path: os.access(path, os.R_OK),
    is_file: Optional[Callable[[str], bool]] = None,
    source_format: str = "custom",
    target_format: str = "windows",
    rewrites: Sequence[Mapping[str, object]] = (),
    wsl_distribution: str = "",
) -> PathResolution:
    """Convenience wrapper around :class:`PathResolver`."""

    return PathResolver(
        rules, exists=exists, readable=readable, is_file=is_file,
        source_format=source_format, target_format=target_format, rewrites=rewrites, wsl_distribution=wsl_distribution,
    ).resolve(
        raw_path,
        source_base=source_base,
        require_exists=require_exists,
    )


__all__ = [
    "DEFAULT_X_K_RULES",
    "PathCandidate",
    "PathMappingRule",
    "PathResolution",
    "PathResolver",
    "PathResolverError",
    "STATUS_AMBIGUOUS",
    "STATUS_AVAILABLE",
    "STATUS_ERROR",
    "STATUS_INVALID",
    "STATUS_MISSING",
    "STATUS_UNCHECKED",
    "STATUS_UNREADABLE",
    "resolve_path",
    "x_k_mapping_rules",
]
