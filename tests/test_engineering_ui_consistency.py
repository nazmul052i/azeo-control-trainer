"""Desktop and font contracts shared by the three engineering applications."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from azeo_control_trainer.app import AreaContext
from azeo_control_trainer.azeo_control_designer import ControlDesignerWindow
from azeo_control_trainer.azeo_explorer import ExplorerWindow
from azeo_control_trainer.azeo_graphics_designer import HmiStudioWindow
from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
from azeo_control_trainer.core.strategy.engine.pk_controller import PKController


@pytest.fixture(scope="module")
def engineering_windows(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("engineering-ui")
    app = QApplication.instance() or QApplication([])
    apply_application_font()
    (tmp_path / "_project.json").write_text('{"name": "UI review"}', encoding="utf-8")
    store = SharedDataStore()
    store.controller = PKController.from_config(None)
    control = ControlDesignerWindow(store=store, plugin=AreaContext(tmp_path))
    explorer = ExplorerWindow(store=store, area=tmp_path, designer=control)
    graphics = HmiStudioWindow(lambda: {}, tmp_path / "displays/pvm")
    windows = {"control": control, "explorer": explorer, "graphics": graphics}
    yield windows
    for window in reversed(tuple(windows.values())):
        window.close()
    app.processEvents()


@pytest.mark.parametrize("name", ("explorer", "control", "graphics"))
def test_engineering_widget_fonts_have_positive_point_sizes(engineering_windows, name):
    window = engineering_windows[name]
    window.show()
    QApplication.processEvents()
    invalid = [(type(child).__name__, child.objectName())
               for child in window.findChildren(QWidget)
               if child.font().pointSizeF() <= 0]
    assert not invalid, invalid


def test_control_ribbon_does_not_force_the_desktop_width(engineering_windows):
    window = engineering_windows["control"]
    window.setMinimumSize(0, 0)
    window.resize(920, 600)
    window.show()
    QApplication.processEvents()
    for index in range(window._ribbon._pages.count()):
        window._ribbon._tab_bar.setCurrentIndex(index)
        QApplication.processEvents()
        assert window.width() == 920
        assert window.minimumSizeHint().width() <= 920
