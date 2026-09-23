"""Lifecycle for disposable simulator faceplates and their detail windows."""
import weakref

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog


class LiveDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)

    def hideEvent(self, event):  # noqa: N802
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):  # noqa: N802
        timer = getattr(self, "_timer", None)
        if timer is not None:
            self._refresh()
            timer.start()
        super().showEvent(event)


def retain_faceplate(owner, key, dialog):
    owner._open_faceplates[key] = dialog
    owner_ref, dialog_ref = weakref.ref(owner), weakref.ref(dialog)

    def forgotten(*_):
        parent, old = owner_ref(), dialog_ref()
        if parent is not None and parent._open_faceplates.get(key) is old:
            parent._open_faceplates.pop(key, None)

    # Clear immediately on completion, before another click can reuse a window
    # that is already scheduled for deferred deletion.
    dialog.finished.connect(forgotten)
    dialog.destroyed.connect(forgotten)


def retain_detail(owner, dialog):
    owner._detail = dialog
    owner_ref, dialog_ref = weakref.ref(owner), weakref.ref(dialog)

    def forgotten(*_):
        parent, old = owner_ref(), dialog_ref()
        if parent is not None and parent._detail is old:
            parent._detail = None

    dialog.finished.connect(forgotten)
    dialog.destroyed.connect(forgotten)
