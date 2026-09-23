"""``RolePalette`` — the one object that turns a role into a colour.

A painter is handed a palette and asks it for roles. It never sees a theme
name, never sees a hex string, and cannot reach past the palette to a literal.
That is invariant I1 expressed as an interface rather than a rule people are
asked to remember.

    palette = RolePalette.for_theme("hpgray")
    painter.setPen(palette.pen(Role.EQUIPMENT))
    painter.setBrush(palette.brush(Role.EQUIPMENT_FILL))

Qt objects are cached per palette: a dynamo asks for the same handful of roles
on every repaint, and building a ``QColor`` each time is pure waste in the one
loop that has to stay cheap.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPen

from .roles import Role
from .tokens import DEFAULT_THEME, THEMES


class UnknownTheme(KeyError):
    """Raised rather than silently falling back to a default theme.

    A typo in a theme name that quietly renders the wrong palette is the kind
    of defect that survives review, so it is loud.
    """


class RolePalette:
    """Resolved colours for one theme."""

    __slots__ = ("_theme", "_values", "_colours", "_pens", "_brushes")

    def __init__(self, theme: str, values: dict):
        self._theme = theme
        self._values = values
        self._colours: dict[str, QColor] = {}
        self._pens: dict[tuple, QPen] = {}
        self._brushes: dict[str, QBrush] = {}

    # ------------------------------------------------------------ building
    @classmethod
    def for_theme(cls, theme: str | None = None) -> "RolePalette":
        name = theme or DEFAULT_THEME
        try:
            values = THEMES[name]
        except KeyError as exc:
            raise UnknownTheme(
                f"unknown theme {name!r}; available: "
                f"{', '.join(sorted(THEMES))}") from exc
        return cls(name, values)

    @classmethod
    def themes(cls) -> list[str]:
        return sorted(THEMES)

    @property
    def theme(self) -> str:
        return self._theme

    # ------------------------------------------------------------- lookup
    def hex(self, role: Role) -> str:
        """The literal for a role. The *only* sanctioned way to obtain one."""
        try:
            return self._values[role.value if isinstance(role, Role) else role]
        except KeyError as exc:
            raise KeyError(
                f"role {role!r} is not defined in theme {self._theme!r}. "
                f"Add it to schema/tokens.json and regenerate.") from exc

    def color(self, role: Role) -> QColor:
        key = role.value if isinstance(role, Role) else str(role)
        cached = self._colours.get(key)
        if cached is None:
            cached = QColor(self.hex(role))
            self._colours[key] = cached
        return cached

    def pen(self, role: Role, width: float = 1.0,
            style: Qt.PenStyle = Qt.SolidLine) -> QPen:
        key = (role.value if isinstance(role, Role) else str(role), width, style)
        cached = self._pens.get(key)
        if cached is None:
            cached = QPen(self.color(role), width, style)
            cached.setCosmetic(True)
            self._pens[key] = cached
        return cached

    def brush(self, role: Role) -> QBrush:
        key = role.value if isinstance(role, Role) else str(role)
        cached = self._brushes.get(key)
        if cached is None:
            cached = QBrush(self.color(role))
            self._brushes[key] = cached
        return cached

    def alpha(self, role: Role, alpha: float) -> QColor:
        """A role at reduced opacity — for a shaded band, not a new colour.

        Deliberately the only modifier offered. Lightening or darkening a role
        to invent a variant is how a palette grows colours nobody approved.
        """
        colour = QColor(self.color(role))
        colour.setAlphaF(max(0.0, min(1.0, alpha)))
        return colour

    def __repr__(self) -> str:                             # pragma: no cover
        return f"<RolePalette {self._theme} roles={len(self._values)}>"


@lru_cache(maxsize=8)
def palette_for(theme: str | None = None) -> RolePalette:
    """Shared palette per theme. Palettes are immutable, so sharing is safe."""
    return RolePalette.for_theme(theme)
