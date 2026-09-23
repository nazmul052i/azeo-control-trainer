"""Non-destructive data scenarios for Graphics Designer Test mode.

The station and controller always read the real provider.  This wrapper is
installed only around a Studio canvas source and overlays selected reads while
TEST is active.  It therefore lets an author exercise Bad quality, alarms and
forced indication without writing to a running process or inventing a second
renderer.
"""
from __future__ import annotations

from dataclasses import replace

from azeo_control_trainer.core.strategy.model.terminal import LimitStatus, Quality
from azeo_control_trainer.core.hmi.binding.result import BindingResult, UNRESOLVED


class PreviewSource:
    """A source-compatible, opt-in overlay over the live graph source."""

    def __init__(self, source):
        self.source = source
        self.enabled = False
        self.overrides: dict[str, dict] = {}
        # Alarm-aware callers expect this attribute on LiveGraphSource.
        self.alarm_state = source.alarm_state

    def __getattr__(self, name: str):
        """Preserve the complete provider contract outside read overlays.

        Graphics Designer verification and browsing intentionally ask the live
        provider about its configured namespace.  Explicit delegation keeps
        those read-only capabilities available without coupling the overlay
        to one concrete source implementation.
        """
        return getattr(self.source, name)

    def read(self, path: str) -> BindingResult:
        base = self.source.read(path)
        override = self.overrides.get(str(path)) if self.enabled else None
        if override is None:
            return base
        if override.get("unresolved"):
            return UNRESOLVED
        if base is UNRESOLVED:
            base = BindingResult()
        quality = override.get("quality", Quality.GOOD)
        if isinstance(quality, str):
            quality = Quality.__members__.get(quality.upper(), Quality.GOOD)
        limit = override.get("limit", base.limit)
        if isinstance(limit, str):
            limit = LimitStatus.__members__.get(
                limit.upper(), LimitStatus.NOT_LIMITED)
        return replace(
            base,
            value=override.get("value", base.value),
            quality=quality,
            limit=limit,
            forced=bool(override.get("forced", base.forced)),
            alarm_active=bool(override.get("alarm_active", False)),
            alarm_acked=bool(override.get("alarm_acked", False)),
            alarm_priority=int(override.get("alarm_priority", 0) or 0),
            alarm_condition=str(override.get("alarm_condition", "")),
            mode_target=str(override.get("mode_target", base.mode_target)),
            mode_actual=str(override.get("mode_actual", base.mode_actual)),
            mode_normal=str(override.get("mode_normal", base.mode_normal)),
            module_running=override.get("module_running", base.module_running),
        )

    def set_override(self, path: str, **values) -> None:
        path = str(path or "").strip()
        if path:
            self.overrides[path] = dict(values)

    def clear_override(self, path: str) -> None:
        self.overrides.pop(str(path), None)

    def clear(self) -> None:
        self.overrides.clear()

    def can_write(self, path: str):
        return self.source.can_write(path)

    def write(self, path: str, value):
        return self.source.write(path, value)
