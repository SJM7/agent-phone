from __future__ import annotations
from typing import Optional

class AttentionRouter:
    def __init__(self) -> None:
        self._bindings: dict[str, str] = {}
        self._queue: list[str] = []
        self._cursor: Optional[str] = None

    def bind(self, key: str, label: str) -> None:
        if key in self._bindings:
            self._bindings[key] = label
        else:
            self._bindings[key] = label

    def unbind(self, key: str) -> bool:
        if key not in self._bindings:
            return False
        del self._bindings[key]
        if key in self._queue:
            self._queue.remove(key)
        if self._cursor == key:
            self._cursor = None
        return True

    def mark_attention(self, key: str) -> bool:
        if key not in self._bindings or key in self._queue:
            return False
        self._queue.append(key)
        return True

    def clear_attention(self, key: str) -> bool:
        if key not in self._queue:
            return False
        self._queue.remove(key)
        if self._cursor == key:
            self._cursor = None
        return True

    def next_attention(self) -> Optional[str]:
        if not self._queue:
            return None
        if self._cursor is None or self._cursor not in self._queue:
            self._cursor = self._queue[0]
            return self._cursor
        
        try:
            idx = self._queue.index(self._cursor)
            next_idx = (idx + 1) % len(self._queue)
            self._cursor = self._queue[next_idx]
            return self._cursor
        except ValueError:
            self._cursor = self._queue[0]
            return self._cursor

    def current(self) -> Optional[str]:
        if self._cursor is not None and self._cursor in self._queue:
            return self._cursor
        return None

    def needs_attention(self) -> list[str]:
        return list(self._queue)

    def bindings(self) -> list[tuple[str, str]]:
        return list(self._bindings.items())
