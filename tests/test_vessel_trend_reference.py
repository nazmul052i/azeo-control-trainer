"""Operator-visible contracts of the vessel level/history presentation."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.pvms.base import Pvm
from azeo_control_trainer.core.hmi.pvms.pvm_painters import paint_vessel_trend
from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem
from azeo_control_trainer.core.hmi.theme.roles import Role
from azeo_control_trainer.core.hmi.theme.tokens import THEMES
from azeo_control_trainer.core.hmi.theme.fonts import ensure_font_directory

ensure_font_directory()


def vessel():
    result = SimpleNamespace(value=65.0, quality=SimpleNamespace(name="GOOD"),
                             eu_range=(0, 100), units="%", alarm_active=False,
                             alarm_priority=0, forced=False)
    pvm = Pvm("d1", "AI/dynamo_inline", "AI", "dynamo_inline", {},
              variant="vessel", label="D1", w=240, h=330)
    item = PvmItem(pvm, SimpleNamespace(result=result), None, THEMES["silver"])
    item.history.extend([42, 48, 45, 52, 65])
    return item


def test_healthy_vessel_trend_has_no_alarm_coloured_endpoint():
    app = QApplication.instance() or QApplication([])
    item = vessel()
    image = QImage(260, 350, QImage.Format_RGB32)
    image.fill(QColor(THEMES["silver"][Role.SURFACE_PANEL]))
    painter = QPainter(image)
    paint_vessel_trend(painter, item)
    painter.end()
    alarm = QColor(THEMES["silver"][Role.ALARM_P1]).rgb()
    assert not any(image.pixel(x, y) == alarm for x in range(image.width())
                   for y in range(image.height())), "A healthy trace must not carry a critical alarm dot"
    app.processEvents()


def test_vessel_missing_limits_and_bad_quality_remain_absent():
    from azeo_control_trainer.core.hmi.pvms.vessel_trend import limit_fractions
    app = QApplication.instance() or QApplication([])
    item = vessel()
    assert limit_fractions(item, (0, 100)) == ()
    item.rows["LO"] = SimpleNamespace(result=SimpleNamespace(value=10, quality=SimpleNamespace(name="BAD")))
    item.rows["HI"] = SimpleNamespace(result=SimpleNamespace(value=80, quality=SimpleNamespace(name="GOOD")))
    assert limit_fractions(item, (0, 100)) == (("HI", .8),)
    item.sample_history()
    item.binding.result.quality.name = "BAD"
    item.sample_history()
    item.binding.result.quality.name = "GOOD"
    item.sample_history()
    assert list(item.history)[-3:] == [65.0, None, 65.0]
    app.processEvents()


def test_vessel_shell_uses_the_active_theme_equipment_fill():
    app = QApplication.instance() or QApplication([])
    item = vessel()
    for theme in ("silver", "dark"):
        item._palette = THEMES[theme]
        canvas = QImage(260, 350, QImage.Format_RGB32)
        canvas.fill(QColor(THEMES[theme][Role.SURFACE_PANEL]))
        painter = QPainter(canvas)
        paint_vessel_trend(painter, item)
        painter.end()
        assert canvas.pixelColor(50, 80) == QColor(THEMES[theme][Role.EQUIPMENT_FILL])
    app.processEvents()
