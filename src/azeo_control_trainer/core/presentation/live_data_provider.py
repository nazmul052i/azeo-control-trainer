"""QTimer-driven polling of SharedDataStore for live trending.

Polls the store at 1 Hz, converts SI values to display (FPS) units,
and maintains per-tag ring buffers.  Emits ``new_data`` after each poll
so trend panes can refresh.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Dict, Set, Tuple

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from .tag_registry import TAG_REGISTRY, TagMeta


class LiveDataProvider(QObject):
    """Centralized live-data poller with per-tag ring buffers."""

    POLL_INTERVAL_MS = 1000
    BUFFER_SIZE = 3600  # 1 hour at 1 Hz

    new_data = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self._store = store
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

        self._subscribed: Set[str] = set()
        self._buffers: Dict[str, deque] = {}
        self._time_buffer: deque = deque(maxlen=self.BUFFER_SIZE)

    def subscribe(self, tag: str):
        if tag not in self._subscribed:
            self._subscribed.add(tag)
            self._buffers[tag] = deque(maxlen=self.BUFFER_SIZE)

    def unsubscribe(self, tag: str):
        self._subscribed.discard(tag)
        self._buffers.pop(tag, None)

    def get_data(self, tag: str) -> Tuple[np.ndarray, np.ndarray]:
        """Return (wall_times, display_values) for a subscribed tag."""
        times = np.array(self._time_buffer, dtype=np.float64)
        buf = self._buffers.get(tag)
        if buf is None:
            return times[:0], np.array([], dtype=np.float64)
        values = np.array(buf, dtype=np.float64)
        # Align lengths -- use the LATEST entries from both buffers
        # (tag may have been subscribed after provider started, so its
        # buffer is shorter than _time_buffer)
        n = min(len(times), len(values))
        return times[-n:], values[-n:]

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _poll(self):
        if self._store is None:
            return
        data = self._store.get_all()
        wall = time.time()
        self._time_buffer.append(wall)

        for tag in self._subscribed:
            raw = data.get(tag)
            if raw is not None and isinstance(raw, (int, float)):
                meta = TAG_REGISTRY.get(tag)
                if meta and meta.si_to_display:
                    converted = meta.si_to_display(raw)
                else:
                    converted = raw
                self._buffers[tag].append(converted)
            else:
                # Keep buffer aligned even if value missing
                buf = self._buffers.get(tag)
                if buf and len(buf) > 0:
                    self._buffers[tag].append(buf[-1])
                else:
                    self._buffers.setdefault(tag, deque(maxlen=self.BUFFER_SIZE)).append(0.0)

        self.new_data.emit()
