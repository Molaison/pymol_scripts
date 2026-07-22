"""Object/group-level target resolution and command iteration."""

from .resolver import TargetMatch, TargetResolutionError, resolve_targets

__all__ = ["TargetMatch", "TargetResolutionError", "resolve_targets"]
