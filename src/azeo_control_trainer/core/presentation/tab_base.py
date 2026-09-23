"""Base class for all main-window tabs."""
from __future__ import annotations

from PySide6.QtWidgets import QWidget


class BaseTab(QWidget):
    """Abstract base for tabs in the main window.

    Subclasses override ``on_tick`` to receive 1 Hz live updates
    (only called when the tab is active).
    """

    #: When True the tab is only visible to Admin users. Gating on this
    #: class flag (not the tab's display label) keeps a rename from
    #: silently dropping access control.
    admin_only: bool = False

    def __init__(self, store=None, provider=None, parent=None, **kwargs):
        super().__init__(parent)
        self._store = store
        self._provider = provider
        self._active = False

    def set_active(self, active: bool):
        """Called by MainWindow when this tab becomes active/inactive."""
        self._active = active

    def on_tick(self):
        """Called at ~1 Hz when this tab is the active tab.

        Subclasses should read from ``self._store`` and update their
        widgets here.
        """

    def refresh_background(self):
        """Called at ~1 Hz while this tab is INACTIVE.

        Lets a tab keep detached popups / trend dialogs live even when the
        user is on another tab. The default refreshes a faceplate manager
        if the tab registered one as ``self._fp_mgr`` (no-op otherwise), so
        the host window doesn't need to know which tabs own managers.
        """
        mgr = getattr(self, "_fp_mgr", None)
        if mgr is not None and mgr.has_open_views():
            mgr.refresh_all()

    def cleanup(self):
        """Called when the application is closing."""
