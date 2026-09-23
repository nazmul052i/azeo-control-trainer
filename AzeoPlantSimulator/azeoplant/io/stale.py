# -*- coding: utf-8 -*-
"""Stale detection for DCS-owned outputs.

A tag is armed the first time an external writer touches it: from then
on, silence longer than its timeout marks the quality UNCERTAIN, so a
lost DCS session shows as degraded signals rather than as outputs
frozen at plausible values with nobody home. Nothing is armed by
default - a plant deliberately run open loop with no external DCS is
quiet, not stale.
"""

from __future__ import annotations

from typing import Dict, List

DEFAULT_TIMEOUT_S = 2.0


class StaleTracker:
    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.timeout_s = float(timeout_s)
        self._last: Dict[str, float] = {}
        self._stale: Dict[str, bool] = {}

    def touch(self, tag: str, now: float) -> None:
        """An external write arrived: arm the tag and reset its timer."""
        self._last[tag] = now
        self._stale[tag] = False

    def disarm(self, tag: str) -> None:
        self._last.pop(tag, None)
        self._stale.pop(tag, None)

    def disarm_all(self) -> None:
        self._last.clear()
        self._stale.clear()

    def scan(self, now: float) -> List[str]:
        """Tags newly gone stale since the last scan."""
        fresh = []
        for tag, t in self._last.items():
            stale = (now - t) > self.timeout_s
            if stale and not self._stale[tag]:
                fresh.append(tag)
            self._stale[tag] = stale
        return fresh

    def is_stale(self, tag: str) -> bool:
        return self._stale.get(tag, False)

    def armed(self) -> List[str]:
        return list(self._last)
