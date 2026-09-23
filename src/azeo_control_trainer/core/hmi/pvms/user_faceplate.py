"""Runtime host for Studio-authored user faceplates.

This is deliberately a thin document adapter over ``PvmDisplayView``.  A user
faceplate is assembled from the same drawing items as a display and a PVM, and
is painted by the same renderer; the adapter merely instantiates its class at
the origin and gives that scene a contextual window.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame

from .configurator.model import PvmConfiguration
from .publishing import PvmDisplay
from .rendering.viewer import PvmDisplayView
from .user_library import UserPvmLibrary


class UserFaceplateView(PvmDisplayView):
    """A live, read-only instance of one user-authored faceplate class."""

    def __init__(self, name: str, root, graphs_provider, *, source=None,
                 choices=None, theme: str = "azeo_live", parent=None,
                 action_handler=None, write_handler=None,
                 write_checker=None):
        root = Path(root)
        library = UserPvmLibrary(root)
        entry = library.entries.get(name)
        if entry is None \
                or entry.get("definition_kind", "pvm") not in {"faceplate", "detail"}:
            raise KeyError(f"unknown user faceplate {name!r}")
        config_path = root / "_pvmcfg" / f"{name}.pvmcfg.json"
        config = PvmConfiguration.load(config_path) \
            if config_path.exists() else None

        def standard(standard_name: str):
            try:
                from .standards import StandardsStore
                from .class_revisions import standards_root
                found = StandardsStore(standards_root(root)).get(standard_name)
                return found["value"] if found else None
            except Exception:                       # noqa: BLE001
                return None

        items = library.instantiate(
            name, 0.0, 0.0, config=config, choices=dict(choices or {}),
            standards=standard)
        document = PvmDisplay(
            name=name, items=items,
            width=max(1, int(entry.get("w", 1))),
            height=max(1, int(entry.get("h", 1))),
            view_type="scale_to_frame" if entry.get("definition_kind") == "detail" else "actual_size").to_dict()
        super().__init__(
            document, graphs_provider, theme=theme, config_root=root,
            source=source, action_handler=action_handler,
            write_handler=write_handler, write_checker=write_checker,
            parent=parent)
        self.class_name = name
        self.instance_choices = dict(choices or {})
        self.pinned = False
        from .render import ContextualTitleBar
        self.context_title = ContextualTitleBar(name, "", self)
        self.context_title.pin_requested.connect(self.toggle_pin)
        self.context_title.close_requested.connect(self.close)
        self.context_title.minimize.hide()
        self.setFrameShape(QFrame.NoFrame)
        title_height = self.context_title.sizeHint().height()
        self.setViewportMargins(0, title_height, 0, 0)
        self.setWindowTitle(name)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.resize(max(120, int(entry.get("w", 1))),
                    max(80, int(entry.get("h", 1))) + title_height)
        self.apply_theme(theme)

    def toggle_pin(self) -> bool:
        self.pinned = not self.pinned
        self.context_title.set_pinned(self.pinned)
        return self.pinned

    def apply_theme(self, theme):
        if not super().apply_theme(theme):
            return False
        from ..theme.widgets import apply_role_palette
        # The scene paints its own controls. Broad widget CSS here also
        # styles the viewport and adds native style work to every repaint.
        apply_role_palette(self, self.palette_roles)
        self.context_title.apply_theme(self.palette_roles)
        return True

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "context_title"):
            self.context_title.setGeometry(0, 0, self.width(), self.context_title.sizeHint().height())


__all__ = ["UserFaceplateView"]
