"""An Ethernet I/O Card node for external OPC UA device integration.

The PK controller's embedded OPC UA endpoint is a *server* face for clients
that consume controller data. Browsing a third-party server and bringing its
signals into the control system is the EIOC's client role. Keeping that role
as a separate engineering node prevents external OPC signals from being
misreported as PK DST usage or as controller-native I/O cards.
"""
from __future__ import annotations

from dataclasses import dataclass, field


EIOC_SIGNAL_LIMIT = 30_000
EIOC_DEVICE_LIMIT = 64


@dataclass(frozen=True)
class EthernetIoCard:
    """One configured EIOC and the external signals it owns."""

    name: str = "EIOC-1"
    description: str = "External OPC UA client"
    field_io: dict = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict | None) -> "EthernetIoCard | None":
        """Build a node from an area's ``eioc`` declaration."""
        config = dict(config or {})
        field_io = dict(config.get("field_io") or {})
        if field_io.get("type") != "opcua":
            return None
        return cls(
            name=str(config.get("name") or "EIOC-1"),
            description=str(config.get("description") or
                            "External OPC UA client"),
            field_io=field_io,
        )

    @property
    def endpoint(self) -> str:
        return str(self.field_io.get("endpoint") or "")

    @property
    def signals(self) -> dict[str, dict]:
        return dict(self.field_io.get("signals") or {})

    @property
    def signal_count(self) -> int:
        return len(self.signals)

    @property
    def read_count(self) -> int:
        return sum(1 for spec in self.signals.values()
                   if str(spec.get("direction") or "read").lower() == "read")

    @property
    def write_count(self) -> int:
        return self.signal_count - self.read_count

    @property
    def signal_limit(self) -> int:
        return EIOC_SIGNAL_LIMIT

    @property
    def device_limit(self) -> int:
        return EIOC_DEVICE_LIMIT

    @property
    def over_capacity(self) -> bool:
        return self.signal_count > self.signal_limit

    def identity_line(self) -> str:
        """Compact diagnostics text for Explorer and startup logging."""
        return (f"{self.name} · OPC UA client · {self.signal_count} of "
                f"{self.signal_limit} signals · 1 of {self.device_limit} "
                "devices")
