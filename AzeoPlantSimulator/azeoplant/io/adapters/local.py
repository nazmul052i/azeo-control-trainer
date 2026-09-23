# -*- coding: utf-8 -*-
"""In-process loopback adapter for tests, scripting and CI.

Reads are the live tags (the bus adds no copy, neither does this);
writes go through the same queue-and-drain executive every other
adapter uses, so a test that drives the plant through the LocalAdapter
exercises exactly the arbitration a networked DCS would meet.
"""

from __future__ import annotations

from typing import Iterable

from ..sample import SignalSample
from .base import AdapterHealth, VirtualIOAdapter


class LocalAdapter(VirtualIOAdapter):
    name = "local"

    def __init__(self, bus, source: str = "local") -> None:
        super().__init__(bus)
        source = str(source).strip()
        if not source:
            raise ValueError("LocalAdapter source must not be empty")
        self.source = source
        self._health = AdapterHealth(self.name)

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._health.connected:
            return
        # ``register_dcs`` is idempotent by holder name for the internal DCS,
        # but two LocalAdapter objects carrying the same configured source are
        # still two sessions. Do not let the second borrow the first session's
        # lease and later release it from underneath the real owner.
        if self.bus.holder is not None:
            holder = self.bus.holder
            self._health.last_error = (
                f"output side is already held by {holder}")
            self._health.detail.update(source=self.source, holder=holder)
            raise RuntimeError(self._health.last_error)
        if not self.bus.register_dcs(self.source):
            holder = self.bus.holder or "unknown"
            self._health.last_error = (
                f"output side is already held by {holder}")
            self._health.detail.update(source=self.source, holder=holder)
            raise RuntimeError(self._health.last_error)
        self._health.connected = True
        self._health.last_error = ""
        self._health.detail.update(source=self.source, holder=self.bus.holder)

    def stop(self) -> None:
        if not self._health.connected:
            return
        self.bus.release_dcs(self.source)
        self._health.connected = False
        self._health.detail.update(source=self.source, holder=self.bus.holder)

    # ------------------------------------------------------------- moving
    def read(self, tag: str):
        """The live value, forces included (they are applied to the tag)."""
        return self.read_sample(tag).value

    def read_sample(self, tag: str) -> SignalSample:
        """Return an immutable signal sample, never a live plant object."""
        # The native bus takes its mutex inside sample(). Enter through the
        # Python lock facade first: it releases the GIL while waiting. Without
        # that, a snapshot owner doing file I/O cannot wake to release the lock.
        with self.bus.db.lock:
            return self.bus.sample(tag)

    def read_samples(self, tags: Iterable[str] | None = None
                     ) -> dict[str, SignalSample]:
        """Take one lock-consistent snapshot of the requested signals."""
        with self.bus.db.lock:
            return self.bus.samples(tags)

    def write(self, tag: str, value) -> None:
        """Queue an output write, drained on the next engine step."""
        if not self._health.connected:
            raise RuntimeError("LocalAdapter is not started")
        self.bus.queue_write(tag, value, source=self.source)
        self._health.received += 1

    def publish_inputs(self) -> None:
        self._health.published += 1      # in-process: reads are live

    def receive_outputs(self) -> None:
        pass                             # writes land on the queue directly

    def health(self) -> AdapterHealth:
        self._health.rejected = (self.bus.stats.rejected_ownership
                                 + self.bus.stats.rejected_holder
                                 + self.bus.stats.rejected_value
                                 + self.bus.stats.rejected_capacity)
        self._health.detail.update(source=self.source,
                                   holder=self.bus.holder,
                                   write_queue=self.bus.queue_health())
        return self._health
