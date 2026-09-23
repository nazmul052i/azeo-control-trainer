"""Faceplate families share one disposable, non-polling hidden lifecycle."""
import gc
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QWidget
from azeoplant.core.tags import Tag, TagDatabase, TagKind
from azeoplant.ui.faceplate import FaceplateDialog
from azeoplant.ui.main_window import MainWindow


@pytest.mark.parametrize("dismiss", ["close", "reject", "accept", "button"])
def test_reopening_basic_faceplate_does_not_retain_hidden_timers(dismiss):
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.db = TagDatabase()
    owner.db.add(Tag("REVIEW-DI", TagKind.DI, "Review", "Lifecycle"))
    owner.engine = SimpleNamespace(controller=None)
    owner._device_registry = {}
    owner._open_faceplates = {}
    refs = []
    try:
        for _ in range(5):
            MainWindow.open_faceplate(owner, "REVIEW-DI")
            dialog = owner._open_faceplates["REVIEW-DI"]
            refs.append(weakref.ref(dialog))
            if dismiss == "button":
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Close).click()
            else:
                getattr(dialog, dismiss)()
            assert not dialog._timer.isActive()
            del dialog
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            gc.collect()
        assert not owner._open_faceplates
        assert not owner.findChildren(FaceplateDialog)
        assert all(ref() is None for ref in refs)
    finally:
        owner.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("family", ["analog", "loop", "device"])
def test_faceplate_details_release_on_escape_and_can_reopen(family):
    from azeoplant.models.flowsheet import Flowsheet
    from azeoplant.control.strategy import ControlSystem
    from azeoplant.io import VirtualIOBus
    from azeoplant.ui.ai_faceplate import AiFaceplate, AnalogMonitor
    from azeoplant.ui.loop_faceplate import LoopFaceplate
    from azeoplant.ui.device_faceplate import DeviceFaceplate, DeviceAdapter
    app = QApplication.instance() or QApplication([])
    db = TagDatabase()
    Flowsheet(db, dt=.1)
    controller = ControlSystem(db, VirtualIOBus(db))
    owner = QWidget()
    if family == "analog":
        dialog = AiFaceplate(AnalogMonitor(next(t for t in db.all() if t.kind is TagKind.AI)), owner)
    elif family == "loop":
        dialog = LoopFaceplate(next(iter(controller.loops.values())), controller, parent=owner)
    else:
        from azeoplant.models.base import ProcessUnit
        from azeoplant.models.packages import MotorPackage
        class DeviceUnit(ProcessUnit):
            def build(self):
                pass
        unit = DeviceUnit(db, {})
        package = MotorPackage(unit, "REVIEW-1", "Lifecycle motor")
        dialog = DeviceFaceplate(DeviceAdapter("REVIEW-1", package, "Review", "Lifecycle motor"), owner)
    try:
        dialog.show()
        for _ in range(3):
            dialog._open_detail()
            detail = dialog._detail
            assert detail._timer.isActive()
            detail.reject()
            assert not detail._timer.isActive()
            assert dialog._detail is None
            ref = weakref.ref(detail)
            del detail
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            gc.collect()
            assert ref() is None
        dialog.reject()
        assert not dialog._timer.isActive()
    finally:
        owner.deleteLater()
        app.processEvents()
