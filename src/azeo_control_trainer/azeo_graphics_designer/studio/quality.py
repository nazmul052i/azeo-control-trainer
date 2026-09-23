"""Cooperative verification and checked, undoable layout corrections."""
from __future__ import annotations

from dataclasses import replace
import logging
import time
import weakref

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem, item_document_data
from azeo_control_trainer.core.hmi.pvms.visual_quality import text_size, visual_findings


def apply_visual_fix(studio, finding):
    """Resolve the identity again: a Problems row may predate Undo or deletion."""
    if studio.mode != "edit" or studio._gesture_open or not studio.store.owns_lock(studio.display.name):
        raise ValueError("Finish editing and open this display for edit before applying a fix")
    current = next((f for f in visual_findings(studio.canvas.scene(), studio.display)
                    if f.item == finding.item and f.code == finding.code and f.fix), None)
    if current is None:
        return False
    item = next((i for i in studio._items() + studio._static_items()
                 if str(getattr(getattr(i, "pvm", None), "id", "") or item_document_data(i).get("id", "")) == finding.item), None)
    if item is None:
        return False
    data = item_document_data(item)
    if getattr(item, "pvm_locked", False) or data.get("locked") or data.get("user_pvm"):
        raise ValueError("Unlock the object or edit its reusable class to correct its layout")
    width, height = item.content_design_size() if isinstance(item, PvmItem) else text_size(item)
    width, height = max(width, item.rect().width()), max(height, item.rect().height())
    studio.checkpoint()
    item.prepareGeometryChange()
    if isinstance(item, PvmItem):
        if item._bar_swapped():
            width, height = height, width
        item.pvm = replace(item.pvm, w=width, h=height)
        item._apply_pvm_rotation()
    else:
        data.update(w=width, h=height)
        item.setRect(0, 0, width, height)
        item.apply_rotation()
    item.update()
    studio.reroute_pipes()
    studio.mark_unsaved()
    studio.selection.replace([item], primary=item)
    return True


class QualityMonitor(QObject):
    """Only the active document is inspected; GUI work stays on the GUI thread."""
    updated = Signal(object, object)
    stateChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._studio = None
        self._iterator = None
        self.enabled = True
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.advance)
        self.last_slice_ms = 0.0

    def studio(self):
        widget = self._studio() if self._studio else None
        return widget if widget is not None and isValid(widget) else None

    def set_studio(self, studio):
        previous = self.studio()
        if previous is not None:
            previous.documentChanged.disconnect(self.schedule)
        self._studio = weakref.ref(studio) if studio is not None else None
        if studio is not None:
            studio.documentChanged.connect(self.schedule)
        self.schedule()

    def stop(self):
        self.timer.stop()
        self._iterator = None

    def schedule(self):
        self.stop()
        if self.enabled and self.studio() is not None and not self.studio()._closing:
            self.stateChanged.emit("Checks pending")
            self.timer.start(650)
        else:
            self.stateChanged.emit("Automatic checks paused")

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.schedule()

    def advance(self):
        studio = self.studio()
        if studio is None or getattr(studio, "_closing", False) or not self.enabled:
            self.stop()
            return
        # A keypress in a property field must not race a partially applied edit.
        if studio._gesture_open or QApplication.mouseButtons():
            self.timer.start(250)
            return
        focus = QApplication.focusWidget()
        if focus and focus.inherits("QLineEdit") and focus.isModified():
            self.timer.start(650)
            return
        start = time.perf_counter()
        try:
            if self._iterator is None:
                from .verification import iter_findings_for_studio
                self._iterator = iter_findings_for_studio(studio)
                self.stateChanged.emit("Checking…")
            while (time.perf_counter() - start) < .004:
                next(self._iterator)
            self.timer.start(10)
        except StopIteration as done:
            self._iterator = None
            findings = done.value
            self.updated.emit(studio, findings)
            errors = sum(f.blocks_publish for f in findings)
            self.stateChanged.emit(f"{errors} errors · {len(findings) - errors} advisories")
        except Exception as error:  # noqa: BLE001 - timer must not tear down Qt
            self.stop()
            logging.getLogger(__name__).exception("Automatic HMI verification failed")
            self.stateChanged.emit(f"Checks unavailable: {error}. Use Verify to retry.")
        finally:
            self.last_slice_ms = (time.perf_counter() - start) * 1000
