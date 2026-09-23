"""The silver column layout is driven by real tags and standard faceplates."""
# Project imports follow the standalone test's path/environment bootstrap.
# ruff: noqa: E402
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]

import pytest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.binding.result import BindingResult
from azeo_control_trainer.core.hmi.pvms.fan_indicator import measurement
from azeo_control_trainer.core.hmi.pvms.base import registry
from azeo_control_trainer.core.strategy.model.terminal import Quality


@pytest.fixture(scope="module")
def app():
    from azeo_control_trainer.core.hmi.theme.fonts import ensure_font_directory
    ensure_font_directory()
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("value,expected", [(-10, 0), (0, 0), (50, .5), (100, 1), (120, 1)])
def test_fan_uses_the_engineering_range(value, expected):
    reading = measurement(BindingResult(value=value, quality=Quality.GOOD, eu_range=(0, 100)))
    assert reading[0] == value
    assert reading[-1] == expected


@pytest.mark.parametrize("value,quality,limits", [
    (12, Quality.BAD, (0, 100)), (12, Quality.UNCERTAIN, (0, 100)),
    (math.nan, Quality.GOOD, (0, 100)), (math.inf, Quality.GOOD, (0, 100)),
    (True, Quality.GOOD, (0, 100)), (12, Quality.GOOD, None),
    (12, Quality.GOOD, (100, 0)), (12, Quality.GOOD, (0, 0)),
    (12, Quality.GOOD, (False, 100)),
    (12, Quality.GOOD, ("bad", 100)), (12, Quality.GOOD, (0, math.inf)),
])
def test_fan_does_not_draw_a_false_healthy_needle(value, quality, limits):
    assert measurement(BindingResult(value=value, quality=quality, eu_range=limits)) is None


def test_fan_is_a_library_class_with_the_existing_ai_faceplate():
    fan = registry.get("AI", "dynamo_inline", "fan_indicator")
    assert fan is not None and fan.display_name == "Fan Indicator"
    assert fan.bindings[0].path == "{path}/OUT"
    assert registry.get("AI", "faceplate") is not None


def test_reference_template_is_editable_protected_and_unconfigured(tmp_path):
    from azeo_control_trainer.core.hmi.pvms.instances import TemplateStore
    from azeo_control_trainer.core.hmi.pvms.distillation_template import TEMPLATE_NAME
    store = TemplateStore(tmp_path)
    assert store.is_builtin(TEMPLATE_NAME)
    doc = store.instantiate(TEMPLATE_NAME, "My column")
    assert doc["level"] == 2 and not doc.get("background")
    assert len(doc["pvms"]) >= 12
    assert all(g["params"]["path"].startswith("CONFIGURE/") for g in doc["pvms"])
    assert not any(i["kind"] == "image" for i in doc["items"])
    for item in doc["items"]:
        if item["kind"] == "symbol":
            assert item.get("fill_role") == "EQUIPMENT_FILL", item["id"]
            assert item.get("line_role") == "EQUIPMENT", item["id"]


def test_plant_bindings_resolve_and_levels_match_process_roles():
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401
    from create_distillation_display import plant_document, PLANT_BINDINGS
    from azeo_control_trainer.core.strategy.serialization.strategy_io import load_strategy
    from azeo_control_trainer.core.hmi.binding.source import LiveGraphSource
    from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED
    graphs = {}
    for module in {path.split("/")[0] for path in PLANT_BINDINGS.values()}:
        graph, _ = load_strategy(str(ROOT / "projects/AzeoPlantVirtualController/control" / (module + ".json")))
        graphs[module] = graph
    source = LiveGraphSource(lambda: graphs)
    document = plant_document()
    for pvm in document.pvms:
        cls = registry.get(pvm.block_type, pvm.role, pvm.variant)
        assert cls is not None, pvm.variant
        for binding in cls.bindings:
            if binding.path and not binding.expr:
                path = binding.path.format(**pvm.params)
                assert source.read(path) is not UNRESOLVED, (pvm.id, path)
    by_id = {g.id: g.params["path"] for g in document.pvms}
    assert by_id["column"] == "LIC-5002/LIC-5002"
    assert by_id["receiver"] == "LIC-5001/LIC-5001"
    assert by_id["boot_level"] == "LIC-5003/LIC-5003"


def test_navigation_can_show_a_display_outside_the_hierarchy(app):
    from azeo_control_trainer.azeo_operator_station.layout_surface import StationLayoutSurface
    from azeo_control_trainer.core.hmi.pvms.layout import DisplaySet
    display_set = DisplaySet("Review")
    display_set.add_root("Overview")
    display_set.add_non_hierarchical("Column preview")
    # This public method must tolerate an absent branch, as station search can
    # open any non-hierarchical published display in the active set.
    StationLayoutSurface.set_navigation(SimpleNamespace(hosts={}), display_set, "Column preview")


