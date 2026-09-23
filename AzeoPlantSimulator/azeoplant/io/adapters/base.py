# -*- coding: utf-8 -*-
"""The adapter contract."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass
class AdapterHealth:
    name: str
    connected: bool = False
    published: int = 0        # simulator-owned values sent out
    received: int = 0         # external writes handed to the bus
    rejected: int = 0         # writes the bus or the wire refused
    last_error: str = ""
    detail: dict = field(default_factory=dict)


class VirtualIOAdapter(abc.ABC):
    """Moves signals between the bus and one outside world.

    Lifecycle is synchronous from the runtime's point of view; an adapter
    that needs an event loop (OPC UA) owns its thread internally, the way
    the existing server always has.
    """

    name = "adapter"

    def __init__(self, bus) -> None:
        self.bus = bus

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def stop(self) -> None: ...

    @abc.abstractmethod
    def publish_inputs(self) -> None:
        """Push simulator-owned values to the transport (no-op where the
        transport pulls or subscribes on its own)."""

    @abc.abstractmethod
    def receive_outputs(self) -> None:
        """Move pending external writes onto the bus (no-op where the
        transport pushes into the bus directly)."""

    @abc.abstractmethod
    def health(self) -> AdapterHealth: ...
