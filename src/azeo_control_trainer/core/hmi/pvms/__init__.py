"""The PVM class system (HMI proposal §10, PVM_CLASS_CATALOG.md).

Importing this package registers the shipped PVM classes. Rendering is
the subclass's job in the display layer; everything here is Qt-free —
declarations, the registry, the §6 display-state contract.
"""
from . import (  # noqa: F401
    analog, composite, control, coverage, device, dynamos, machines, process,
    sequence, signal,
)
# The High Performance variants register themselves on import, the
# same way every other class module does.
from .hp import classes as _hp_classes  # noqa: F401,E402
from .hp import fb_classes as _fb_classes  # noqa: F401,E402
from .hp import fb_faceplates as _fb_faceplates  # noqa: F401,E402
from .base import (
    BAD_VALUE_TEXT, Bind, DisplayState, Pvm, PvmClass, PvmRegistry,
    register_pvm, registry, resolve_state,
)
from .roles import Tier

__all__ = ["BAD_VALUE_TEXT", "Bind", "DisplayState", "Pvm", "PvmClass",
           "PvmRegistry", "Tier", "register_pvm", "registry",
           "resolve_state"]
