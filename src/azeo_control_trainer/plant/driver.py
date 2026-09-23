"""Runs a plant against the tag store.

The plant itself is pure Python and knows nothing about the store or Qt; this
is the only piece that couples the two, and it is deliberately thin.

It steps on its own timer rather than inside the controller executive, because
a plant and a controller are not synchronous in the field and a student should
see that. The default is faster than the module scan, so the process moves
smoothly between scans instead of stepping.
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, Qt, QTimer

from .process import Plant

log = logging.getLogger("plant.driver")

#: Process step. Faster than the 500 ms module scan so the controller samples
#: a process that is already moving, rather than one that only changes when it
#: happens to look.
DEFAULT_PERIOD_MS = 200

#: A blocked UI thread must not deliver one enormous step that drains a tank
#: between frames.
MAX_DT_S = 1.0


class PlantDriver(QObject):
    """Steps a :class:`Plant` and exchanges its tags with the store."""

    def __init__(self, plant: Plant, store, period_ms: int = DEFAULT_PERIOD_MS,
                 parent=None):
        super().__init__(parent)
        self.plant = plant
        self._store = store
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self.step_once)
        self._period_ms = int(period_ms)
        self._last_t: float | None = None
        self._steps = 0

    # ------------------------------------------------------------ control
    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    @property
    def step_count(self) -> int:
        return self._steps

    def seed(self) -> None:
        """Publish initial values, before anything goes on scan."""
        values = self.plant.initial_values()
        if values and self._store is not None:
            self._store.set_many(values)
            log.info("Plant '%s' seeded %d tags", self.plant.key, len(values))

    def start(self) -> None:
        if self._timer.isActive():
            return
        self._last_t = None
        self._timer.start(self._period_ms)
        log.info("Plant '%s' running (%d ms)", self.plant.key, self._period_ms)

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            log.info("Plant '%s' stopped after %d steps",
                     self.plant.key, self._steps)

    def set_period_ms(self, ms: int) -> None:
        self._period_ms = max(10, int(ms))
        if self._timer.isActive():
            self._timer.start(self._period_ms)

    # --------------------------------------------------------------- step
    def step_once(self, dt: float | None = None) -> dict:
        """Read the controller's outputs, advance the process, publish."""
        if self._store is None:
            return {}
        if dt is None:
            now = time.perf_counter()
            dt = (self._period_ms / 1000.0 if self._last_t is None
                  else min(now - self._last_t, MAX_DT_S))
            self._last_t = now

        data = self._store.get_all()
        inputs = {tag: data.get(tag) for tag in self.plant.reads()}
        try:
            outputs = self.plant.step(dt, inputs)
        except Exception:                                   # noqa: BLE001
            log.exception("Plant '%s' step failed", self.plant.key)
            return {}

        self._store.set_many(outputs)
        self._steps += 1
        return outputs
