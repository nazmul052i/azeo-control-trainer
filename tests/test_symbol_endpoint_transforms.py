"""Ports must follow the rendered equipment through placement and document edits."""

import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QTransform  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from azeo_control_trainer.core.hmi.pvms import registry, symbols  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.elements import default_symbol_ports  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem, StaticItem  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import port_anchor  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.rendering.renderer import pvm_from_dict  # noqa: E402
from azeo_control_trainer.core.hmi.theme.tokens import THEMES  # noqa: E402

VECTORS = {"n": (0, -1), "e": (1, 0), "s": (0, 1), "w": (-1, 0)}
ALIASES = {"n": "top", "e": "outlet", "s": "bottom", "w": "inlet"}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("name", list(symbols.CATALOG))
def test_catalog_endpoints_survive_resize_mirror_rotation_and_saved_port_metadata(app, name):
    for mx, my in ((False, False), (True, False), (False, True), (True, True)):
        for rotation in (0, 90, 180, 270):
            for width, height in ((160, 160 * symbols.aspect(name)), (233, 117)):
                ports = default_symbol_ports(name)
                # A persisted library port follows a library correction even
                # when the document still contains the old coordinates.
                for port in ports:
                    port.update(x=0.123, y=0.234)
                item = StaticItem(
                    dict(
                        kind="symbol",
                        symbol=name,
                        x=237.3,
                        y=181.7,
                        w=width,
                        h=height,
                        mx=mx,
                        my=my,
                        rot=rotation,
                        ports=ports,
                    ),
                    THEMES["silver"],
                )
                for side, (fx, fy) in symbols.connection_ports(name).items():
                    local = QPointF(width * (1 - fx if mx else fx), height * (1 - fy if my else fy))
                    expected = item.mapToScene(local)
                    dx, dy = VECTORS[side]
                    dx, dy = -dx if mx else dx, -dy if my else dy
                    for _ in range(rotation // 90):
                        dx, dy = -dy, dx
                    normal = next(key for key, value in VECTORS.items() if value == (dx, dy))
                    for endpoint in (side, ALIASES[side]):
                        assert (item.anchor(endpoint) - expected).manhattanLength() < 0.001
                        assert port_anchor(item, endpoint).normal == normal


def test_explicit_custom_nozzle_is_not_replaced_by_catalog_correction(app):
    item = StaticItem(
        dict(
            kind="symbol",
            symbol="compressor",
            w=150,
            h=120,
            ports=[dict(name="inlet", x=0.123, y=0.234, normal="w")],
        ),
        THEMES["silver"],
    )
    point = item.mapFromScene(item.anchor("inlet"))
    assert (point.x(), point.y()) == pytest.approx((150 * 0.123, 120 * 0.234))


def test_all_symbol_pvm_classes_share_the_catalog_nozzles_through_resize_and_rotation(app):
    checked = set()
    for (block, role, variant), _cls in registry.all_classes().items():
        if role not in ("dynamo_inline", "dynamo_compact"):
            continue
        for width, height in ((160, 140), (95, 210), (245, 90)):
            for rotation in (0, 90, 180, 270):
                data = dict(
                    id="audit",
                    **{"class": f"{block}/{role}"},
                    variant=variant,
                    w=width,
                    h=height,
                    x=31,
                    y=59,
                    rot=rotation,
                    params={},
                )
                item = PvmItem(pvm_from_dict(data), None, None, THEMES["silver"])
                name = item._symbol_name()
                if not name:
                    continue
                checked.add((block, role, variant))
                rect = item._symbol_view_rect(name)
                transform = QTransform()
                if role == "dynamo_compact":
                    transform.translate(rect.center().x(), rect.center().y())
                    transform.rotate(rotation)
                    transform.translate(-rect.center().x(), -rect.center().y())
                for side, (fx, fy) in symbols.connection_ports(name).items():
                    point = QPointF(
                        rect.left() + rect.width() * fx, rect.top() + rect.height() * fy
                    )
                    expected = item.mapToScene(transform.map(point))
                    assert (item.anchor(side) - expected).manhattanLength() < 0.001, (
                        block,
                        role,
                        variant,
                        side,
                    )
                    normal = "nesw"[("nesw".index(side) + rotation // 90) % 4]
                    assert port_anchor(item, side).normal == normal, (block, role, variant, side)
    assert len(checked) >= 23
