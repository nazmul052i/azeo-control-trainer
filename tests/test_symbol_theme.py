"""Static P&ID symbols are display content, so they ask the theme.

A vessel, pump or exchanger placed without an authored colour must paint the
theme's EQUIPMENT_FILL under every theme; an authored literal fill keeps
precedence. Before this check every unauthored symbol rendered the artwork's
own grey, so a dark station showed silver equipment among dark pipes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SYMBOLS = {"vessel": (40, 40, 104, 144, (92, 120)),
           "pump": (220, 60, 92, 78, (266, 100)),
           "reactor": (360, 40, 128, 172, (424, 110)),
           "exchanger": (540, 60, 144, 98, (612, 110)),
           "compressor": (720, 60, 100, 100, (770, 110))}


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _document(**overrides):
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
    items = [dict(kind="symbol", id=name, x=x, y=y, w=w, h=h, symbol=name, **overrides)
             for name, (x, y, w, h, _sample) in SYMBOLS.items()]
    return PvmDisplay(name="Symbol theme probe", items=items, width=900, height=260,
                      level=2, show_tag="none").to_dict()


def _sampled(app, document, theme) -> dict[str, str]:
    from PySide6.QtGui import QColor
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView
    view = PvmDisplayView(document, lambda: {}, theme=theme, live=False)
    view.resize(910, 300)
    view.show()
    app.processEvents()
    view.refresh()
    app.processEvents()
    try:
        image = view.grab().toImage()
        return {name: QColor(image.pixel(*sample)).name().upper()
                for name, (_x, _y, _w, _h, sample) in SYMBOLS.items()}
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("theme", ["silver", "hpgray", "dark", "azeo_live"])
def test_unauthored_symbols_fill_with_the_theme_equipment_role(app, theme):
    from azeo_control_trainer.core.hmi.theme.roles import Role
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES
    expected = THEMES[theme][Role.EQUIPMENT_FILL].upper()
    assert _sampled(app, _document(), theme) == {name: expected for name in SYMBOLS}


def test_an_authored_literal_fill_keeps_precedence_over_the_theme(app):
    sampled = _sampled(app, _document(fill="#A05030"), "dark")
    assert all(colour == "#A05030" for colour in sampled.values()), sampled


def test_an_authored_role_still_selects_that_role(app):
    from azeo_control_trainer.core.hmi.theme.roles import Role
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES
    sampled = _sampled(app, _document(fill_role="SURFACE_PANEL"), "dark")
    expected = THEMES["dark"][Role.SURFACE_PANEL].upper()
    assert all(colour == expected for colour in sampled.values()), sampled
