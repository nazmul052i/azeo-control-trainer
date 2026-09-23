"""Adversarial integrity cases that span tabs, classes, and publishing."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.pvms.publishing import PublishRefused
from azeo_control_trainer.azeo_graphics_designer.window import HmiStudioWindow
from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary


def _application():
    return QApplication.instance() or QApplication([])


def _window(tmp_path):
    _application()
    return HmiStudioWindow(lambda: {}, tmp_path, area_name="Plant")


def test_cancelled_multi_tab_close_applies_no_prior_discard(tmp_path):
    window = _window(tmp_path)
    first = window.current()
    second = window.open_display("Second")
    for studio in (first, second):
        studio.unsaved = True
        studio.store.save_recovery(studio.display)
    answers = iter(("discard", None))
    original_decision = window._studio_close_decision
    window._studio_close_decision = lambda _studio: next(answers)

    assert not window._confirm_studios_close((first, second))
    assert first.unsaved and second.unsaved
    assert first.store.load_recovery(first.display.name) is not None
    assert second.store.load_recovery(second.display.name) is not None
    window._studio_close_decision = original_decision
    for studio in (first, second):
        studio.unsaved = False
    window.close()


def test_saved_authored_configuration_advances_revision_and_lists_affected(
        tmp_path):
    library = UserPvmLibrary(tmp_path)
    assert library.add("Card", [{
        "id": "body", "kind": "rect", "x": 0, "y": 0,
        "w": 80, "h": 40,
    }]) is not None
    before = library.definition_metadata("Card")[1]
    window = _window(tmp_path)
    studio = window.current()
    studio.enter_edit()
    assert studio.place_user_pvm("Card", 20, 30) == 1
    assert studio.save_draft()

    assert window._pvm_configuration_saved("Card") >= 1
    after = UserPvmLibrary(tmp_path).definition_metadata("Card")[1]
    assert after == before + 1
    assert studio.display.name in window.last_affected_displays
    assert studio.display.work_in_progress
    window.close()


def test_headless_window_publish_refusal_does_not_open_modal(tmp_path):
    window = _window(tmp_path)
    studio = window.current()

    def refuse():
        raise PublishRefused("broken action")

    studio.open_publish = refuse
    with pytest.raises(PublishRefused, match="broken action"):
        window._publish()
    window.close()
