"""The controller as a thing: a Azeo-PK-style node with a name and a size.

The trainer has always *had* a controller — the scan executive — but as an
anonymous timer, not a thing a student can point at. The real Azeo PK is a
named node with a model number, a DST capacity, a scheduler and a carrier
keylock, and every one of those is a concept the course teaches. This module
is that node.

Three rules keep it honest:

- **Nothing here is hand-counted.** DST usage is derived from the tag database
  and excludes signals owned by a configured EIOC. It cannot drift from the
  configuration because it *is* the configuration.
- **Capacity warns, it does not refuse.** A trainer that blocks an
  over-subscribed download teaches less than one that shows the red gauge
  and lets the student feel what over-subscription looks like.
- **The keylock is runtime state, never serialized.** A locked file that
  silently refuses downloads next session is a trap, not a lesson. Locked
  refuses download and decommission; operation, tuning and forces continue —
  the PK's exact split between operating and re-engineering a controller.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger("strategy.pk_controller")


class PKModel(Enum):
    """The four PK sizes; the value is the DST capacity (May 2026 PDS)."""

    PK100 = 100
    PK300 = 300
    PK750 = 750
    PK1500 = 1500


#: The module execution rate menu, in ms. Azeo's menu starts at 25 ms;
#: 25 and 50 are deliberately absent because a Windows/Qt/Python runtime
#: cannot keep them without jitter that would mis-teach — a rate the trainer
#: offers must be a rate it actually delivers. They are shown greyed out
#: with the reason, which itself teaches that controllers have a floor.
SCAN_RATE_MENU_MS: tuple[int, ...] = (
    100, 200, 500, 1000, 2000, 5000, 10000, 30000, 60000)

#: Rates the real PK offers that this runtime honestly cannot.
UNSUPPORTED_RATES_MS: tuple[int, ...] = (25, 50)

#: The default module scan rate — Azeo's default is 1 s; the trainer has
#: always used 500 ms, and a module file that never says otherwise stays
#: byte-identical.
DEFAULT_SCAN_MS = 500


def rate_label(ms: int) -> str:
    return f"{ms} ms" if ms < 1000 else f"{ms // 1000} s"


@dataclass
class PKController:
    """One controller node, declared per area in `_project.json`.

    No declaration means the defaults — every area gains a controller
    without an edit, which is why none of these fields is required.
    """

    name: str = "PK-CTLR-1"
    model: PKModel = field(default=PKModel.PK100)
    #: The carrier key. TRUE maps to Azeo's `KeyLockStatus`: download,
    #: decommission and upgrade are refused; operation continues untouched.
    keylock: bool = False

    @classmethod
    def from_config(cls, config: dict | None) -> "PKController":
        """The node an area's `_project.json` `controller` section declares."""
        config = dict(config or {})
        model_name = str(config.get("model", "PK100")).upper().strip()
        try:
            model = PKModel[model_name]
        except KeyError:
            log.warning("Unknown PK model %r — using PK100 "
                        "(known: %s)", model_name,
                        ", ".join(m.name for m in PKModel))
            model = PKModel.PK100
        return cls(name=str(config.get("name") or "PK-CTLR-1"), model=model)

    # ------------------------------------------------------------- capacity
    @property
    def dst_limit(self) -> int:
        return self.model.value

    def dst_usage(self, store) -> int:
        """Controller-native field signal tags in use.

        The tag database carries both native I/O and external EIOC signals.
        The latter are subtracted because the EIOC owns their capacity; the
        PK's embedded OPC UA server also consumes no DSTs.
        """
        tagdb = getattr(store, "tagdb", None)
        if tagdb is None:
            return 0
        try:
            field_tags = set(tagdb.field_tags())
            # External client signals are owned/capacity-checked by the EIOC,
            # not by the PK's native DST pool.
            eioc = getattr(store, "eioc", None)
            external = set(eioc.signals) if eioc is not None else set()
            return len(field_tags - external)
        except Exception:                                   # noqa: BLE001
            return 0

    def over_capacity(self, store) -> bool:
        return self.dst_usage(store) > self.dst_limit

    def identity_line(self, store) -> str:
        """The one-line identity the diagnostics header shows."""
        lock = "keylock LOCKED" if self.keylock else "keylock unlocked"
        return (f"{self.name} · {self.model.name} · simplex · {lock} · "
                f"DSTs {self.dst_usage(store)} of {self.dst_limit}")
