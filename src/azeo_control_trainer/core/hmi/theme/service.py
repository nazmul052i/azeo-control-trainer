"""Validated theme identity and notifications for one operator console.

The console applies retained surfaces before committing a selection. Shared Qt
widgets subscribe through an owned binding; each console retains its own theme
so engineering windows and other stations are unaffected.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from .tokens import DEFAULT_THEME, THEMES

THEME_LABELS = {"silver": "Azeo Silver", "dark": "Charcoal Dark",
                "hpgray": "High Performance Gray", "azeo_live": "Azeo Classic"}


class ThemeService(QObject):
    """The session's current theme, and who to tell when it moves."""

    #: Emitted with the new theme name, after `current` has moved.
    changed = Signal(str)

    def __init__(self, theme: str = DEFAULT_THEME, parent=None, *, initial_theme=None):
        super().__init__(parent)
        self._current = theme if theme in THEMES else DEFAULT_THEME
        self.initial = initial_theme if initial_theme in THEMES else DEFAULT_THEME

    @property
    def current(self) -> str:
        return self._current

    @property
    def palette(self) -> dict:
        """The role table for the current theme."""
        return THEMES[self._current]

    def names(self) -> tuple:
        """Every offered theme, with this console's station default first."""
        rest = [n for n in ("silver", "dark", "hpgray", "azeo_live")
                if n in THEMES and n != self.initial]
        rest.extend(sorted(set(THEMES) - {self.initial, *rest}))
        return (self.initial, *rest)

    def is_initial(self, name: str) -> bool:
        """Whether `name` is the theme the session starts in."""
        return name == self.initial

    def set_theme(self, name: str) -> bool:
        """Switch, and tell everyone. False if nothing changed.

        An unknown name is REFUSED rather than falling back to the
        default: silently re-skinning to something the caller did not
        ask for is worse than not moving, because the operator sees a
        change and reads it as the one they requested.
        """
        if name not in THEMES or name == self._current:
            return False
        self._current = name
        self.changed.emit(name)
        return True


#: The session's service. A console may own its own instead — this is
#: the convenient default, not a requirement.
theme_service = ThemeService()
