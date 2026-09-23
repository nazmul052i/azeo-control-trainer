"""Measured and semantic contracts for the AT_fp and DCC_fp painters."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QSignalSpy, QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.hmi.binding.result import (  # noqa: E402
    BindingResult,
)
from azeo_control_trainer.core.hmi.pvms.condition_surfaces import (  # noqa: E402
    ATFaceplateSurface,
    CONDITION_FACEPLATE_VISUALS,
    MotorInterlockFaceplateSurface,
)
from azeo_control_trainer.core.hmi.theme.tokens import THEMES  # noqa: E402
from azeo_control_trainer.core.strategy.model.terminal import Quality  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _bound(rows, **values):
    result = BindingResult(
        value=json.dumps(rows),
        quality=Quality.GOOD,
    )
    bound = {"conditions": SimpleNamespace(result=result)}
    for key, value in values.items():
        bound[key] = SimpleNamespace(result=BindingResult(
            value=value, quality=Quality.GOOD))
    return bound


def test_condition_surfaces_publish_the_measured_manual_shells() -> None:
    _app()
    at = ATFaceplateSurface(THEMES["azeo_live"])
    dcc = MotorInterlockFaceplateSurface(THEMES["azeo_live"])

    assert at.size().toTuple() == (578, 449)
    assert dcc.size().toTuple() == (624, 505)
    assert CONDITION_FACEPLATE_VISUALS == {
        "ATFaceplate": ATFaceplateSurface,
        "MotorInterlockFaceplate": MotorInterlockFaceplateSurface,
    }
    assert at.COLUMN_CENTRES["delay_on"] < at.COLUMN_CENTRES["delay_off"]
    assert dcc.COLUMN_CENTRES["first_out"] < dcc.COLUMN_CENTRES["condition"]
    assert dcc.RESET_RECT.bottom() < dcc.ROW_TOP
    assert dcc.ROW_TOP + dcc.MAX_ROWS * dcc.ROW_HEIGHT \
        <= dcc.TABLE_RECT.bottom()


def test_motor_surface_keeps_tabs_first_out_and_trip_polarity() -> None:
    app = _app()
    surface = MotorInterlockFaceplateSurface(THEMES["azeo_live"])
    surface.refresh(_bound([
        {"kind": "interlock", "description": "Lube oil OK",
         "state": True, "delay": 4.0, "timer": 2.0,
         "first_out": True, "reset_required": True},
        {"kind": "permissive", "description": "Suction open",
         "state": False, "delay": 1.0, "timer": 0.5},
        {"kind": "trip", "description": "E-stop",
         "state": True, "delay": 4.0, "timer": 2.0},
    ], **{"reset.required": True}))
    surface.set_write_permissions({"cmd.reset"})
    surface.show()
    app.processEvents()

    assert surface.tab_titles == ("Interlocks", "Permissives", "Trips")
    assert [row.description for row in surface.visible_rows] == ["Lube oil OK"]
    assert surface.first_out_rows[0].description == "Lube oil OK"

    trip = surface.rows_for("trip")[0]
    assert trip.state_text == "ACTIVE"
    assert not trip.healthy and trip.active
    assert trip.timer_fraction == 0.5

    writes = QSignalSpy(surface.write_requested)
    QTest.mouseClick(surface, Qt.LeftButton,
                     pos=surface.RESET_RECT.center().toPoint())
    assert writes.count() == 1
    assert writes.at(0) == ["cmd.reset", True]

    # The third tab is a real hit target, not painted decoration.
    QTest.mouseClick(surface, Qt.LeftButton,
                     pos=surface.TAB_RECTS[2].center().toPoint())
    app.processEvents()
    assert surface.active_tab == "trip"
    assert [row.description for row in surface.visible_rows] == ["E-stop"]

    # Rendering carries a real vector first-out arrow, not a font glyph.
    surface.set_active_tab("interlock")
    image = surface.grab().toImage()
    arrow_pixels = {
        image.pixelColor(x, y).name()
        for x in range(36, 70)
        for y in range(107, 119)
    }
    assert "#ce0f45" in arrow_pixels
    surface.close()


def test_at_reset_is_disabled_without_a_declared_write_target() -> None:
    app = _app()
    surface = ATFaceplateSurface(THEMES["azeo_live"])
    surface.set_identity("UNIT100/AT1", "Analog Tracking")
    surface.refresh(_bound([
        {"kind": "tracking", "description": "Track on bad PV",
         "state": False, "delay_on": 2.0, "delay_off": 1.0,
         "timer": 0.5, "value": 42.0, "bypassed": True,
         "reset_required": True, "hold_manual": True,
         "first_out": True},
    ]))
    surface.show()
    app.processEvents()

    assert surface.identity_title == "UNIT100"
    assert surface.identity_zone == "AT1"
    assert surface.reset_visible
    assert surface.rows[0].value == 42.0

    writes = QSignalSpy(surface.write_requested)
    actions = QSignalSpy(surface.action_requested)
    surface.set_available_actions({"faceplate"})
    surface.set_write_permissions(set())
    QTest.mouseClick(surface, Qt.LeftButton,
                     pos=surface.RESET_RECT.center().toPoint())
    QTest.mouseClick(surface, Qt.LeftButton,
                     pos=surface.RETURN_RECT.center().toPoint())
    app.processEvents()

    assert writes.count() == 0
    assert actions.count() == 1
    assert actions.at(0) == ["faceplate"]
    surface.close()


def test_condition_return_icon_rejects_an_unrelated_detail_route() -> None:
    _app()
    surface = ATFaceplateSurface(THEMES["azeo_live"])
    actions = QSignalSpy(surface.action_requested)
    surface.set_available_actions({"detail"})
    QTest.mouseClick(surface, Qt.LeftButton,
                     pos=surface.RETURN_RECT.center().toPoint())
    assert actions.count() == 0
    assert surface.available_actions == frozenset()


def test_bad_condition_document_stays_empty_instead_of_inventing_ok_rows() -> None:
    _app()
    surface = MotorInterlockFaceplateSurface(THEMES["azeo_live"])
    surface.refresh({"conditions": SimpleNamespace(result=BindingResult(
        value="not json", quality=Quality.BAD))})
    assert surface.rows == []
    assert surface.visible_rows == ()


def test_compact_at_contract_populates_one_real_row_not_placeholders() -> None:
    _app()
    surface = ATFaceplateSurface(THEMES["azeo_live"])
    surface.refresh({
        "request": SimpleNamespace(result=BindingResult(
            value=True, quality=Quality.GOOD)),
        "state.active": SimpleNamespace(result=BindingResult(
            value=True, quality=Quality.GOOD)),
        "pv.value": SimpleNamespace(result=BindingResult(
            value=37.5, quality=Quality.GOOD)),
    })
    assert len(surface.rows) == 1
    assert surface.rows[0].description == "Tracking request"
    assert surface.rows[0].state
    assert surface.rows[0].value == 37.5
