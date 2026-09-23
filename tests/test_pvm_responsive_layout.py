"""Regression checks for proportional, responsive PVM resizing."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import azeo_control_trainer.core.hmi.pvms  # noqa: E402,F401
from azeo_control_trainer.core.hmi.pvms.base import Pvm  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.rendering.items import (  # noqa: E402
    PvmItem,
)
from azeo_control_trainer.core.hmi.theme.palette import (  # noqa: E402
    palette_for,
)


def _item(width: float, height: float) -> PvmItem:
    pvm = Pvm(
        id="responsive-ai",
        pvm_class="AI/dynamo_compact",
        block_type="AI",
        role="dynamo_compact",
        params={"path": "FIC-0101/FT-0102"},
        w=width,
        h=height,
    )
    return PvmItem(pvm, None, None, palette_for("azeo_live"))


@pytest.mark.parametrize("width,height", [(268.0, 43.0), (96.0, 84.0)])
def test_resized_pvm_uses_one_scale_and_a_responsive_layout(
        width: float, height: float) -> None:
    item = _item(width, height)
    scale_x, scale_y = item.content_scale()
    layout_w, layout_h = item.content_layout_size()

    assert scale_x == pytest.approx(scale_y)
    assert layout_w * scale_x == pytest.approx(width)
    assert layout_h * scale_y == pytest.approx(height)
    # A non-canonical aspect ratio becomes usable layout space.  It must not
    # become independent X/Y glyph scaling, which widened the L1 text 2.8x.
    assert layout_w / layout_h == pytest.approx(width / height)


def test_area_alarm_pvm_uses_registry_state_not_a_fake_bad_binding() -> None:
    pvm = Pvm(
        id="area-alarms",
        pvm_class="AREA/dynamo_inline",
        block_type="AREA",
        role="dynamo_inline",
        variant="hp_alarms",
        label="PLANT ALARMS",
        params={"path": "ESD-9000"},
        w=202.0,
        h=54.0,
    )
    item = PvmItem(pvm, None, None, palette_for("azeo_live"))
    item.alarm_provider = lambda: ()
    healthy = item.hp_state()
    assert not healthy.bad_io
    assert not healthy.has_alarm
    assert healthy.alarm_count == 0

    active = SimpleNamespace(
        priority=15,
        active=True,
        acknowledged=False,
        suppressed=False,
        condition="HI_HI",
    )
    suppressed = SimpleNamespace(
        priority=7,
        active=False,
        acknowledged=True,
        suppressed=True,
        condition="LO",
    )
    item.alarm_provider = lambda: (active, suppressed)
    alarmed = item.hp_state()
    assert alarmed.has_alarm
    assert not alarmed.bad_io
    assert not alarmed.suppressed
    assert alarmed.priority == 15
    assert alarmed.alarm_count == 2

    item.alarm_provider = lambda: (suppressed,)
    shelved = item.hp_state()
    assert not shelved.has_alarm
    assert shelved.suppressed
