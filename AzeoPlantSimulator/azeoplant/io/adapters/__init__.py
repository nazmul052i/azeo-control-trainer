"""Integration adapters: transport only, bus only.

An adapter moves signals between the :class:`~azeoplant.io.bus.VirtualIOBus`
and some outside world - in-process for tests and scripting, OPC UA for a
real or virtual DCS. Process and device code never import an adapter, and
an adapter never touches the tag database directly.
"""

from ..sample import SignalSample
from .base import AdapterHealth, VirtualIOAdapter
from .local import LocalAdapter
from .opcua import OpcUaAdapter

__all__ = ["VirtualIOAdapter", "AdapterHealth", "SignalSample", "LocalAdapter",
           "OpcUaAdapter"]
