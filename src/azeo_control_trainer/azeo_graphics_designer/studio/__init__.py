"""Graphics Designer's display-authoring surface.

Authoring commands, browsers, panes, and validation live here. Scene items,
smart-pipe geometry, the renderer, and the display viewer are shared through
``core.hmi.pvms.rendering`` so preview and Operator Station cannot drift.
This package re-exports those established drawing names for Graphics Designer
callers.
"""

from __future__ import annotations

from azeo_control_trainer.core.hmi.pvms.rendering.chrome import (
    DYNAMO_ROLES,
    PVM_H,
    PVM_W,
    MIME_BLOCK,
    MODE_EDIT,
    MODE_TEST,
    MODE_VIEW,
    ROLE_SIZES,
    WF,
    _mode_chip_colour,
)
from azeo_control_trainer.core.hmi.pvms.rendering.crossing import (
    CROSSOVER_KINDS,
    _collect_segments,
    _scene_segments,
    _seg_cross,
    crossing_count,
    draw_crossed_polyline,
    stroke_points,
)
from azeo_control_trainer.core.hmi.pvms.rendering.items import (
    PvmItem,
    PipeItem,
    StaticItem,
)
from azeo_control_trainer.core.hmi.pvms.rendering.renderer import (
    DisplayRenderer,
    pvm_from_dict,
)
from azeo_control_trainer.core.hmi.pvms.rendering.viewer import (
    PvmDisplayView,
    open_published,
)

from .assembler import PvmStudio
from .browser import ControlBrowser, QApplicationClipboard
from .canvas import _Canvas
from .panes import _AlarmBanner, _ConfigPane, _PathField, _Snippet, _WatchArea

__all__ = [
    "CROSSOVER_KINDS",
    "ControlBrowser",
    "DYNAMO_ROLES",
    "DisplayRenderer",
    "PVM_H",
    "PVM_W",
    "PvmDisplayView",
    "PvmItem",
    "PvmStudio",
    "MIME_BLOCK",
    "MODE_EDIT",
    "MODE_TEST",
    "MODE_VIEW",
    "PipeItem",
    "QApplicationClipboard",
    "ROLE_SIZES",
    "StaticItem",
    "WF",
    "_AlarmBanner",
    "_Canvas",
    "_ConfigPane",
    "_PathField",
    "_Snippet",
    "_WatchArea",
    "_collect_segments",
    "_mode_chip_colour",
    "_scene_segments",
    "_seg_cross",
    "crossing_count",
    "draw_crossed_polyline",
    "pvm_from_dict",
    "open_published",
    "stroke_points",
]
