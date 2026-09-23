# -*- coding: utf-8 -*-
"""The OPC UA transport as an adapter.

The wire behaviour lives in :class:`azeoplant.opc.server.OpcUaServer` -
address space, node identifiers, publish cadence, clamp-and-writeback on
client writes - and is deliberately unchanged: the trainer's EIOC driver
subscribes to it today. This class rehomes that server behind the adapter
contract: the runtime starts and stops it here, its client writes are
arbitrated by the bus (``server.bus``), and its statistics surface as
adapter health.
"""

from __future__ import annotations

from .base import AdapterHealth, VirtualIOAdapter


class OpcUaAdapter(VirtualIOAdapter):
    name = "opcua"

    def __init__(self, bus, server) -> None:
        super().__init__(bus)
        self.server = server
        server.bus = bus

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.server.start()

    def stop(self) -> None:
        self.server.stop()

    # ------------------------------------------------------------- moving
    def publish_inputs(self) -> None:
        pass    # the server publishes on its own cadence from the live tags

    def receive_outputs(self) -> None:
        pass    # client writes arrive through the server's subscription,
                # arbitrated by bus.authorize_dcs_write

    def health(self) -> AdapterHealth:
        s = self.server.stats
        return AdapterHealth(
            name=self.name,
            connected=bool(getattr(s, "running", False)),
            published=getattr(s, "publishes", 0),
            received=getattr(s, "writes_in", 0),
            rejected=getattr(s, "rejected_writes", 0),
            last_error=getattr(s, "last_error", "") or "",
            detail={"endpoint": getattr(self.server, "endpoint", "")},
        )
