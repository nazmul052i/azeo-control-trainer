"""The switchover drill — what redundancy quality *means*, measured.

True 1:1 redundancy is a deliberate non-goal (two synchronized executives
is a large, subtle build). But the thing redundancy exists to protect — the
process not feeling the controller change — can be taught without it, by
doing the failover and **measuring the bump**:

- **Warm switchover** is the redundant pair's story: the standby holds
  synced state, so takeover resumes from exactly where the primary died.
  In the trainer the modules simply keep their in-memory state across the
  outage — perfectly synced, because it is the same state. What the
  process feels is the **outage window**: scans missed while nobody was in
  control, and one capped-dt scan on takeover.
- **Cold restart** is the simplex story — the swapped controller with the
  SD card: configuration survives, state does not. Every block resets,
  integrators go to zero, and outputs re-seed bumplessly from the store
  (`initialize_outputs`, hard-won item 17) — which is precisely why the
  *first* post-takeover output matches and the interesting number is what
  the loop does in the settle window after it.

The report is per controller output (AO/DO field tags, walked from the tag
database): value at failure, first value after takeover, and the worst
deviation inside the observation window. The student runs the drill while
watching a trend; these numbers are the caption.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("strategy.switchover")

WARM = "warm"
COLD = "cold"


@dataclass
class OutputBump:
    tag: str
    before: float | bool | None
    first: float | bool | None = None
    worst: float = 0.0          # max |value - before| over the window

    def line(self) -> str:
        def show(value):
            return "—" if value is None else (
                f"{value:.2f}" if isinstance(value, float) else str(value))
        return (f"{self.tag}: {show(self.before)} → {show(self.first)}"
                f"  (worst deviation {self.worst:.2f})")


@dataclass
class DrillReport:
    mode: str
    outage_s: float
    bumps: list[OutputBump] = field(default_factory=list)

    @property
    def worst(self) -> float:
        return max((b.worst for b in self.bumps), default=0.0)

    def text(self) -> str:
        head = (f"{self.mode.upper()} switchover — controller dead for "
                f"{self.outage_s:.1f} s, worst output deviation "
                f"{self.worst:.2f}")
        return "\n".join([head] + [f"  {b.line()}" for b in self.bumps])


class SwitchoverDrill:
    """One failover, staged: fail → (outage) → takeover → observe.

    Split into stages rather than one blocking call so a UI can run it on
    timers while the operator watches the trend, and a test can drive the
    same object synchronously. The drill owns no timer of its own.
    """

    def __init__(self, store, executive):
        self.store = store
        self.executive = executive
        self.mode = WARM
        self.report: DrillReport | None = None
        self._bumps: dict[str, OutputBump] = {}
        self._failed_at: float | None = None
        self._runtimes: list = []

    # ------------------------------------------------------------ the tags
    def output_tags(self) -> list[str]:
        """Every controller output the drill watches — AO/DO field points,
        from the tag database, so the watch list cannot drift from the
        configuration."""
        tagdb = getattr(self.store, "tagdb", None)
        if tagdb is None:
            return []
        from ..tagdb import EntryKind

        seen: dict[str, None] = {}
        for entry in tagdb.of_kind(EntryKind.FIELD):
            if entry.direction == "output" and entry.io_tag:
                seen.setdefault(entry.io_tag)
        return sorted(seen)

    # ------------------------------------------------------------- staging
    def fail(self, mode: str = WARM) -> bool:
        """The primary dies: capture the last good outputs, stop the scan.

        Cold additionally does what a replacement controller does — resets
        every block and re-seeds outputs from the store — via the ordinary
        `go_offline`/`go_online` path, because the drill exists to measure
        the real machinery, not a copy of it.
        """
        self.mode = mode if mode in (WARM, COLD) else WARM
        self._runtimes = list(self.executive.online_runtimes())
        if not self._runtimes:
            return False
        self._bumps = {tag: OutputBump(tag=tag, before=self.store.get(tag))
                       for tag in self.output_tags()}
        self.executive.stop()
        self._failed_at = time.perf_counter()
        if self.mode == COLD:
            for runtime in self._runtimes:
                runtime.go_offline()
                runtime.go_online()
        log.info("Switchover drill: %s failure staged, %d output(s) watched",
                 self.mode, len(self._bumps))
        return True

    def takeover(self) -> None:
        """The standby (or the replacement) takes over: scanning resumes."""
        outage = (time.perf_counter() - self._failed_at
                  if self._failed_at is not None else 0.0)
        self.executive.start()
        self.report = DrillReport(mode=self.mode, outage_s=outage,
                                  bumps=list(self._bumps.values()))
        log.info("Switchover drill: takeover after %.2f s", outage)

    def sample(self) -> None:
        """One observation of every watched output. Call repeatedly while
        the loops settle; the report keeps the worst deviation seen."""
        for bump in self._bumps.values():
            value = self.store.get(bump.tag)
            if bump.first is None and value is not None:
                bump.first = value
            try:
                deviation = abs(float(value) - float(bump.before))
            except (TypeError, ValueError):
                # A discrete that changed is a full-size bump; one that
                # did not is none.
                deviation = 0.0 if value == bump.before else 1.0
            if deviation > bump.worst:
                bump.worst = deviation
