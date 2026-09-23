"""Visual PVM and faceplate class configuration in Graphics Designer.

The reusable, Qt-free configuration model remains in
``core.hmi.pvms.configurator`` so both authoring and runtime use one contract.
"""

from azeo_control_trainer.core.hmi.pvms.configurator import (
    PvmConfiguration,
    PvmProperty,
    Presence,
    PropertyGroup,
)

__all__ = ["PvmConfiguration", "PvmProperty", "Presence", "PropertyGroup"]
