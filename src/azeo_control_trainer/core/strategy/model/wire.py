"""Wire connection between block terminals."""
from __future__ import annotations

from dataclasses import dataclass
import uuid


@dataclass
class Wire:
    """A connection from one block's output terminal to another block's input terminal."""

    id: str
    src_block_id: str
    src_terminal: str  # output terminal name
    dst_block_id: str
    dst_terminal: str  # input terminal name
    is_bkcal: bool = False  # Back-calculation wire (feedback edge)
    # Operator-pinned orthogonal route, as [[x, y], …] scene points. None means
    # "route computed automatically". Canvas-only presentation state — it never
    # affects execution — but it must persist, or a hand-routed diagram silently
    # reverts to auto-routing on reload.
    route_points: list | None = None

    def __init__(
        self,
        src_block_id: str,
        src_terminal: str,
        dst_block_id: str,
        dst_terminal: str,
        is_bkcal: bool = False,
        wire_id: str = None,
        route_points: list | None = None,
    ):
        self.id = wire_id or uuid.uuid4().hex[:12]
        self.src_block_id = src_block_id
        self.src_terminal = src_terminal
        self.dst_block_id = dst_block_id
        self.dst_terminal = dst_terminal
        self.is_bkcal = is_bkcal
        self.route_points = route_points

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "src_block_id": self.src_block_id,
            "src_terminal": self.src_terminal,
            "dst_block_id": self.dst_block_id,
            "dst_terminal": self.dst_terminal,
        }
        # Absent when default (I1): shipped modules carry no is_bkcal key —
        # BKCAL identity lives on the *terminals* — and unconditionally
        # writing `false` here made every load→save of a wired module churn
        # the file. Found by the Phase 0 byte-idempotence sweep.
        if self.is_bkcal:
            d["is_bkcal"] = True
        # Omitted when auto-routed so existing files stay byte-identical.
        if self.route_points:
            d["route_points"] = self.route_points
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Wire:
        return cls(
            src_block_id=data["src_block_id"],
            src_terminal=data["src_terminal"],
            dst_block_id=data["dst_block_id"],
            dst_terminal=data["dst_terminal"],
            is_bkcal=data.get("is_bkcal", False),
            wire_id=data["id"],
            route_points=data.get("route_points"),
        )
