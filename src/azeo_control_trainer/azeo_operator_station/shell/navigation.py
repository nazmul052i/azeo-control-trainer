"""Which displays exist, and how an operator moves between them.

Two small objects, both Qt-free so they can be tested without a window.

:class:`DisplayRegistry` loads a directory of display files once and answers
questions about them. :class:`NavigationStack` is back/forward — the browser
model, because it is the one every operator already knows.

Parent and home are **derived from the display header** rather than
configured: parent is the nearest display at a lower level in the same area,
home is the plant overview at level 1. A separate navigation configuration
file would be a second place for the hierarchy to live, and the two would
disagree within a year.
"""
from __future__ import annotations




# `DisplayRegistry` — an index of DynaLive `Display` FILES —
# moved to `archive/dynalive/`. `NavigationStack` is document
# agnostic (it holds ids) and is what the PVM console uses.

class NavigationStack:
    """Back and forward, with the browser's semantics.

    Navigating somewhere new drops the forward history. That is what everyone
    expects, and an operator who has to think about the navigation model is
    an operator not thinking about the plant.
    """

    def __init__(self, start: str | None = None):
        self._entries: list[str] = [start] if start else []
        self._index = 0 if start else -1

    @property
    def current(self) -> str | None:
        if 0 <= self._index < len(self._entries):
            return self._entries[self._index]
        return None

    @property
    def can_go_back(self) -> bool:
        return self._index > 0

    @property
    def can_go_forward(self) -> bool:
        return self._index < len(self._entries) - 1

    def go(self, display_id: str) -> str:
        if display_id == self.current:
            return display_id
        del self._entries[self._index + 1:]
        self._entries.append(display_id)
        self._index = len(self._entries) - 1
        return display_id

    def back(self) -> str | None:
        if self.can_go_back:
            self._index -= 1
        return self.current

    def forward(self) -> str | None:
        if self.can_go_forward:
            self._index += 1
        return self.current

    def history(self) -> tuple[str, ...]:
        return tuple(self._entries)
