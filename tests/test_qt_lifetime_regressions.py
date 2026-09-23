"""Closing a tool must release its work, including Escape and button paths."""
import gc
import os
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QWidget


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def collect(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()


@pytest.mark.parametrize("dismiss", ["close", "reject", "accept", "button"])
def test_block_diagnostics_releases_closed_dialogs(app, dismiss):
    from azeo_control_trainer.azeo_control_designer.dialogs.block_diagnostics_dialog import BlockDiagnosticsDialog
    from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
    owner = QWidget()
    refs = []
    try:
        for _ in range(3):
            dialog = BlockDiagnosticsDialog(PIDBlock("PID1"), owner)
            dialog.show()
            if dismiss == "button":
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Close).click()
            else:
                getattr(dialog, dismiss)()
            refs.append(weakref.ref(dialog))
            del dialog
            collect(app)
        assert not owner.findChildren(BlockDiagnosticsDialog)
        assert all(ref() is None for ref in refs)
    finally:
        owner.deleteLater()
        collect(app)


@pytest.fixture
def studio(app, tmp_path):
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
    from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
    store = DisplayStore(tmp_path / "pvm")
    store.save_draft(PvmDisplay(name="Review"))
    window = PvmStudio(lambda: {}, store.root, "Review")
    yield window
    window.unsaved = False
    window.close()
    window.deleteLater()
    collect(app)


def test_replaced_canvas_menus_release_actions_and_widgets(app, studio):
    refs = []
    for _ in range(12):
        studio.canvas_context_menu(QPoint(0, 0))
        refs.append(weakref.ref(studio._context_menu))
        studio._context_menu.close()
        collect(app)
    assert sum(ref() is not None and shiboken6.isValid(ref()) for ref in refs[:-1]) == 0


@pytest.mark.parametrize("dismiss", ["close", "reject", "accept", "escape"])
def test_sequence_completion_restores_previous_overlay(app, studio, dismiss):
    from azeo_control_trainer.azeo_graphics_designer.sequence_tools import SequenceDialog
    from azeo_control_trainer.core.hmi.binding.result import BindingResult
    studio.preview_source.source.read = lambda *_: BindingResult(value=37)
    studio.preview_source.set_override("KEEP/AI/PV", value=25)
    sequence = SequenceDialog(studio)
    sequence.setAttribute(Qt.WA_DeleteOnClose)
    sequence.path.setText("M/AI/PV")
    sequence.add_lifecycle()
    sequence.show()
    sequence.play()
    if dismiss == "escape":
        QTest.keyClick(sequence, Qt.Key_Escape)
    else:
        getattr(sequence, dismiss)()
    collect(app)
    assert not studio.test_mode
    assert studio.preview_source.overrides == {"KEEP/AI/PV": {"value": 25}}


def test_history_comparisons_dispose_after_escape(app):
    from azeo_control_trainer.core.hmi.history import ContinuousHistorian
    from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView
    historian = ContinuousHistorian(None)
    historian.add_point("LOOP/PID/PV", module="LOOP").sample(0, 15)
    history = ProcessHistoryView(historian, "LOOP")
    from PySide6.QtWidgets import QMenu
    collect(app)
    menus_before = sum(isinstance(widget, QMenu) for widget in app.allWidgets())
    refs = []
    try:
        for _ in range(3):
            history._compare_runs()
            dialog = history._comparison_dialogs[-1]
            rows = [{"time": t, "pv": value, "sp": 10, "out": 30, "quality": "GOOD"}
                    for t, value in ((0, 0), (1, 12), (2, 10), (7, 10))]
            dialog.set_windows({"baseline": rows, "trial": rows}, "LOOP/PID")
            assert len(dialog.chart._pens) == 6
            refs.append(weakref.ref(dialog))
            refs.append(weakref.ref(dialog.chart))
            refs.extend(weakref.ref(values) for _, values in dialog.chart._data_cache.values())
            QTest.keyClick(dialog, Qt.Key_Escape)
            del dialog
            collect(app)
        assert not history._comparison_dialogs
        assert all(ref() is None for ref in refs)
        assert sum(isinstance(widget, QMenu) for widget in app.allWidgets()) <= menus_before
    finally:
        history.close()
        history.deleteLater()
        collect(app)


@pytest.mark.parametrize("dismiss", ["close", "reject", "accept"])
def test_command_faceplate_releases_pending_pulse_on_completion(app, dismiss):
    from azeo_control_trainer.core.strategy.blocks.device_blocks import DevctlBlock
    from azeo_control_trainer.azeo_control_designer.faceplates.device_faceplate import DevctlFaceplate
    block = DevctlBlock("review-device")
    dialog = DevctlFaceplate(block)
    try:
        dialog.show()
        dialog._btn_off.click()
        assert block.inputs["STOP_CMD"].value is True
        getattr(dialog, dismiss)()
        assert block.inputs["STOP_CMD"].value is False
    finally:
        dialog.deleteLater()
        collect(app)


def test_reapplying_application_style_does_not_replace_live_qt_style(app, monkeypatch):
    from azeo_control_trainer.core.presentation.application_style import apply_application_style
    apply_application_style(app)
    original = app.style()
    monkeypatch.setattr(app, "setStyle", lambda *_: pytest.fail("Replaced live Qt style"))
    apply_application_style(app)
    assert app.style() is original


def test_sfc_debugger_stops_when_hidden_and_disposes_on_escape(app):
    from azeo_control_trainer.core.strategy.blocks.sfc_chart_block import SfcChartBlock
    from azeo_control_trainer.azeo_control_designer.dialogs.sfc_debug_dialog import SfcDebugDialog
    owner = QWidget()
    dialog = SfcDebugDialog(SfcChartBlock("Review"), owner)
    try:
        dialog.show()
        dialog.hide()
        assert not dialog._timer.isActive()
        dialog.show()
        assert dialog._timer.isActive()
        QTest.keyClick(dialog, Qt.Key_Escape)
        collect(app)
        assert not shiboken6.isValid(dialog)
    finally:
        owner.deleteLater()
        collect(app)


def test_control_canvas_contains_bad_drop_and_accepts_next_valid_drop(app):
    from PySide6.QtCore import QMimeData, QPointF
    from PySide6.QtGui import QDropEvent
    from PySide6.QtTest import QSignalSpy
    from azeo_control_trainer.azeo_control_designer.canvas.strategy_scene import StrategyScene
    from azeo_control_trainer.azeo_control_designer.canvas.strategy_view import StrategyView, BLOCK_MIME_TYPE
    scene = StrategyScene()
    view = StrategyView(scene)
    errors = QSignalSpy(view.uiError)
    mime = QMimeData()
    try:
        mime.setData(BLOCK_MIME_TYPE, b"\xff")
        event = QDropEvent(QPointF(40, 40), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        view.dropEvent(event)
        assert errors.count() == 1
        assert event.isAccepted()
        assert not scene._wiring
        mime.setData(BLOCK_MIME_TYPE, b"PID")
        view.dropEvent(QDropEvent(QPointF(40, 40), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
        assert errors.count() == 1
        assert len(scene.graph.blocks) == 1
    finally:
        view.deleteLater()
        scene.deleteLater()
        collect(app)
