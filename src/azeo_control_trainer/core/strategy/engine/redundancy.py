"""Qualified redundant-controller state and failover simulation.

This is a training model, not a claim of independent safety or availability
hardware.  It gives the existing measured switchover drill the controller-pair
state machine engineers expect: primary/standby roles, synchronization health,
peer-link degradation, inhibited failover, promotion, recovery, and an event
chronicle.  Process effect is still measured by :class:`SwitchoverDrill`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .switchover import SwitchoverDrill, WARM


ACTIVE = "ACTIVE"
STANDBY = "STANDBY"
FAILED = "FAILED"
UNAVAILABLE = "UNAVAILABLE"
NOT_SYNCHRONIZED = "NOT SYNCHRONIZED"


@dataclass(frozen=True)
class RedundancyEvent:
    timestamp: float
    event: str
    detail: str


@dataclass(frozen=True)
class RedundancyStatus:
    primary: str
    primary_state: str
    standby: str
    standby_state: str
    peer_link: str
    synchronized: bool
    generation: int
    last_sync: float | None
    failover_ready: bool


class RedundancySimulator:
    """Stateful 1:1 redundancy drill over the live controller executive."""

    def __init__(self, store, executive, *, primary="CTRL-A", standby="CTRL-B"):
        self.store = store
        self.executive = executive
        self.primary = str(primary)
        self.standby = str(standby)
        self.primary_state = ACTIVE
        self.standby_state = STANDBY
        self.peer_link_up = True
        self.synchronized = False
        self.generation = 0
        self.last_sync: float | None = None
        self.events: list[RedundancyEvent] = []
        self.active_drill: SwitchoverDrill | None = None
        self.last_report = None
        self.synchronize()

    @property
    def failover_ready(self) -> bool:
        return (
            self.primary_state == ACTIVE
            and self.standby_state == STANDBY
            and self.peer_link_up
            and self.synchronized
            and bool(self.executive.online_runtimes())
        )

    def status(self) -> RedundancyStatus:
        return RedundancyStatus(
            primary=self.primary,
            primary_state=self.primary_state,
            standby=self.standby,
            standby_state=self.standby_state,
            peer_link="HEALTHY" if self.peer_link_up else "FAILED",
            synchronized=self.synchronized,
            generation=self.generation,
            last_sync=self.last_sync,
            failover_ready=self.failover_ready,
        )

    def synchronize(self) -> bool:
        """Record a successful standby state transfer at a scan boundary."""
        if not self.peer_link_up or self.standby_state != STANDBY:
            self.synchronized = False
            if self.standby_state == STANDBY:
                self.standby_state = NOT_SYNCHRONIZED
            return False
        if not self.executive.online_runtimes():
            self.synchronized = False
            return False
        self.generation += 1
        self.last_sync = time.time()
        self.synchronized = True
        self._event("state synchronized", f"generation {self.generation}")
        return True

    def fail_primary(self) -> bool:
        """Fail the active controller and stage a warm automatic takeover."""
        if not self.failover_ready:
            self._event("failover inhibited", self._inhibit_reason())
            return False
        drill = SwitchoverDrill(self.store, self.executive)
        if not drill.fail(WARM):
            self._event("failover inhibited", "no modules are on scan")
            return False
        self.active_drill = drill
        self.primary_state = FAILED
        self._event("primary failed", f"{self.primary} stopped scanning")
        return True

    def takeover(self) -> bool:
        """Promote the synchronized standby and resume controller scans."""
        if self.active_drill is None or self.primary_state != FAILED:
            return False
        self.active_drill.takeover()
        self.active_drill.sample()
        self.last_report = self.active_drill.report
        failed = self.primary
        self.primary, self.standby = self.standby, self.primary
        self.primary_state = ACTIVE
        self.standby_state = FAILED
        self.synchronized = False
        self._event(
            "standby promoted",
            f"{self.primary} assumed control; {failed} requires recovery")
        self.active_drill = None
        return True

    def sample(self) -> None:
        if self.active_drill is not None:
            self.active_drill.sample()
        elif self.last_report is not None:
            # Keep measuring the short post-takeover settle period when the UI
            # calls sample after promotion.
            return

    def fail_standby(self) -> bool:
        if self.standby_state in {FAILED, UNAVAILABLE}:
            return False
        self.standby_state = FAILED
        self.synchronized = False
        self._event("standby failed", f"{self.standby}; control remains simplex")
        return True

    def recover_standby(self) -> bool:
        if self.standby_state == STANDBY and self.synchronized:
            return False
        self.standby_state = STANDBY
        self._event("standby recovered", f"{self.standby} available for synchronization")
        return self.synchronize()

    def lose_peer_link(self) -> bool:
        if not self.peer_link_up:
            return False
        self.peer_link_up = False
        self.synchronized = False
        if self.standby_state == STANDBY:
            self.standby_state = NOT_SYNCHRONIZED
        self._event(
            "peer link failed",
            "primary continues; automatic failover is inhibited")
        return True

    def restore_peer_link(self) -> bool:
        if self.peer_link_up:
            return False
        self.peer_link_up = True
        if self.standby_state == NOT_SYNCHRONIZED:
            self.standby_state = STANDBY
        self._event("peer link restored", "standby synchronization requested")
        self.synchronize()
        return True

    def reset_pair(self) -> None:
        """Recover both members without changing downloaded runtime state."""
        if self.active_drill is not None:
            self.active_drill.takeover()
            self.active_drill = None
        self.primary_state = ACTIVE
        self.standby_state = STANDBY
        self.peer_link_up = True
        self._event("pair reset", "both members available")
        self.synchronize()

    def _inhibit_reason(self) -> str:
        if not self.executive.online_runtimes():
            return "no modules are on scan"
        if not self.peer_link_up:
            return "peer link is failed"
        if self.standby_state != STANDBY:
            return f"standby is {self.standby_state.lower()}"
        if not self.synchronized:
            return "standby state is not synchronized"
        return "pair is not ready"

    def _event(self, event: str, detail: str) -> None:
        self.events.append(RedundancyEvent(time.time(), event, detail))
