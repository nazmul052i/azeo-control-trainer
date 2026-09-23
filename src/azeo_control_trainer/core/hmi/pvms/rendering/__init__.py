"""Shared display rendering primitives used by engineering and runtime apps.

This package deliberately contains no product shell.  Graphics Designer owns
authoring commands while Operator Station owns navigation and operations; both
consume the same scene items and renderer from here.
"""

from .chrome import (
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
from .crossing import (
    CROSSOVER_KINDS,
    _collect_segments,
    _scene_segments,
    _seg_cross,
    crossing_count,
    draw_crossed_polyline,
    stroke_points,
)
from .items import PvmItem, PipeItem, StaticItem
from .renderer import DisplayRenderer, pvm_from_dict
from .viewer import PvmDisplayView, open_published

__all__ = [
    "CROSSOVER_KINDS",
    "DYNAMO_ROLES",
    "DisplayRenderer",
    "PVM_H",
    "PVM_W",
    "PvmDisplayView",
    "PvmItem",
    "MIME_BLOCK",
    "MODE_EDIT",
    "MODE_TEST",
    "MODE_VIEW",
    "PipeItem",
    "ROLE_SIZES",
    "StaticItem",
    "WF",
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
