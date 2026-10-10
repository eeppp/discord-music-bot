from __future__ import annotations

import random
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from player import Song


class SongQueue:
    """Per-guild queue operations; nested to avoid shadowing Python's queue module."""

    def __init__(self) -> None:
        self._items: deque[Song] = deque()

    def add(self, song: Song) -> None:
        self._items.append(song)

    def add_next(self, song: Song) -> None:
        self._items.appendleft(song)

    def pop(self) -> Song | None:
        return self._items.popleft() if self._items else None

    def clear(self) -> None:
        self._items.clear()

    def shuffle(self) -> None:
        items = list(self._items)
        random.shuffle(items)
        self._items = deque(items)

    def snapshot(self, limit: int | None = None) -> list[Song]:
        items = list(self._items)
        return items if limit is None else items[:limit]

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)
