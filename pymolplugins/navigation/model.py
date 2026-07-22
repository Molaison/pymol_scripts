"""Pure state model shared by file, object, and group navigation adapters."""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


class NavigationError(ValueError):
    """Raised when a navigation request violates the session contract."""


@dataclass(frozen=True)
class NavigationEntry:
    """One atomic navigation unit."""

    id: str
    kind: str
    display_name: str
    source_paths: Tuple[str, ...] = ()
    object_names: Tuple[str, ...] = ()
    group_name: str = ""
    metadata: Dict[str, object] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if not self.id:
            raise NavigationError("entry id is required")
        if self.kind not in {"file", "object", "group"}:
            raise NavigationError(f"unsupported entry kind: {self.kind}")


@dataclass
class NavigationSession:
    """Deterministic batch/window cursor independent from PyMOL scene state."""

    entries: List[NavigationEntry]
    batch_size: int = 20
    window_size: int = 1
    stride: int = 1
    wrap: bool = True
    cursor: int = 0
    pinned_entry_ids: Set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.entries:
            raise NavigationError("navigation requires at least one entry")
        ids = [entry.id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise NavigationError("entry ids must be unique")
        self.batch_size = int(self.batch_size)
        self.window_size = int(self.window_size)
        self.stride = int(self.stride)
        if self.batch_size < 1:
            raise NavigationError("batch_size must be at least one")
        if self.window_size < 1:
            raise NavigationError("window_size must be at least one")
        if self.window_size > self.batch_size:
            raise NavigationError("window_size cannot exceed batch_size")
        if self.stride < 1:
            raise NavigationError("stride must be at least one")
        self.cursor = self._clamp_cursor(int(self.cursor))

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def batch_count(self) -> int:
        return (self.count + self.batch_size - 1) // self.batch_size

    @property
    def batch_index(self) -> int:
        return self.cursor // self.batch_size

    @property
    def batch_start(self) -> int:
        return self.batch_index * self.batch_size

    @property
    def batch_end(self) -> int:
        return min(self.batch_start + self.batch_size, self.count)

    def _batch_bounds_for_index(self, index: int) -> Tuple[int, int]:
        start = (index // self.batch_size) * self.batch_size
        return start, min(start + self.batch_size, self.count)

    def _max_window_start(self, batch_start: int, batch_end: int) -> int:
        return max(batch_start, batch_end - min(self.window_size, batch_end - batch_start))

    def _last_stride_start(self, batch_start: int, batch_end: int) -> int:
        """Last window start reachable by repeated ``next`` calls in a batch."""
        maximum = self._max_window_start(batch_start, batch_end)
        return batch_start + ((maximum - batch_start) // self.stride) * self.stride

    def _clamp_cursor(self, index: int) -> int:
        index = max(0, min(index, self.count - 1))
        start, end = self._batch_bounds_for_index(index)
        return min(index, self._max_window_start(start, end))

    def current_indices(self) -> Tuple[int, ...]:
        width = min(self.window_size, self.batch_end - self.cursor)
        return tuple(range(self.cursor, self.cursor + max(width, 0)))

    def current_entries(self) -> Tuple[NavigationEntry, ...]:
        return tuple(self.entries[index] for index in self.current_indices())

    def resident_indices(self) -> Tuple[int, ...]:
        return tuple(range(self.batch_start, self.batch_end))

    def resident_entries(self) -> Tuple[NavigationEntry, ...]:
        return tuple(self.entries[index] for index in self.resident_indices())

    def pinned_entries(self) -> Tuple[NavigationEntry, ...]:
        return tuple(entry for entry in self.entries if entry.id in self.pinned_entry_ids)

    def visible_entries(self) -> Tuple[NavigationEntry, ...]:
        result: List[NavigationEntry] = []
        seen: Set[str] = set()
        for entry in self.current_entries() + self.pinned_entries():
            if entry.id not in seen:
                result.append(entry)
                seen.add(entry.id)
        return tuple(result)

    def next(self) -> int:
        candidate = self.cursor + self.stride
        maximum = self._max_window_start(self.batch_start, self.batch_end)
        if candidate <= maximum:
            self.cursor = candidate
            return self.cursor
        next_batch = self.batch_index + 1
        if next_batch >= self.batch_count:
            if not self.wrap:
                self.cursor = maximum
                return self.cursor
            next_batch = 0
        self.cursor = next_batch * self.batch_size
        return self.cursor

    def previous(self) -> int:
        candidate = self.cursor - self.stride
        if candidate >= self.batch_start:
            self.cursor = candidate
            return self.cursor
        previous_batch = self.batch_index - 1
        if previous_batch < 0:
            if not self.wrap:
                self.cursor = 0
                return self.cursor
            previous_batch = self.batch_count - 1
        start = previous_batch * self.batch_size
        end = min(start + self.batch_size, self.count)
        self.cursor = self._last_stride_start(start, end)
        return self.cursor

    def next_batch(self) -> int:
        index = self.batch_index + 1
        if index >= self.batch_count:
            index = 0 if self.wrap else self.batch_count - 1
        self.cursor = index * self.batch_size
        return self.cursor

    def previous_batch(self) -> int:
        index = self.batch_index - 1
        if index < 0:
            index = self.batch_count - 1 if self.wrap else 0
        self.cursor = index * self.batch_size
        return self.cursor

    def goto(self, index: int) -> int:
        index = int(index)
        if index < 0 or index >= self.count:
            raise NavigationError(f"index must be in [0, {self.count - 1}]")
        self.cursor = self._clamp_cursor(index)
        return self.cursor

    def pin(self, entry_id: Optional[str] = None) -> str:
        target = self.entries[self.cursor].id if entry_id is None else str(entry_id)
        if target not in {entry.id for entry in self.entries}:
            raise NavigationError(f"unknown entry id: {target}")
        self.pinned_entry_ids.add(target)
        return target

    def unpin(self, entry_id: Optional[str] = None) -> str:
        target = self.entries[self.cursor].id if entry_id is None else str(entry_id)
        self.pinned_entry_ids.discard(target)
        return target

    def status(self) -> Dict[str, object]:
        return {
            "count": self.count,
            "cursor": self.cursor,
            "position": self.cursor + 1,
            "batch_index": self.batch_index,
            "batch_position": self.batch_index + 1,
            "batch_count": self.batch_count,
            "batch_size": self.batch_size,
            "window_size": self.window_size,
            "stride": self.stride,
            "wrap": self.wrap,
            "current_entry_ids": [entry.id for entry in self.current_entries()],
            "visible_entry_ids": [entry.id for entry in self.visible_entries()],
            "pinned_entry_ids": sorted(self.pinned_entry_ids),
        }