def test_review_installer_preserves_engineer_edits(tmp_path):
    from create_distillation_display import install, DISPLAY_NAME
    from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
    install(tmp_path)
    install(tmp_path)
    store = DisplayStore(tmp_path)
    draft = store.load_draft(DISPLAY_NAME)
    draft.description = "An engineer's reviewed operating instructions"
    store.save_draft(draft)
    with pytest.raises(FileExistsError):
        install(tmp_path)
    assert store.load_draft(DISPLAY_NAME).description == draft.description


def test_column_template_has_clear_connected_routes(app):
    from azeo_control_trainer.core.hmi.pvms.distillation_template import distillation_document
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
    from azeo_control_trainer.core.hmi.pvms.rendering.items import PipeItem, PvmItem
    view = PvmDisplayView(distillation_document("Review").to_dict(), lambda: {}, theme="silver")
    try:
        pipes = [i for i in view.scene().items() if isinstance(i, PipeItem)]
        assert len(pipes) == 17
        assert not {p.data["id"]: p.route_message for p in pipes if p.route_status != "ok"}
        column = next(i for i in view.scene().items() if isinstance(i, PvmItem) and i.pvm.id == "column")
        for pipe in pipes:
            if pipe.data.get("b") == "column" and pipe.data["b_side"].startswith("outline:"):
                endpoint = column.anchor(pipe.data["b_side"])
                assert column.connection_anchor_at(endpoint, pixels=2) is not None
    finally:
        view.close()
        view.deleteLater()


def test_column_routes_have_clean_runs_and_a_real_discharge_branch(app):
    from azeo_control_trainer.core.hmi.pvms.distillation_template import distillation_document
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
    from azeo_control_trainer.core.hmi.pvms.rendering.items import PipeItem
    document = distillation_document("Connection review").to_dict()
    budget = dict(feed_to_valve=0, feed_to_column=0, overhead=1, condensate=0,
                  receiver_to_pump=1, pump_discharge=0, reflux_discharge=1,
                  reflux_return=2, distillate=1, distillate_out=0, sump_to_pump=1,
                  bottoms_outlet=0, bottoms_export=0, boil_feed=2, boil_return=1,
                  steam_in=0, steam_to_reboiler=2)
    view = PvmDisplayView(document, lambda: {}, theme="silver")
    try:
        pipes = {p.data["id"]: p for p in view.scene().items() if isinstance(p, PipeItem)}
        for name, pipe in pipes.items():
            assert pipe.route_status == "ok", (name, pipe.route_message)
            assert len(pipe._points) - 2 <= budget[name], (name, pipe._points)
            assert all((a - b).manhattanLength() >= 10 for a, b in zip(pipe._points, pipe._points[1:])), name
        tee = pipes["pump_discharge"].data["b"]
        assert pipes["reflux_discharge"].data["a"] == tee == pipes["distillate"].data["a"]
        assert next(i for i in document["items"] if i["id"] == tee)["pipe_junction"]
        assert pipes["distillate"].data["b_side"] == "w"
        assert next(i for i in document["items"] if i["id"] == "reboiler")["symbol"] == "exchanger"
        assert pipes["steam_to_reboiler"].data["b_side"] == "e"
        assert pipes["boil_feed"].data["b_side"] == "w"
        assert pipes["boil_return"].data["a_side"] == "n"
    finally:
        view.close()
        view.deleteLater()


def test_silver_operating_text_has_readable_srgb_contrast():
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES
    from azeo_control_trainer.core.hmi.theme.roles import Role

    def luminance(colour):
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))

    palette = THEMES["silver"]
    for text in (Role.TEXT, Role.TEXT_DIM, Role.TEXT_FAINT, Role.ACTION):
        for background in (Role.SURFACE_BG, Role.SURFACE_PANEL, Role.SURFACE_FIELD):
            contrast = (luminance(palette[background]) + .05) / (luminance(palette[text]) + .05)
            assert contrast >= 4.5, (text, background, contrast)


def test_large_loop_typography_keeps_all_numeric_digits(app):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QFont, QImage, QPainter
    from azeo_control_trainer.core.hmi.pvms.hp.painters import _field_row
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES

    class Capture:
        def __init__(self, painter):
            self.painter, self.readings = painter, []

        def __getattr__(self, name):
            return getattr(self.painter, name)

        def drawText(self, rect, alignment, text):  # noqa: N802
            self.readings.append((text, rect.width(), self.painter.fontMetrics().horizontalAdvance(text)))

    def typeface(role, family, size, weight=QFont.Normal):
        font = QFont(family)
        font.setPointSizeF(size * 1.35)
        font.setWeight(weight)
        return font

    image = QImage(250, 100, QImage.Format_ARGB32)
    painter = QPainter(image)
    capture = Capture(painter)
    try:
        _field_row(capture, SimpleNamespace(typeface=typeface), QRectF(0, 0, 200, 80),
                   0, "PV", 166.66, "", THEMES["silver"], 2)
        assert all(width >= ink for _, width, ink in capture.readings), capture.readings
    finally:
        painter.end()
