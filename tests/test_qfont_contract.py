"""Qt-owned editors must never receive the canvas's pixel-sized fonts."""
from __future__ import annotations

import os
import sys
from tempfile import TemporaryDirectory
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QEvent, QObject, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from azeo_control_trainer.core.hmi.theme.fonts import (
    FontRole,
    apply_application_font,
    font_for,
)


def _library_item(tree, predicate):
    def walk(item):
        payload = item.data(0, Qt.UserRole)
        if payload and predicate(payload):
            return item
        for index in range(item.childCount()):
            match = walk(item.child(index))
            if match is not None:
                return match
        return None

    for index in range(tree.topLevelItemCount()):
        match = walk(tree.topLevelItem(index))
        if match is not None:
            return match
    return None


def test_editable_combo_never_copies_an_invalid_point_size() -> None:
    """Repair global and class-specific fonts before Qt creates its editor."""
    app = QApplication.instance() or QApplication([])
    messages: list[str] = []

    def capture(_kind, _context, message: str) -> None:
        messages.append(message)

    previous_handler = qInstallMessageHandler(capture)
    combo = None
    try:
        # QApplication stores this override independently from its default.
        # It recreates the Windows failure even after the global font is safe.
        for widget_class in (
            "QComboBox", "QFontComboBox", "QLineEdit", "QAbstractSpinBox",
            "QSpinBox", "QDoubleSpinBox", "QMenu", "QToolTip",
        ):
            app.setFont(font_for(FontRole.CHROME), widget_class)
        messages.clear()  # The deliberately invalid setup is not the test.

        apply_application_font()
        combo = QComboBox()
        combo.setEditable(True)
        app.processEvents()
    finally:
        if combo is not None:
            combo.close()
        qInstallMessageHandler(previous_handler)

    for widget_class in (
        "QComboBox", "QFontComboBox", "QLineEdit", "QAbstractSpinBox",
        "QSpinBox", "QDoubleSpinBox", "QMenu", "QToolTip",
    ):
        widget_font = app.font(widget_class)
        assert widget_font.pointSizeF() > 0, widget_class
        assert widget_font.pixelSize() == -1, widget_class
    assert not [message for message in messages
                if "QFont::setPointSize" in message]


def test_graphics_library_preview_never_emits_invalid_font_warning() -> None:
    """Opening and previewing an installed class stays warning-free."""
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401
    from azeo_control_trainer.azeo_graphics_designer.window import (
        HmiStudioWindow,
    )
    from azeo_control_trainer.core.strategy.model.block_registry import (
        BlockRegistry,
    )
    from azeo_control_trainer.core.strategy.model.strategy_graph import (
        StrategyGraph,
    )

    app = QApplication.instance() or QApplication([])
    block = BlockRegistry().create("PID", "PID1")
    assert block is not None
    block._apply_config()
    graph = StrategyGraph(name="UNIT100")
    graph.add_block(block)
    messages: list[str] = []

    def capture(_kind, _context, message: str) -> None:
        messages.append(message)

    previous_handler = qInstallMessageHandler(capture)
    window = None
    try:
        with TemporaryDirectory() as root:
            window = HmiStudioWindow(
                lambda: {"UNIT100": graph}, root, area_name="Plant")
            window.show()
            class_item = _library_item(
                window.library.tree,
                lambda payload: payload[0] == "class"
                and payload[1][1] == "dynamo_compact")
            assert class_item is not None
            window.library._select_tree_item(class_item)
            QTest.qWait(250)
            invalid_fonts = [
                (type(widget).__name__, widget.objectName())
                for widget in window.findChildren(QWidget)
                if widget.font().pointSizeF() <= 0
            ]
            assert invalid_fonts == []
            # Ribbon contents may scroll on a compact/high-DPI desktop; they
            # must not become the native window's minimum-track width.
            assert window.minimumSizeHint().width() < 1200
    finally:
        if window is not None:
            window.close()
        qInstallMessageHandler(previous_handler)
        app.processEvents()

    assert not [message for message in messages
                if "QFont::setPointSize" in message]


def test_opening_another_product_does_not_restyle_existing_editors() -> None:
    """Reapplying Studio's font must leave live native editors alone."""
    app = QApplication.instance() or QApplication([])
    apply_application_font()
    combo = QComboBox()
    combo.setEditable(True)
    combo.show()
    app.processEvents()

    class FontChanges(QObject):
        def __init__(self):
            super().__init__()
            self.events = []

        def eventFilter(self, obj, event):
            if event.type() in (QEvent.FontChange, QEvent.ApplicationFontChange):
                self.events.append((type(obj).__name__, event.type()))
            return False

    changes = FontChanges()
    app.installEventFilter(changes)
    try:
        apply_application_font()
        app.processEvents()
        assert changes.events == []
        assert combo.lineEdit().font().pointSizeF() > 0
    finally:
        app.removeEventFilter(changes)
        combo.close()
