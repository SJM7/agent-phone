from __future__ import annotations
from typing import Optional

class AttentionRouter:
    def __init__(self) -> None:
        self._bindings: dict[str, str] = {}
        self._queue: list[str] = []
        self._cursor: Optional[str] = None
        self._browse: Optional[str] = None   # last key visited by * (any mode)

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
        if self._browse == key:
            self._browse = None
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
        else:
            idx = self._queue.index(self._cursor)
            self._cursor = self._queue[(idx + 1) % len(self._queue)]
        self._browse = self._cursor          # browsing continues from here
        return self._cursor

    def next_bound(self) -> Optional[str]:
        """Round-robin over ALL bound terminals (quiet-time browsing with *).
        Starts at the first bound terminal; an attention visit moves the
        browse position too, so cycling continues from the last one seen."""
        keys = list(self._bindings)
        if not keys:
            return None
        if self._browse is None or self._browse not in keys:
            self._browse = keys[0]
        else:
            idx = keys.index(self._browse)
            self._browse = keys[(idx + 1) % len(keys)]
        return self._browse

    def current(self) -> Optional[str]:
        if self._cursor is not None and self._cursor in self._queue:
            return self._cursor
        return None

    def needs_attention(self) -> list[str]:
        return list(self._queue)

    def bindings(self) -> list[tuple[str, str]]:
        return list(self._bindings.items())
