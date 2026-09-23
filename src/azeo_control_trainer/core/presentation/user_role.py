"""User role management for the fired heater dashboard.

Two roles:
  ADMIN    -- full access: can edit P&ID, change tuning, etc.
  OPERATOR -- run-time only: can operate valves and controllers,
             but cannot modify P&ID layout or advanced settings.

Usage::

    from .user_role import role_manager, Role

    if role_manager.role == Role.ADMIN:
        ...

    role_manager.role_changed.connect(my_slot)
"""
from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QObject, Signal


class Role(Enum):
    ADMIN = "Admin"
    OPERATOR = "Operator"


class RoleManager(QObject):
    """Singleton-style role holder with a Qt signal on change."""

    role_changed = Signal(object)  # emits Role enum value

    def __init__(self, parent=None):
        super().__init__(parent)
        self._role = Role.OPERATOR  # default: operator (least privilege)

    @property
    def role(self) -> Role:
        return self._role

    @role.setter
    def role(self, value: Role):
        if value != self._role:
            self._role = value
            self.role_changed.emit(value)

    @property
    def is_admin(self) -> bool:
        return self._role == Role.ADMIN


# Module-level singleton -- import this everywhere
role_manager = RoleManager()
