from __future__ import annotations

from dataclasses import dataclass

from .live_monitor import MonitorSnapshot


@dataclass(frozen=True)
class ReadinessResult:
    status: str
    message: str
    blocking: list[str]
    warnings: list[str]
    snapshot: MonitorSnapshot

    @property
    def ok(self) -> bool:
        return self.status == "READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "message": self.message,
            "blocking": self.blocking,
            "warnings": self.warnings,
            "snapshot": self.snapshot.as_dict(),
        }


class ReadinessEvaluator:
    """Evaluate if a procedure is safe enough to start in advisory/read-only mode."""

    def __init__(self, allow_stale: bool = False, allow_uncertain: bool = False):
        self.allow_stale = allow_stale
        self.allow_uncertain = allow_uncertain

    def evaluate(self, snapshot: MonitorSnapshot) -> ReadinessResult:
        blocking: list[str] = []
        warnings: list[str] = []
        for row in snapshot.rows:
            if not row.is_good:
                text = f"{row.tag}: {row.quality} {row.message}".strip()
                if row.is_stale and self.allow_stale:
                    warnings.append(text)
                elif row.quality.lower() == "uncertain" and self.allow_uncertain:
                    warnings.append(text)
                else:
                    blocking.append(text)
        if blocking:
            return ReadinessResult(
                status="NOT_READY",
                message=f"{len(blocking)} blocking tag condition(s) detected.",
                blocking=blocking,
                warnings=warnings,
                snapshot=snapshot,
            )
        if warnings:
            return ReadinessResult(
                status="READY_WITH_WARNINGS",
                message=f"Ready with {len(warnings)} warning(s).",
                blocking=blocking,
                warnings=warnings,
                snapshot=snapshot,
            )
        return ReadinessResult(
            status="READY",
            message="All monitored tags are good and fresh.",
            blocking=blocking,
            warnings=warnings,
            snapshot=snapshot,
        )
