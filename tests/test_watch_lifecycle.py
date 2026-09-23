"""A retained Watch window must never present a stopped monitor as live."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.azeo_control_designer.widgets.watch_panel import WatchPanel


def test_reopened_watch_refreshes_immediately_and_continues_polling():
    app = QApplication.instance() or QApplication([])
    terminal = SimpleNamespace(value=1.0, forced=False,
                               data_type=SimpleNamespace(value="FLOAT"))
    block = SimpleNamespace(id="loop", instance_name="Loop", inputs={}, outputs={"PV": terminal})
    panel = WatchPanel()
    try:
        panel.add(block, "PV", "OUT")
        panel.show()
        app.processEvents()
        panel.close()
        assert not panel._timer.isActive()
        terminal.value = 2.0
        panel.show()
        app.processEvents()
        assert panel._table.item(0, 4).text() == "2.0"
        assert panel._timer.isActive()
        terminal.value = 3.0
        assert QSignalSpy(panel._timer.timeout).wait(2000)
        assert panel._table.item(0, 4).text() == "3.0"
        panel.hide()
        assert not panel._timer.isActive()
        del block.outputs["PV"]
        panel.show()
        app.processEvents()
        assert panel._table.item(0, 4).text() == "Unavailable"
        assert not panel._table.cellWidget(0, 6).findChildren(type(panel._btn_clear))[0].isEnabled()
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()
