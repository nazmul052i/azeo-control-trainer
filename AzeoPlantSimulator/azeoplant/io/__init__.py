"""The virtual I/O boundary: the one doorway between the plant and any DCS.

The plant models keep writing their tags directly - they are the physics
and own their measurements. Everything that enters from OUTSIDE the plant
(the internal software DCS, an external DCS over an adapter, an instructor
force) comes through :class:`~azeoplant.io.bus.VirtualIOBus`, which
enforces ownership, audits forcing, drains queued writes between
integration steps and tracks staleness. Adapters live in
:mod:`azeoplant.io.adapters` and never touch the tag database directly.
"""

from .ownership import OwnershipViolation, SignalOwner, owner_of
from .bus import VirtualIOBus
from .sample import SignalSample

__all__ = ["VirtualIOBus", "SignalSample", "SignalOwner",
           "OwnershipViolation", "owner_of"]
