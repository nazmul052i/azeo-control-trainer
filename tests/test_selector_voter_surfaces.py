"""Measured selector, voter and sequence faceplate contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.binding.result import BindingResult
from azeo_control_trainer.core.hmi.pvms.selector_voter_surfaces import (
    AVTRFaceplateSurface,
    DVTRFaceplateSurface,
    ECTLSLFaceplateSurface,
    FACEPLATE_SURFACES,
    SEQFaceplateSurface,
    STDFaceplateSurface,
    ThreeInputSelectFaceplateSurface,
    WHOLE_SURFACES,
    XmtrFaceplateSurface,
)
from azeo_control_trainer.core.hmi.pvms.hp.fb_faceplates import (
    SEQFaceplate,
    STDFaceplate,
)
from azeo_control_trainer.core.hmi.theme.tokens import THEMES
from azeo_control_trainer.core.strategy.model.terminal import Quality
from azeo_control_trainer.core.strategy.blocks.dv_seq_blocks import (
    SequencerBlock,
    StateTransitionDiagramBlock,
)


def _application():
    return QApplication.instance() or QApplication([])


def _binding(value, quality=Quality.GOOD):
    return SimpleNamespace(result=BindingResult(value=value, quality=quality))


def _render(widget) -> QImage:
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    widget.render(painter, QPoint())
    painter.end()
    return image


def test_whole_surface_catalog_has_every_measured_fb_family() -> None:
    assert WHOLE_SURFACES is FACEPLATE_SURFACES
    assert set(WHOLE_SURFACES) == {
        "ISELFaceplate", "LowSelectFaceplate", "HighSelectFaceplate",
        "CTLSLFaceplate", "AVTRFaceplate",
        "DVTRFaceplate", "SEQFaceplate", "SfcChartFaceplate",
        "STDFaceplate",
    }
    dimensions = {
        name: (surface.DESIGN_WIDTH, surface.DESIGN_HEIGHT)
        for name, surface in WHOLE_SURFACES.items()
    }
    assert dimensions == {
        "ISELFaceplate": (250, 412),
        "LowSelectFaceplate": (250, 412),
        "HighSelectFaceplate": (250, 412),
        "CTLSLFaceplate": (250, 412),
        "AVTRFaceplate": (440, 372),
        "DVTRFaceplate": (440, 372),
        "SEQFaceplate": (367, 468),
        "SfcChartFaceplate": (367, 468),
        "STDFaceplate": (620, 400),
    }
    assert all(surface.WHOLE_SURFACE for surface in WHOLE_SURFACES.values())


def test_xmtr_and_enhanced_selector_keep_distinct_row_contracts() -> None:
    _application()
    palette = THEMES["azeo_live"]
    xmtr = XmtrFaceplateSurface(palette)
    enhanced = ECTLSLFaceplateSurface(palette)
    assert len(xmtr.geometry_contract()["input_rows"]) == 4
    assert enhanced.geometry_contract()["rows"] == 16
    assert xmtr.geometry_contract()["return"].y() > 300
    assert enhanced.geometry_contract()["return"].y() < 20

    writes = []
    xmtr.write_requested.connect(lambda key, value: writes.append((key, value)))
    xmtr.set_write_permissions({"operator_selection"})
    xmtr.refresh({
        "selected": _binding(2),
        "operator_selection": _binding(2),
    })
    QTest.mouseClick(
        xmtr, Qt.MouseButton.LeftButton,
        pos=xmtr.OPERATOR_ROWS[3].center().toPoint(),
    )
    assert writes == [("operator_selection", 3)]

    # Releasing over a command after pressing elsewhere is a drag, not a
    # click.  The old release-only handler changed the selection here.
    QTest.mousePress(xmtr, Qt.MouseButton.LeftButton, pos=QPoint(5, 200))
    QTest.mouseRelease(
        xmtr, Qt.MouseButton.LeftButton,
        pos=xmtr.OPERATOR_ROWS[4].center().toPoint())
    assert writes == [("operator_selection", 3)]

    xmtr.set_write_permissions(set())
    QTest.mouseClick(
        xmtr, Qt.MouseButton.LeftButton,
        pos=xmtr.OPERATOR_ROWS[2].center().toPoint())
    assert writes == [("operator_selection", 3)]

    # The documented return icon emits only an intent the host advertised;
    # it is absent, with no hotspot, until the host can service that route.
    actions = []
    xmtr.action_requested.connect(actions.append)
    QTest.mouseClick(
        xmtr, Qt.MouseButton.LeftButton,
        pos=xmtr.RETURN_RECT.center().toPoint(),
    )
    assert actions == []
    xmtr.set_available_actions({"faceplate"})
    assert xmtr.available_actions == frozenset({"faceplate"})
    QTest.mouseClick(
        xmtr, Qt.MouseButton.LeftButton,
        pos=xmtr.RETURN_RECT.center().toPoint(),
    )
    assert actions == ["faceplate"]


def test_three_input_selector_omits_inert_xmtr_controls_and_sentinels() -> None:
    _application()
    surface = ThreeInputSelectFaceplateSurface(THEMES["azeo_live"])
    assert len(surface.geometry_contract()["input_rows"]) == 3
    assert not hasattr(surface, "OPERATOR_ROWS")

    writes = []
    surface.write_requested.connect(
        lambda key, value: writes.append((key, value)))
    surface.refresh({
        "in1.value": _binding(42.0),
        "in2.value": _binding(float("inf")),
        "in3.value": _binding(float("-inf")),
        "out.value": _binding(42.0),
        "selected": _binding(1),
    })
    assert surface.input_value_text(1) == "42.00"
    assert surface.input_value_text(2) == "Not used"
    assert surface.input_value_text(3) == "Not used"
    QTest.mouseClick(
        surface, Qt.MouseButton.LeftButton,
        pos=surface.INPUT_ROWS[1].center().toPoint(),
    )
    assert writes == []


def test_voter_tabs_match_analog_and_discrete_reference_anatomy() -> None:
    _application()
    palette = THEMES["azeo_live"]
    analog = AVTRFaceplateSurface(palette)
    discrete = DVTRFaceplateSurface(palette)
    assert analog.tab_names == (
        "Trip", "Pre-Trip", "Bypass", "Startup", "Alert", "Misc")
    assert discrete.tab_names == ("Trip", "Bypass", "Startup", "Alert")

    # A write exists only when the binding exists; the absent state must not
    # expose a convincing but inert Allow Bypass hotspot.
    writes = []
    analog.write_requested.connect(
        lambda key, value: writes.append((key, value)))
    QTest.mouseClick(
        analog, Qt.MouseButton.LeftButton,
        pos=analog.tab_rects[2].center().toPoint())
    QTest.mouseClick(analog, Qt.MouseButton.LeftButton, pos=QPoint(180, 282))
    assert writes == []
    analog.refresh({"bypass.allow": _binding(False)})
    analog.set_write_permissions(set())
    QTest.mouseClick(analog, Qt.MouseButton.LeftButton, pos=QPoint(180, 282))
    assert writes == []


def test_sfc_chart_reuses_seq_surface_without_inventing_rows() -> None:
    _application()
    surface = SEQFaceplateSurface(THEMES["azeo_live"])
    chart = {"steps": [
        {"id": "idle", "name": "IDLE"},
        {"id": "charge", "name": "CHARGE"},
    ]}
    surface.refresh({
        "chart": _binding(json.dumps(chart)),
        "step.active": _binding("charge"),
    })
    assert surface._sfc_chart_rows() == [  # noqa: SLF001 - adapter contract
        ("IDLE", False), ("CHARGE", True)]

    surface.refresh({"chart": _binding("not-json")})
    assert surface._sfc_chart_rows() == []  # noqa: SLF001

    surface.refresh({
        "state.name": _binding(""),
        "step.active": _binding(""),
        "step.number": _binding(3),
    })
    assert surface.current_state_text() == "3"


def test_seq_std_bind_all_supported_rows_and_real_matrix_destinations() -> None:
    seq_keys = {binding.key for binding in SEQFaceplate.bindings}
    std_keys = {binding.key for binding in STDFaceplate.bindings}
    assert {f"row{i}" for i in range(1, 17)} <= seq_keys
    assert {f"row{i}.state" for i in range(1, 17)} <= seq_keys
    assert {f"row{i}" for i in range(1, 17)} <= std_keys
    assert {f"row{i}.state" for i in range(1, 17)} <= std_keys
    assert "matrix" in std_keys
    assert not any(key.endswith(".destination") for key in std_keys)
    seq_schema = SequencerBlock("SEQ").get_config_schema()
    std_schema = StateTransitionDiagramBlock("STD").get_config_schema()
    assert all(
        f"DESC_OUT{i}" in seq_schema for i in range(1, 17)
    )
    assert all(
        f"DESC_IN{i}" in std_schema for i in range(1, 17)
    )

    _application()
    surface = STDFaceplateSurface(THEMES["azeo_live"])
    surface.refresh({
        "state.name": _binding(2),
        "matrix": _binding(json.dumps({"2": [3, 0, 5]})),
    })
    assert surface.transition_destinations() == {1: "3", 3: "5"}
    surface.refresh({
        "state.name": _binding(2),
        "matrix": _binding(json.dumps([
            {"state": 2, "trans": 2, "next": 4},
            {"state": 1, "trans": 1, "next": 9},
        ])),
    })
    assert surface.transition_destinations() == {2: "4"}


def test_every_surface_paints_at_its_measured_size() -> None:
    _application()
    palette = THEMES["azeo_live"]
    classes = {
        XmtrFaceplateSurface, ECTLSLFaceplateSurface,
        ThreeInputSelectFaceplateSurface,
        AVTRFaceplateSurface, DVTRFaceplateSurface,
        SEQFaceplateSurface, STDFaceplateSurface,
    }
    for surface_class in classes:
        surface = surface_class(palette)
        surface.set_identity("UNIT/BLOCK", surface_class.__name__)
        image = _render(surface)
        assert (image.width(), image.height()) == (
            surface_class.DESIGN_WIDTH, surface_class.DESIGN_HEIGHT)
        assert image.pixelColor(1, 1).alpha() == 255
