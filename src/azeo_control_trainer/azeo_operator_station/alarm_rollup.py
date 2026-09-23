"""Display alarm rollups over published PVM documents.

The monitor binds every published display in the active display set, not just
the frames currently visible. That distinction is the point of a rollup: a
unit button must announce an alarm in a child display before the operator has
opened that child.
"""
from __future__ import annotations

from azeo_control_trainer.core.hmi.binding import BindingEngine
from azeo_control_trainer.core.hmi.model.alarms import (
    AlarmRecord, AlarmState, Banner, BannerLine,
)
from azeo_control_trainer.core.hmi.model.tags import LimitKind, Priority
from azeo_control_trainer.core.hmi.pvms.rendering.renderer import (
    DisplayRenderer,
    pvm_from_dict,
)


def _priority(value: int) -> Priority:
    if value >= 15:
        return Priority.P1
    if value >= 11:
        return Priority.P2
    return Priority.P3


def _kind(condition: str):
    return {"HI_HI": LimitKind.HH, "LO_LO": LimitKind.LL,
            "HI": LimitKind.HI, "LO": LimitKind.LO}.get(condition)


class DisplayAlarmRollup:
    """Keep alarm bindings and block membership for published displays."""

    def __init__(self, deployment, source, palette, *, config_root=None):
        self.deployment = deployment
        self.source = source
        self.palette = palette
        self.config_root = config_root
        self._engines: dict[str, BindingEngine] = {}
        self._blocks: dict[str, set[str]] = {}
        self.refresh_documents()

    @property
    def registry(self):
        return self.source.alarm_state

    def refresh_documents(self, force: bool = False) -> None:
        if force:
            self._engines.clear()
            self._blocks.clear()
        available = set(self.deployment.displays())
        for name in tuple(self._engines):
            if name not in available:
                self._engines.pop(name, None)
                self._blocks.pop(name, None)
        for name in sorted(available):
            if name in self._engines:
                continue
            reader = getattr(self.deployment, "monitor_document",
                             self.deployment.document)
            document = reader(name)
            if document is None:
                continue
            engine = BindingEngine(self.source)
            renderer = DisplayRenderer(
                engine, self.palette,
                config_root=(self.deployment.configuration_root(name)
                             if callable(getattr(self.deployment, "configuration_root", None))
                             else self.config_root))
            blocks = set()
            for data in document.get("pvms", ()):
                item = renderer.build_pvm(pvm_from_dict(data))
                bindings = (item.binding, item.mode_binding,
                            *item.rows.values())
                for binding in bindings:
                    path = getattr(binding, "path", "")
                    parts = path.split("/")
                    if len(parts) >= 3:
                        blocks.add("/".join(parts[:2]))
            self._engines[name] = engine
            self._blocks[name] = blocks

    def poll(self) -> int:
        self.refresh_documents()
        poll_alarms = getattr(self.source, "poll_alarms", None)
        if callable(poll_alarms):
            return poll_alarms(set().union(*self._blocks.values()))
        return sum(engine.poll() for engine in self._engines.values())

    def _descendants(self, display_set, name: str) -> tuple[str, ...]:
        node = display_set.find(name) if display_set is not None else None
        return tuple(one.display for one in node.walk()) if node else (name,)

    def summary(self, display_set=None) -> dict[str, dict]:
        names = (display_set.displays() if display_set is not None
                 else tuple(self._blocks))
        result = {}
        for name in names:
            descendants = self._descendants(display_set, name)
            blocks = set().union(
                *(self._blocks.get(child, set()) for child in descendants))
            rows = self.registry.records(blocks=blocks)
            demanding = [row for row in rows
                         if not row.suppressed
                         and (row.active or not row.acknowledged)]
            top = (demanding or list(rows) or [None])[0]
            result[name] = {
                "count": len(rows),
                "active": sum(row.active and not row.suppressed
                              for row in rows),
                "unacknowledged": sum(not row.acknowledged
                                      and not row.suppressed for row in rows),
                "suppressed": sum(row.suppressed for row in rows),
                "priority": int(top.priority if top else 0),
            }
        return result

    def records(self):
        return self.registry.records()

    def primary_control(self, module: str, block: str,
                        display_set=None) -> str:
        """Best primary-control display for an alarm source.

        Membership comes from actual display bindings.  When several
        displays show the block, the hierarchy's highest-level member is
        the primary control view; name order only breaks a tie.
        """
        key = f"{module}/{block}"
        matches = [name for name, blocks in self._blocks.items()
                   if key in blocks]
        if not matches:
            return ""
        if display_set is None:
            return sorted(matches)[0]
        return min(matches, key=lambda name: (
            display_set.level_of(name) or 99, name))

    def banner(self, lines=2) -> Banner:
        rows = self.records()
        counts = {priority: 0 for priority in Priority}
        for row in rows:
            if not row.suppressed and (
                    row.active or not row.acknowledged):
                counts[_priority(row.priority)] += 1
        unacked = [row for row in rows
                   if not row.suppressed and not row.acknowledged]
        active = [row for row in rows if row.active and not row.suppressed]
        chosen = unacked[:max(1, lines)] or active[:1]
        if not chosen:
            return Banner((BannerLine("No active alarms"),), counts)
        banner_lines = []
        for row in chosen:
            state = (AlarmState.DSUPR if row.suppressed else
                     AlarmState.UNACK_ALM if not row.acknowledged else
                     AlarmState.ACK_ALM)
            record = AlarmRecord(
                tag=f"{row.module}/{row.block}", state=state,
                priority=_priority(row.priority), kind=_kind(row.condition),
                value=row.breached_limit, description=row.condition,
                raised_at=row.raised_at, changed_at=row.changed_at,
                condition_active=row.active)
            text = record.text + ("  (acknowledged)"
                                  if row.acknowledged else "")
            banner_lines.append(BannerLine(
                text, record, acknowledged=row.acknowledged))
        return Banner(tuple(banner_lines), counts)


__all__ = ["DisplayAlarmRollup"]
