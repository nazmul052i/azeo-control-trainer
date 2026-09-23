"""Historian plot gestures; both value axes share the same navigation."""
from functools import wraps
import logging

import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, Signal

from azeo_control_trainer.core.presentation.brand import UI


def _guard_gesture(method):
    @wraps(method)
    def guarded(self, event, *args, **kwargs):
        try:
            return method(self, event, *args, **kwargs)
        except Exception as error:
            logging.getLogger(__name__).exception("Historian gesture failed")
            self.rbScaleBox.hide()
            self.navigation.emit("error", str(error))
            event.accept()
    return guarded


class HistoryViewBox(pg.ViewBox):
    navigation = Signal(str, object)

    def __init__(self):
        super().__init__(enableMenu=False)
        self.rbScaleBox.setPen(pg.mkPen(UI.blue, width=1))
        colour = pg.mkColor(UI.selection)
        colour.setAlpha(100)
        self.rbScaleBox.setBrush(pg.mkBrush(colour))
        self._cancelled = False

    @_guard_gesture
    def mouseDragEvent(self, event, axis=None):  # noqa: N802
        if event.button() not in (Qt.LeftButton, Qt.MiddleButton):
            event.ignore()
            return
        event.accept()
        if event.isStart():
            self._cancelled = False
            self.navigation.emit("begin", None)
        if self._cancelled:
            return
        if event.button() == Qt.LeftButton and axis is None:
            if event.isFinish():
                self.rbScaleBox.hide()
                rect = QRectF(event.buttonDownScenePos(), event.scenePos()).normalized()
                # A nearly horizontal/vertical click must not produce a
                # microscopic value or time range.
                if rect.width() >= 6 and rect.height() >= 6:
                    self.navigation.emit("rectangle", rect)
            else:
                self.updateScaleBox(event.buttonDownPos(), event.pos())
        else:
            self.navigation.emit("pan", (event.scenePos() - event.lastScenePos(), axis))
            if event.isFinish():
                self.navigation.emit("finish", None)

    @_guard_gesture
    def wheelEvent(self, event, axis=None):  # noqa: N802
        event.accept()
        self.navigation.emit("wheel", (event.scenePos(), 1.02 ** (event.delta() * -.125), axis))

    @_guard_gesture
    def mouseClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and event.double():
            event.accept()
            self.navigation.emit("fit", None)
        else:
            super().mouseClickEvent(event)

    def cancel_gesture(self):
        self._cancelled = True
        self.rbScaleBox.hide()
