# -*- coding: utf-8 -*-
"""The virtual I/O bus: the one doorway into the plant.

The models write their own measurements directly - they are the field.
Everything else comes through here:

* the software DCS (when the loop is closed) writes outputs through
  :meth:`write_from_dcs`;
* adapters land external writes on :meth:`queue_write`, and the queue is
  drained exactly once per engine step, between integration steps, with
  ownership checked at the drain;
* instructor forces go through :meth:`force` with a source and a reason,
  are visible in :meth:`active_forces`, survive snapshots, and are
  re-applied over the physics every tick;
* one DCS at a time holds the output side: the internal control system
  registers while the loop is closed, an external session may hold it
  only while the loop is open. The loser's writes are counted and
  dropped, never silently mixed.

Reads are the live typed tags themselves - the bus adds no copy.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from ..core.tags import Quality, Tag, TagDatabase
from .forcing import ForceRecord
from .ownership import OwnershipViolation, SignalOwner, owner_of
from .sample import SignalSample
from .stale import StaleTracker

log = logging.getLogger(__name__)


@dataclass
class BusStats:
    sim_writes: int = 0
    dcs_writes: int = 0
    queued: int = 0
    drained: int = 0
    rejected_ownership: int = 0
    rejected_holder: int = 0
    rejected_value: int = 0
    adjusted_writes: int = 0
    coalesced_writes: int = 0
    rejected_capacity: int = 0
    pending_peak: int = 0
    forces_applied: int = 0


class VirtualIOBus:
    """Ownership-enforcing facade over the tag database."""

    MAX_PENDING_WRITES = 4096

    def __init__(self, db: TagDatabase,
                 stale_timeout_s: float = 2.0) -> None:
        self.db = db
        self.stats = BusStats()
        # Adapter writes are current demands, not an audit log.  A frozen
        # engine must resume with the newest value for each tag/source pair,
        # never replay every intermediate controller scan.  Distinct routes
        # are capped as a second line of defence against malformed clients.
        self._queue: OrderedDict[
            Tuple[str, str], object
        ] = OrderedDict()
        self._queue_lock = threading.Lock()
        self._forces: Dict[str, ForceRecord] = {}
        self._force_qualities: Dict[str, Quality] = {}
        self._force_lock = threading.RLock()
        self._subs: Dict[str, List[Callable]] = {}
        self._holder: Optional[str] = None       # who owns the output side
        self.stale = StaleTracker(stale_timeout_s)

    # -------------------------------------------------------------- reading
    def read(self, tag: str) -> Tag:
        return self.db[tag]

    def sample(self, tag: str) -> SignalSample:
        """Return an immutable, lock-consistent copy of one signal."""
        return self.samples((tag,))[tag]

    def samples(self, tags=None) -> Dict[str, SignalSample]:
        """Return a lock-consistent snapshot without exposing live tags."""
        with self.db.lock:
            names = list(tags) if tags is not None else [
                signal.name for signal in self.db.all()]
            result: Dict[str, SignalSample] = {}
            for name in names:
                signal = self.db[name]
                result[name] = SignalSample(
                    name=signal.name,
                    value=signal.value,
                    quality=signal.quality,
                    timestamp=signal.ts,
                    kind=signal.kind,
                    unit=signal.unit,
                    eu=signal.eu,
                    lo=float(signal.lo),
                    hi=float(signal.hi),
                )
            return result

    def owner(self, tag: str) -> SignalOwner:
        return owner_of(self.db[tag].kind)

    # ------------------------------------------------------------ DCS side
    def register_dcs(self, name: str) -> bool:
        """Claim the output side. One holder at a time; first wins."""
        if self._holder is not None and self._holder != name:
            log.warning("DCS registration refused: %s already holds the "
                        "output side, %s rejected", self._holder, name)
            return False
        self._holder = name
        log.info("Output side held by %s", name)
        return True

    def release_dcs(self, name: str) -> None:
        if self._holder == name:
            self._holder = None
            log.info("Output side released by %s", name)

    @property
    def holder(self) -> Optional[str]:
        return self._holder

    def write_from_dcs(self, tag: str, value, source: str = "internal"):
        """A checked, immediate output write from the holding DCS."""
        t = self.db[tag]
        if owner_of(t.kind) is not SignalOwner.DCS:
            self.stats.rejected_ownership += 1
            raise OwnershipViolation(tag, source)
        # The SIS is not a BPCS: its trip outputs pass holder arbitration
        # unconditionally, exactly as a hardwired trip passes a controller.
        if (source != "sis" and self._holder is not None
                and source != self._holder):
            self.stats.rejected_holder += 1
            return False
        try:
            _applied, adjusted = t.set_from_dcs(value)
        except (TypeError, ValueError, PermissionError) as exc:
            self.stats.rejected_value += 1
            log.warning("Rejected %s write to %s: %s", source, tag, exc)
            return False
        if adjusted:
            self.stats.adjusted_writes += 1
            log.warning("Clamped %s write to %s within [%g, %g]",
                        source, tag, t.lo, t.hi)
        self.stats.dcs_writes += 1
        self._notify(tag)
        return True

    def authorize_dcs_write(self, tag: str, source: str = "external") -> bool:
        """Arbitration for adapters that must apply writes synchronously on
        their own wire (the OPC UA server keeps its clamp-and-writeback
        semantics): ownership and holder are checked here, the stale timer
        touched, and the caller applies the value itself on True."""
        t = self.db.get(tag)
        if t is None:
            return False
        if owner_of(t.kind) is not SignalOwner.DCS:
            self.stats.rejected_ownership += 1
            return False
        if self._holder is not None and source != self._holder:
            self.stats.rejected_holder += 1
            log.debug("Rejected %s write to %s: output side held by %s",
                      source, tag, self._holder)
            return False
        self.stale.touch(tag, time.time())
        self.stats.dcs_writes += 1
        return True

    # -------------------------------------------------------- adapter side
    def queue_write(self, tag: str, value, source: str = "external") -> None:
        """Keep the newest demand for a bounded set of tag/source routes."""
        key = (tag, source)
        with self._queue_lock:
            if key in self._queue:
                del self._queue[key]
                self.stats.coalesced_writes += 1
            elif len(self._queue) >= self.MAX_PENDING_WRITES:
                self.stats.rejected_capacity += 1
                raise BufferError(
                    "virtual I/O write queue reached its distinct-route "
                    f"capacity ({self.MAX_PENDING_WRITES})"
                )
            self._queue[key] = value
            self.stats.queued += 1
            self.stats.pending_peak = max(
                self.stats.pending_peak, len(self._queue))

    def drain_writes(self) -> int:
        """Apply the queued external writes, ownership checked at the
        drain, holder arbitration applied, stale timers touched."""
        with self._queue_lock:
            pending = [
                (tag, value, source)
                for (tag, source), value in self._queue.items()
            ]
            self._queue.clear()
        n = 0
        now = time.time()
        for tag, value, source in pending:
            t = self.db.get(tag)
            if t is None:
                continue
            if owner_of(t.kind) is not SignalOwner.DCS:
                self.stats.rejected_ownership += 1
                log.warning("Rejected %s write to %s: simulator-owned",
                            source, tag)
                continue
            if self._holder is not None and source != self._holder:
                self.stats.rejected_holder += 1
                log.debug("Rejected %s write to %s: output side held by %s",
                          source, tag, self._holder)
                continue
            try:
                _applied, adjusted = t.set_from_dcs(value)
            except (TypeError, ValueError, PermissionError) as exc:
                self.stats.rejected_value += 1
                log.warning("Rejected %s write to %s: %s",
                            source, tag, exc)
                continue
            if adjusted:
                self.stats.adjusted_writes += 1
                log.warning("Clamped %s write to %s within [%g, %g]",
                            source, tag, t.lo, t.hi)
            self.stale.touch(tag, now)
            self.stats.drained += 1
            self._notify(tag)
            n += 1
        return n

    def queue_health(self) -> Dict[str, int]:
        """Return bounded-demand diagnostics for adapters and tests."""
        with self._queue_lock:
            return {
                "pending": len(self._queue),
                "capacity": self.MAX_PENDING_WRITES,
                "peak": self.stats.pending_peak,
                "coalesced": self.stats.coalesced_writes,
                "rejected_capacity": self.stats.rejected_capacity,
            }

    # ------------------------------------------------------------- forcing
    def force(self, tag: str, value, source: str, reason: str) -> None:
        """Instructor force, audited. Simulator-owned signals publish the
        forced value while physics continues underneath; DCS-owned
        signals ride the tag's local override mechanism."""
        if not reason:
            raise ValueError("a force needs a reason")
        t = self.db[tag]
        rec = ForceRecord(tag, value, source, reason)
        if owner_of(t.kind) is SignalOwner.DCS:
            t.override = True
            t.override_value = (float(value) if t.kind.analogue
                                else bool(value))
        else:
            rec.true_value = t.value
        with self._force_lock:
            self._force_qualities.pop(tag, None)
            self._forces[tag] = rec
        log.info("FORCE %s = %s by %s: %s", tag, value, source, reason)

    def simulate_input(self, tag: str, value, quality: Quality | str | int,
                       source: str = "virtual-io-simulator") -> None:
        """Apply an audited value/quality override to one AI or DI.

        This is narrower than an instructor force: output signals are never
        writable here, matching the physical input-simulation ownership rule.
        """
        t = self.db[tag]
        if owner_of(t.kind) is not SignalOwner.SIMULATOR:
            raise PermissionError(
                f"{tag} is {t.kind.value}; only AI and DI can be simulated")
        if isinstance(quality, str):
            try:
                resolved_quality = Quality[quality.strip().upper()]
            except KeyError as error:
                raise ValueError(f"unsupported quality {quality!r}") from error
        else:
            resolved_quality = Quality(int(quality))
        self.force(tag, value, source, "Virtual I/O signal simulation")
        with self._force_lock:
            self._force_qualities[tag] = resolved_quality
        # Make a static engineering edit visible immediately while the force
        # record keeps the evolving physics available as its true value.
        with self.db.lock:
            t.value = float(value) if t.kind.analogue else bool(value)
            t.quality = resolved_quality
            t.ts = time.time()

    def clear_simulated_input(self, tag: str) -> None:
        self.release(tag)

    def release(self, tag: str) -> None:
        with self._force_lock:
            rec = self._forces.pop(tag, None)
            self._force_qualities.pop(tag, None)
        if rec is None:
            return
        t = self.db.get(tag)
        if t is not None and owner_of(t.kind) is SignalOwner.DCS:
            t.override = False
        log.info("RELEASE %s (was forced by %s)", tag, rec.source)

    def release_all(self) -> None:
        with self._force_lock:
            tags = tuple(self._forces)
        for tag in tags:
            self.release(tag)

    def active_forces(self) -> List[ForceRecord]:
        with self._force_lock:
            return list(self._forces.values())

    # ---------------------------------------------------------------- tick
    def tick(self, dt: float = 0.0) -> None:
        """Once per engine step, between the models and the controller:
        drain external writes, re-apply simulator-side forces over the
        physics, and scan the stale timers."""
        self.drain_writes()
        with self._force_lock:
            for tag, rec in self._forces.items():
                t = self.db.get(tag)
                if t is None or owner_of(t.kind) is SignalOwner.DCS:
                    continue
                rec.true_value = t.value
                t.value = (float(rec.value) if t.kind.analogue
                           else bool(rec.value))
                quality = self._force_qualities.get(tag)
                if quality is not None:
                    t.quality = quality
                    t.ts = time.time()
                self.stats.forces_applied += 1
        for tag in self.stale.scan(time.time()):
            t = self.db.get(tag)
            if t is not None:
                t.quality = Quality.UNCERTAIN
                log.warning("%s stale: no external write for %.1f s",
                            tag, self.stale.timeout_s)

    # -------------------------------------------------------- subscriptions
    def subscribe(self, tag: str, callback: Callable[[str], None]) -> None:
        self._subs.setdefault(tag, []).append(callback)

    def unsubscribe(self, tag: str, callback) -> None:
        try:
            self._subs.get(tag, []).remove(callback)
        except ValueError:
            pass

    def _notify(self, tag: str) -> None:
        for cb in self._subs.get(tag, ()):
            try:
                cb(tag)
            except Exception:
                log.exception("Bus subscriber failed for %s", tag)

    # ---------------------------------------------------------- persistence
    def capture_state(self) -> dict:
        with self._force_lock:
            return {"forces": [r.as_dict() for r in self._forces.values()],
                    "force_qualities": {
                        tag: int(quality)
                        for tag, quality in self._force_qualities.items()
                    },
                    "holder": self._holder}

    def apply_state(self, s: dict) -> None:
        self.release_all()
        for rec in (s or {}).get("forces", []):
            try:
                self.force(rec["tag"], rec["value"],
                           rec.get("source", "restored"),
                           rec.get("reason", "restored from snapshot"))
            except (KeyError, ValueError):
                continue
        with self._force_lock:
            for tag, quality in (s or {}).get("force_qualities", {}).items():
                if tag in self._forces:
                    try:
                        self._force_qualities[tag] = Quality(int(quality))
                    except (TypeError, ValueError):
                        continue
