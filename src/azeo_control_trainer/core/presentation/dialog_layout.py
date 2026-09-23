"""Keep engineering forms reachable on smaller desktops and at high DPI."""
import logging

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget


def scrolling_body(dialog):
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea(dialog)
    scroll.setWidgetResizable(True)
    body = QWidget()
    scroll.setWidget(body)
    outer.addWidget(scroll)
    return QVBoxLayout(body)


def fit_dialog_to_screen(dialog):
    """Leave the native caption and borders reachable at Windows scaling."""
    available = dialog.screen().availableGeometry()
    dialog.resize(min(dialog.width(), max(1, available.width() - 40)),
                  min(dialog.height(), max(1, available.height() - 80)))
    # Saved coordinates may belong to a monitor that is no longer connected.
    # move() addresses the native frame, unlike setGeometry()'s client rect.
    frame = dialog.frameGeometry()
    target = bounded_window_rect(frame, available)
    dialog.move(target.topLeft())


def bounded_window_rect(frame, available):
    """Clamp a restored frame, including negative-coordinate monitor layouts."""
    width = min(frame.width(), available.width())
    height = min(frame.height(), available.height())
    x = max(available.left(), min(frame.x(), available.right() - width + 1))
    y = max(available.top(), min(frame.y(), available.bottom() - height + 1))
    return QRect(x, y, width, height)


def guarded_action(callback, error_label):
    """A failed diagnostic action must not escape through Qt's signal handler."""
    def invoke(*_args):
        try:
            return callback()
        except Exception as error:  # noqa: BLE001 - Qt signal boundary
            logging.getLogger("engineering.dialog").exception("Diagnostic action failed")
            error_label.setText(str(error))
            return None
    return invoke
