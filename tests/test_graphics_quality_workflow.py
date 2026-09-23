"""Checks must diagnose the rendered draft, yield to editing, and preserve evidence."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture
def studio(tmp_path):
    from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
    app = QApplication.instance() or QApplication([])
    widget = PvmStudio(lambda: {}, tmp_path / "displays" / "pvm")
    widget.enter_edit()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_clipped_label_has_an_undoable_size_fix(studio):
    from azeo_control_trainer.core.hmi.pvms.visual_quality import visual_findings
    from azeo_control_trainer.azeo_graphics_designer.studio.quality import apply_visual_fix
    studio.add_static("text", 60, 60, 28, 10)
    item = studio._static_items()[0]
    item.data.update(text="Compressor discharge pressure", font_size=12)
    studio.mark_unsaved()
    original = studio._document()
    findings = visual_findings(studio.canvas.scene(), studio.display, "dark")
    finding = next(f for f in findings if f.code == "clipped_text")
    assert not finding.blocks_publish
    assert apply_visual_fix(studio, finding)
    assert not any(f.code == "clipped_text" for f in visual_findings(studio.canvas.scene(), studio.display, "dark"))
    studio.undo()
    assert studio._document() == original


def test_visual_findings_include_only_foreground_overlap_and_theme_contrast(studio):
    from azeo_control_trainer.core.hmi.pvms.visual_quality import visual_findings
    studio.add_static("rect", 0, 0, 500, 400)
    for x in (20, 25):
        studio.add_static("text", x, 50, 180, 25)
    labels = [i for i in studio._static_items() if i.data["kind"] == "text"]
    for label in labels:
        label.data.update(text="Pressure", text_color="#222222", font_size=10)
    studio.mark_unsaved()
    findings = visual_findings(studio.canvas.scene(), studio.display, "dark")
    assert len([f for f in findings if f.code == "readout_overlap"]) == 1
    assert any(f.code == "low_contrast" for f in findings)
    assert not any(f.code == "low_contrast" for f in visual_findings(studio.canvas.scene(), studio.display, "silver"))


def test_wrapped_label_that_fits_does_not_acquire_an_endless_resize_warning(studio):
    from azeo_control_trainer.core.hmi.pvms.visual_quality import visual_findings
    studio.add_static("text", 30, 30, 220, 60, text="Procedure title")
    studio._static_items()[0].data.update(text_wrap=True)
    studio.mark_unsaved()
    assert not any(f.code == "clipped_text" for f in visual_findings(studio.canvas.scene(), studio.display))


def test_validation_steps_are_cooperative_and_equal_to_explicit_verify(studio):
    from azeo_control_trainer.azeo_graphics_designer.studio.verification import iter_findings_for_studio
    for x in range(12):
        studio.add_static("text", x * 30, 100, 20, 10)
    iterator = iter_findings_for_studio(studio)
    ticks = 0
    while True:
        try:
            next(iterator)
            ticks += 1
        except StopIteration as done:
            assert ticks >= 12
            assert done.value == studio.verification_findings()
            break


def test_monitor_cancels_stale_results_and_stops_on_close(studio):
    from azeo_control_trainer.azeo_graphics_designer.studio.quality import QualityMonitor
    from azeo_control_trainer.core.hmi.pvms.publishing import Finding
    monitor = QualityMonitor()
    observed = []
    monitor.updated.connect(lambda owner, rows: observed.append(rows))
    monitor.set_studio(studio)
    def old():
        while True:
            yield None
        return (Finding("error", "old"),)  # pragma: no cover
    monitor._iterator = old()
    studio.add_static("rect", 20, 20, 50, 50)
    assert monitor._iterator is None
    for _ in range(100):
        monitor.advance()
        if observed:
            break
    assert observed and not any(f.message == "old" for f in observed[-1])
    studio.close()
    assert monitor._iterator is None and not monitor.timer.isActive()
    monitor.set_studio(None)


def test_operator_viewport_check_matches_studio_without_changing_the_draft(studio):
    from azeo_control_trainer.azeo_graphics_designer.quick_online import QuickOnlineView
    from azeo_control_trainer.core.hmi.pvms.visual_quality import visual_findings
    studio.display.width, studio.display.height, studio.display.level = 1920, 1080, 3
    studio.add_static("text", 20, 20, 350, 25, text="Reactor pressure")
    studio._static_items()[0].data.update(font_size=8, text_color="#222222")
    studio.mark_unsaved()
    original = studio._document()
    quick = QuickOnlineView(lambda: {}, source=studio.preview_source.source, config_root=studio.store.root, theme="dark")
    quick.add_display(original)
    quick.resolution_selector.setCurrentIndex(1)
    assert quick.check_readability() == visual_findings(studio.canvas.scene(), studio.display, "dark", (1366, 768))
    assert studio._document() == original
    quick.close()
    quick.deleteLater()


def test_small_pvm_restores_registered_size_with_one_undo(studio):
    from azeo_control_trainer.core.hmi.pvms.machines import VFDSpeedPvm
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
    from azeo_control_trainer.core.hmi.pvms.visual_quality import visual_findings
    from azeo_control_trainer.azeo_graphics_designer.studio.quality import apply_visual_fix
    pvm = VFDSpeedPvm().place("drive", x=40, y=50, w=50, h=40, path="A/PID", device="A/DEV")
    studio._load_document(PvmDisplay(studio.display.name, pvms=[pvm]).to_dict())
    original = studio._document()
    finding = next(f for f in visual_findings(studio.canvas.scene(), studio.display) if f.code == "small_pvm")
    assert apply_visual_fix(studio, finding)
    assert not any(f.code == "small_pvm" for f in visual_findings(studio.canvas.scene(), studio.display))
    assert not apply_visual_fix(studio, finding)
    studio.undo()
    assert studio._document() == original


def test_actual_size_does_not_apply_fictitious_operator_scaling(studio):
    from azeo_control_trainer.core.hmi.pvms.visual_quality import visual_findings
    studio.display.width, studio.display.height = 1920, 1080
    studio.display.view_type = "actual_size"
    studio.add_static("text", 50, 50, 200, 40, text="Pressure")
    studio._static_items()[0].data["font_size"] = 9
    findings = visual_findings(studio.canvas.scene(), studio.display, "silver", (1366, 768))
    assert any(f.code == "viewport_clipping" for f in findings)
    assert not any(f.code == "viewport_readability" for f in findings)


def test_monitor_does_not_dereference_a_deleted_qt_owner(studio):
    from PySide6.QtCore import QObject, Signal
    from shiboken6 import delete
    from azeo_control_trainer.azeo_graphics_designer.studio.quality import QualityMonitor
    class Owner(QObject):
        documentChanged = Signal()
        _closing = False
    owner = Owner()
    monitor = QualityMonitor()
    monitor.set_studio(owner)
    delete(owner)
    monitor.advance()
    monitor.set_studio(None)
    assert not monitor.timer.isActive()
