"""A drawing canvas for user-built displays.

The palette carries the simulator's own PVMs and dynamos - analog bars
and values, trend tiles, DI/DO lamps, loop PVMs, pumps, valves,
equipment, junctions, text - plus pipes drawn by clicking waypoints and
imported SVG or PNG graphics. Everything placed is live: the canvas
refreshes with the plant like any other window, so the page is built
against real values. Pages save as JSON under ``displays/`` and open
from the View menu as live operator displays, with every dynamo
clickable through to its faceplate.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import (QEvent, QPointF, QRectF, QSettings, QSize, Qt,
                            QTimer)
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QKeySequence, \
    QPainter, QShortcut, QTransform, QUndoStack
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QColorDialog,
                               QComboBox, QCompleter, QDockWidget,
                               QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGraphicsItem, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QToolBar, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .builder_canvas import GRID, BuilderScene, BuilderView, HandlesOverlay
from .builder_icons import make_icon
from .builder_commands import (AddCmd, EditCmd, MoveCmd, RemoveCmd, ZCmd,
                               align_moves, clone_recs, conn_rank, copy_rec,
                               export_png, export_svg)
from .hmi import (BAND, CANVAS, NAVY, TEXT, Connector, Equip, Flag,
                  HmiItem, HmiScene, ImageItem, Junction, Label, LoopBox,
                  MeasBox, MovISA, Pipe, PumpISA, StateLamp, TextBox,
                  TrendTile, VBar, ValveISA, _FitView, build_overview,
                  wire_scene_signals)

log = logging.getLogger(__name__)

#: the repository root; image paths inside it are saved relative so
#: pages stay portable
ROOT = Path(__file__).resolve().parents[2]

#: where user pages live, beside snapshots/ at the repository root
DISPLAY_DIR = ROOT / "displays"

#: the curated P&ID/ISA symbol library (tools/import_symbols.py fills it)
SYMBOL_DIR = Path(__file__).resolve().parent / "symbols"

#: crash recovery: the working page is written here while it is dirty
AUTOSAVE = DISPLAY_DIR / ".autosave.json"

#: how many recent pages the File menu remembers
RECENT_MAX = 8

#: builder chrome on top of the app theme: white tool bands with flat
#: buttons, clean number fields, card-style side panels
_BUILDER_QSS = """
QToolBar { padding: 3px 6px; spacing: 1px; }
QToolBar QToolButton { padding: 3px; }
QDockWidget::title { background: #EDF0F4; padding: 5px 10px;
    border-bottom: 1px solid #DDE1E6; font-weight: 600; color: #4A5462; }
#builderHint { background: transparent; color: #5A6472; }
#formCard { background: #FCFCFD; }
#formCard QLabel { background: transparent; }
QListWidget, QTreeWidget { border: 1px solid #DDE1E6; border-radius: 6px; }
QListWidget::item { padding: 3px 6px; border-radius: 4px; }
QListWidget::item:selected { background: #DCE6F2; color: #1A1C1E; }
QTreeWidget::item { padding: 2px 2px; }
QLabel#panelHeader { background: transparent; color: #6B7480;
    font-weight: 700; font-size: 8pt; padding: 2px 1px; }
QStatusBar QLabel { background: transparent; color: #5A6472; }
QGraphicsView { border: none; }
QScrollArea { border: none; }
"""


def _resolve_path(path: str) -> str:
    p = Path(path)
    return str(p if p.is_absolute() else ROOT / p)


def _relativize(path: str) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


class BuildContext:
    """What the item factories need to wire a dynamo into the plant."""

    def __init__(self, db, controller=None, alarms=None) -> None:
        self.db = db
        self.controller = controller
        self.alarms = alarms
        #: id -> placed item, for connectors; filled by builder/loader
        self.items_by_id: Dict[int, object] = {}

    def eu(self, tag: str, default: str = "") -> str:
        try:
            return self.db[tag].eu if tag in self.db else default
        except Exception:
            return default


# ---------------------------------------------------------------- factories
def _vbar(ctx, x, y, pr):
    lim = {}
    if ctx.alarms is not None and pr["tag"]:
        lim = {p.atype: p.setpoint for p in ctx.alarms.points
               if p.tag == pr["tag"] and p.enabled
               and p.atype in ("HI_HI", "HI", "LO", "LO_LO")}
    loop = None
    if ctx.controller is not None and pr["tag"]:
        loop = next((l for l in ctx.controller.loops.values()
                     if l.pv_tag == pr["tag"]), None)
    sp_fn = (lambda l=loop: l.pid.sp) if loop is not None else None
    return VBar(x, y, pr["tag"], unit=pr["unit"] or ctx.eu(pr["tag"], "%"),
                span=(pr["lo"], pr["hi"]), height=pr["height"],
                decimals=pr["decimals"], limits=lim, sp_fn=sp_fn)


def _meas(ctx, x, y, pr):
    return MeasBox(x, y, pr["tag"], unit=pr["unit"] or ctx.eu(pr["tag"]),
                   decimals=pr["decimals"])


def _trend(ctx, x, y, pr):
    return TrendTile(x, y, pr["ref"], pr["caption"] or pr["ref"],
                     pr["unit"] or ctx.eu(pr["ref"]), ctx.controller,
                     mini=pr["mini"])


def _loop(ctx, x, y, pr):
    if ctx.controller is None or pr["module"] not in getattr(
            ctx.controller, "loops", {}):
        return TextBox(x, y, f"{pr['module'] or 'LOOP'}\nnot found",
                       w=110, h=40)
    return LoopBox(x, y, pr["module"], ctx.controller, pr["unit"],
                   card=pr["card"])


def _lamp(ctx, x, y, pr):
    return StateLamp(x, y, pr["tag"], caption=pr["caption"],
                     on_text=pr["on_text"], off_text=pr["off_text"],
                     size=pr["size"])


def _pump(ctx, x, y, pr):
    parts = [s.strip() for s in str(pr["pair"]).split(",") if s.strip()]
    pair = tuple((parts + ["N", "S"])[:2])
    return PumpISA(x, y, pr["run_tag"], pair=pair, size=pr["size"],
                   flip=pr["flip"])


def _valve(ctx, x, y, pr):
    return ValveISA(x, y, pr["tag"], ctx.controller,
                    vertical=pr["vertical"], show_pct=pr["show_pct"])


def _mov(ctx, x, y, pr):
    return MovISA(x, y, pr["base"])


def _equip(ctx, x, y, pr):
    return Equip(x, y, pr["w"], pr["h"], pr["number"], pr["name"],
                 kind=pr["kind"], level_tag=pr["level_tag"],
                 outline=pr["outline"], trays=pr["trays"],
                 ref=pr.get("ref") or pr["level_tag"])


def _junc(ctx, x, y, pr):
    return Junction(x, y, pr["text"], radius=pr["radius"])


def _textbox(ctx, x, y, pr):
    return TextBox(x, y, pr["text"], w=pr["w"], h=pr["h"])


def _label(ctx, x, y, pr):
    return Label(x, y, pr["text"], size=pr["size"], bold=pr["bold"],
                 colour=NAVY if pr["navy"] else TEXT)


def _image(ctx, x, y, pr):
    return ImageItem(x, y, _resolve_path(pr["path"]), w=pr["w"],
                     h=pr["h"], line_color=pr.get("line_color", ""),
                     fill_color=pr.get("fill_color", ""))


def _connector(ctx, x, y, pr):
    a = ctx.items_by_id.get(pr["from_id"])
    b = ctx.items_by_id.get(pr["to_id"])
    if a is None or b is None:
        log.warning("Connector endpoints %s -> %s not found; skipped",
                    pr["from_id"], pr["to_id"])
        return None
    bt = pr.get("bt")
    b_anchor = (bt, 0.0) if bt is not None else (pr["bx"], pr["by"])
    at = pr.get("at")
    a_anchor = (at, 0.0) if at is not None else (pr["ax"], pr["ay"])
    arrows = pr.get("arrows") or "end"
    if arrows == "end" and not pr.get("arrow", True):
        arrows = "none"               # pages saved before arrow styles
    return Connector(a, a_anchor, b, b_anchor,
                     a_side=pr["a_side"], b_side=pr["b_side"],
                     width=pr["width"], navy=pr["navy"],
                     ortho=pr["ortho"], bends=pr.get("bends") or [],
                     colour=pr.get("color", ""), arrows=arrows)


def _pipe(ctx, x, y, pr):
    from PySide6.QtGui import QColor
    pts = [tuple(p) for p in pr["pts"]] or [(0.0, 0.0), (60.0, 0.0)]
    colour = (QColor(pr["color"]) if pr.get("color")
              else NAVY if pr["navy"] else None)
    it = Pipe(pts, width=pr["width"],
              arrow_at=0.6 if pr["arrow"] else None, colour=colour)
    it.setPos(x, y)
    return it


@dataclass
class Spec:
    label: str
    make: Callable
    props: List[Tuple[str, str, str, object]]   # name, caption, kind, default


#: palette order; kinds: tag / module / text / float / int / bool /
#: choice:a|b|c / hidden
SPECS: Dict[str, Spec] = {
    "vbar": Spec("AI bar dynamo", _vbar, [
        ("tag", "Tag", "tag", ""),
        ("unit", "Units (blank = EU)", "text", ""),
        ("lo", "Scale EU0", "float", 0.0),
        ("hi", "Scale EU100", "float", 100.0),
        ("height", "Bar height", "float", 90.0),
        ("decimals", "Decimals", "int", 0)]),
    "meas": Spec("AI value", _meas, [
        ("tag", "Tag", "tag", ""),
        ("unit", "Units (blank = EU)", "text", ""),
        ("decimals", "Decimals", "int", 1)]),
    "trend": Spec("Trend tile", _trend, [
        ("ref", "Loop or tag", "tag", ""),
        ("caption", "Caption", "text", ""),
        ("unit", "Units (blank = EU)", "text", ""),
        ("mini", "Mini card", "bool", True)]),
    "loop": Spec("Loop PVM", _loop, [
        ("module", "Module", "module", ""),
        ("unit", "Units", "text", ""),
        ("card", "Card style", "bool", True)]),
    "lamp": Spec("DI/DO lamp", _lamp, [
        ("tag", "Tag", "tag", ""),
        ("caption", "Caption", "text", ""),
        ("on_text", "Text when 1", "text", "ON"),
        ("off_text", "Text when 0", "text", "OFF"),
        ("size", "Lamp size", "float", 20.0)]),
    "pump": Spec("Pump", _pump, [
        ("run_tag", "Run contact", "tag", ""),
        ("pair", "Duty letters", "text", "A,B"),
        ("size", "Size", "float", 26.0),
        ("flip", "Flow to the left", "bool", False)]),
    "valve": Spec("Control valve", _valve, [
        ("tag", "Position tag", "tag", ""),
        ("vertical", "Vertical line", "bool", False),
        ("show_pct", "Show percent", "bool", True)]),
    "mov": Spec("Motor valve (MOV)", _mov, [
        ("base", "Base name", "text", "MOV1002")]),
    "equip": Spec("Equipment", _equip, [
        ("number", "Number", "text", "V-1"),
        ("name", "Name", "text", "VESSEL"),
        ("kind", "Kind",
         "choice:vessel|column|hvessel|tank|hx|heater|comp", "vessel"),
        ("w", "Width", "float", 64.0),
        ("h", "Height", "float", 150.0),
        ("level_tag", "Level tag", "tag", ""),
        ("ref", "Click-through (tag / SCENE:name)", "text", ""),
        ("outline", "Outline style", "bool", True),
        ("trays", "Trays", "int", 0)]),
    "junction": Spec("Junction", _junc, [
        ("text", "Caption", "text", "MIX"),
        ("radius", "Radius", "float", 30.0)]),
    "textbox": Spec("Text box", _textbox, [
        ("text", "Text", "text", "STREAM"),
        ("w", "Width", "float", 118.0),
        ("h", "Height", "float", 46.0)]),
    "label": Spec("Label", _label, [
        ("text", "Text", "text", "LABEL"),
        ("size", "Point size", "float", 8.0),
        ("bold", "Bold", "bool", True),
        ("navy", "Navy colour", "bool", True)]),
    "image": Spec("Image (SVG / PNG)...", _image, [
        ("path", "File", "text", ""),
        ("w", "Width (0 = natural)", "float", 0.0),
        ("h", "Height (0 = natural)", "float", 0.0),
        ("line_color", "Line colour", "color", ""),
        ("fill_color", "Fill colour", "color", ""),
        ("backdrop", "Lock as background", "bool", False)]),
    "pipe": Spec("Pipe (click waypoints)", _pipe, [
        ("pts", "", "hidden", []),
        ("width", "Line width", "float", 2.2),
        ("arrow", "Arrowhead", "bool", True),
        ("navy", "Navy colour", "bool", True),
        ("color", "Line colour", "color", "")]),
    "connector": Spec("Connector (drag or 2 clicks)", _connector, [
        ("from_id", "", "hidden", 0),
        ("to_id", "", "hidden", 0),
        ("bends", "", "hidden", []),
        ("ax", "", "hidden", 0.5), ("ay", "", "hidden", 0.5),
        ("a_side", "", "hidden", "R"),
        ("bx", "", "hidden", 0.5), ("by", "", "hidden", 0.5),
        ("b_side", "", "hidden", "L"),
        ("bt", "", "hidden", None),
        ("at", "", "hidden", None),
        ("arrow", "", "hidden", True),
        ("width", "Line width", "float", 2.2),
        ("arrows", "Arrowheads", "choice:end|none|start|both", "end"),
        ("navy", "Navy colour", "bool", True),
        ("color", "Line colour", "color", ""),
        ("ortho", "Orthogonal route", "bool", True)]),
}


#: which record prop a resize grip drives, per dimension:
#: dim -> (prop, factor, offset) with prop = pixels * factor + offset
SIZING: Dict[str, Dict[str, Tuple[str, float, float]]] = {
    "image": {"w": ("w", 1.0, 0.0), "h": ("h", 1.0, 0.0)},
    "equip": {"w": ("w", 1.0, 0.0), "h": ("h", 1.0, 0.0)},
    "textbox": {"w": ("w", 1.0, 0.0), "h": ("h", 1.0, 0.0)},
    "vbar": {"h": ("height", 1.0, -34.0)},
    "junction": {"w": ("radius", 0.5, 0.0), "h": ("radius", 0.5, 0.0)},
    "lamp": {"h": ("size", 1.0, -30.0)},
    "pump": {"w": ("size", 1.0, 0.0)},
}

#: the reference's semantic sizing rule: vessel-like artwork stretches
#: per axis, compact equipment keeps its proportions
_FREE_ASPECT = re.compile(
    r"column|tower|vessel|tank|drum|separator|reactor|furnace|boiler"
    r"|stack|chimney")


def _keeps_aspect(rec: dict) -> bool:
    override = rec.get("lock_aspect")
    if override is not None:
        return bool(override)
    if rec["type"] in ("equip", "textbox"):
        return False
    if rec["type"] == "image":
        return not _FREE_ASPECT.search(str(rec["props"].get("path", "")))
    return True


def _arc_t(pts, q) -> float:
    """Arc-length position of the projection of q onto the polyline -
    where along the route a click landed, for bend insertion order."""
    best, best_t, acc = 1e18, 0.0, 0.0
    for a, b in zip(pts, pts[1:]):
        abx, aby = b.x() - a.x(), b.y() - a.y()
        l2 = abx * abx + aby * aby
        if l2 < 1e-9:
            continue
        t = max(0.0, min(1.0, ((q.x() - a.x()) * abx
                               + (q.y() - a.y()) * aby) / l2))
        px, py = a.x() + abx * t, a.y() + aby * t
        d = (q.x() - px) ** 2 + (q.y() - py) ** 2
        seg = math.sqrt(l2)
        if d < best:
            best, best_t = d, acc + seg * t
        acc += seg
    return best_t


#: the four hover connection ports: side -> (fx, fy, side)
_PORTS = {"L": (0.0, 0.5, "L"), "R": (1.0, 0.5, "R"),
          "T": (0.5, 0.0, "T"), "B": (0.5, 1.0, "B")}


def _nearest_on(pts, q) -> QPointF:
    """The closest point on a polyline to q."""
    best, out = 1e18, pts[0]
    for a, b in zip(pts, pts[1:]):
        abx, aby = b.x() - a.x(), b.y() - a.y()
        l2 = abx * abx + aby * aby
        if l2 < 1e-9:
            continue
        t = max(0.0, min(1.0, ((q.x() - a.x()) * abx
                               + (q.y() - a.y()) * aby) / l2))
        px, py = a.x() + abx * t, a.y() + aby * t
        d = (q.x() - px) ** 2 + (q.y() - py) ** 2
        if d < best:
            best, out = d, QPointF(px, py)
    return out


def _edge_anchor(item, scene_pos):
    """The clicked point as a normalized anchor, projected onto the
    nearest edge of the symbol so the connector starts on its rim.
    Mapped through the item so rotated or mirrored symbols anchor
    correctly. A click near the middle of an edge snaps to the exact
    mid-edge port, so opposing lines land level."""
    local = item.mapFromScene(scene_pos)
    w = getattr(item, "_w", 60.0)
    h = getattr(item, "_h", 60.0)
    fx = min(max((local.x() + w / 2) / w, 0.0), 1.0)
    fy = min(max((local.y() + h / 2) / h, 0.0), 1.0)

    ports = getattr(item, "ports", None) or {}

    def result(side: str, free: float, axis: int):
        """Land on the artwork, never on the bounding box.

        A click near the symbol's declared port snaps to it outright.
        Otherwise the click keeps its position ALONG the edge but
        adopts the port's perpendicular inset, because that is where
        the ink is - a nozzle that stops short of the viewBox is what
        leaves a visible gap."""
        if side in ports:
            ax, ay = ports[side]
            free_port = ay if axis == 1 else ax
            if abs(free - free_port) <= 0.18:
                return ax, ay, side
            inset = ax if axis == 1 else ay
            return ((inset, free, side) if axis == 1
                    else (free, inset, side))
        edge = {"L": (0.0, free), "R": (1.0, free),
                "T": (free, 0.0), "B": (free, 1.0)}[side]
        if abs(free - 0.5) <= 0.15:
            edge = {"L": (0.0, 0.5), "R": (1.0, 0.5),
                    "T": (0.5, 0.0), "B": (0.5, 1.0)}[side]
        return edge[0], edge[1], side

    dl, dr, dt, db = fx, 1.0 - fx, fy, 1.0 - fy
    m = min(dl, dr, dt, db)
    if m == dl:
        return result("L", fy, 1)
    if m == dr:
        return result("R", fy, 1)
    if m == dt:
        return result("T", fx, 0)
    return result("B", fx, 0)


def _apply_placement(it, rot: float = 0.0, flip: bool = False,
                     z=None) -> None:
    """Placement attributes shared by the builder and the viewer."""
    if rot:
        it.setRotation(float(rot))
    if flip:
        it.setTransform(QTransform().scale(-1.0, 1.0))
    if z is not None:
        it.setZValue(float(z))


def _make_item(key: str, x: float, y: float, props: dict,
               ctx: BuildContext):
    spec = SPECS.get(key)
    if spec is None:
        log.warning("Unknown display item type %r skipped", key)
        return None
    pr = {n: d for n, _c, _k, d in spec.props}
    pr.update(props or {})
    try:
        return spec.make(ctx, float(x), float(y), pr)
    except Exception:
        log.exception("Could not build display item %s from %s", key, pr)
        return None


def build_user_scene(path, db, controller=None, alarms=None,
                     scene_cls=HmiScene) -> HmiScene:
    """A live HmiScene from a saved builder page."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ctx = BuildContext(db, controller, alarms)
    s = scene_cls(str(data.get("title") or Path(path).stem.upper()))
    w, h = (data.get("size") or [1560, 900])[:2]
    s.setSceneRect(QRectF(0, 0, float(w), float(h)))
    if data.get("background"):
        s.setBackgroundBrush(QColor(data["background"]))
    recs = data.get("items", [])
    # symbols, then connectors, then branches that tap connectors
    for rec in sorted(recs, key=conn_rank):
        it = _make_item(rec.get("type", ""), rec.get("x", 0.0),
                        rec.get("y", 0.0), rec.get("props", {}), ctx)
        if it is not None:
            s.add(it)
            _apply_placement(it, rec.get("rot", 0.0),
                             rec.get("flip", False), rec.get("z"))
            if (rec.get("type") == "image" and rec.get("z") is None
                    and rec.get("props", {}).get("backdrop")):
                it.setZValue(-50.0)
            if rec.get("id") is not None:
                ctx.items_by_id[rec["id"]] = it
    return s


# ------------------------------------------------------------------ builder
class DisplayBuilder(QMainWindow):
    """The builder application window: menus, an icon toolbar, a
    Symbols dock, the canvas, a Format dock and a status bar. Del
    removes, Esc cancels the armed tool."""

    def __init__(self, db, controller=None, alarms=None) -> None:
        super().__init__()
        self.ctx = BuildContext(db, controller, alarms)
        self._ids = self.ctx.items_by_id
        self._recs: Dict[int, dict] = {}
        self._next_id = 1
        self._conn_first = None
        self._path: Optional[Path] = None
        self._pending: Optional[Tuple[str, dict]] = None
        self._pipe_pts: List[Tuple[float, float]] = []
        self._placed: List[QGraphicsItem] = []
        self._editors: Dict[str, Tuple[str, QWidget]] = {}
        self._edited = None
        self._edited_id: Optional[int] = None
        self._loading = False
        self._dirty: set = set()
        self._form_item = None
        self._form_queued = False
        self._clipboard: List[dict] = []
        self._alt_drag: Optional[dict] = None
        self._bend_drag: Optional[dict] = None
        self._seg_drag: Optional[dict] = None
        self._conn_pressed = False
        self._smart_gesture = False
        self._sel_order: List[int] = []

        self.setWindowTitle("Display builder  ·  AzeoPlant")
        self.setStyleSheet(theme.STYLESHEET + _BUILDER_QSS)
        self.resize(1500, 900)

        self.undo = QUndoStack(self)
        self.undo.setUndoLimit(100)
        self.undo.indexChanged.connect(self._resync_form)
        self.undo.cleanChanged.connect(lambda _c: self._sync_title())
        self._autosave = QTimer(self)
        self._autosave.setInterval(45000)
        self._autosave.timeout.connect(self._write_autosave)
        self._autosave.start()
        self._obj_timer = QTimer(self)
        self._obj_timer.setSingleShot(True)
        self._obj_timer.timeout.connect(self._fill_objects)
        self._syncing_objects = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._flush_dirty)

        self.scene = BuilderScene("USER DISPLAY")
        self.scene.setSceneRect(QRectF(0, 0, 1560, 900))
        self.scene.selectionChanged.connect(self._sel_changed)
        self.scene.on_moved = self._push_move

        self.view = BuilderView(self.scene)
        self.view.viewport().installEventFilter(self)
        self.view.viewport().setMouseTracking(True)

        self.overlay = HandlesOverlay()
        self.scene.addItem(self.overlay)
        self.overlay.on_commit = self._commit_geometry
        self.view.zoomChanged.connect(self.overlay.set_view_scale)

        # ----------------------------------------------------- actions/menus
        self._title = "USER DISPLAY"
        self.setWindowTitle(
            f"{self._title}  ·  Display builder  ·  AzeoPlant")

        def act(text, cb, icon=None, tip=None, seq=None,
                view_scope=False, checkable=False) -> QAction:
            a = QAction(text, self)
            if icon:
                a.setIcon(make_icon(icon))
            a.setToolTip(tip or text.replace("&", "").replace("...", ""))
            a.setCheckable(checkable)
            a.triggered.connect(lambda _=False: cb())
            if seq is not None:
                a.setShortcut(QKeySequence(seq))
                if view_scope:
                    # active only while the canvas has focus, so text
                    # fields keep their own clipboard keys
                    a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
                    self.view.addAction(a)
            return a

        self.act_new = act("&New", self._new, "new", seq="Ctrl+N")
        self.act_open = act("&Open...", self._open, "open", seq="Ctrl+O")
        self.act_save = act("&Save", self._save, "save", seq="Ctrl+S")
        self.act_save_as = act("Save &as...", self._save_as,
                               seq="Ctrl+Shift+S")
        self.act_png = act("Export &PNG...", self._export_png, "export")
        self.act_svg = act("Export SV&G...", self._export_svg)
        self.act_import_ov = act("Import built-in over&view",
                                 self._import_overview, "displays",
                                 "Turn the built-in plant overview "
                                 "into an editable page")
        self.act_checks = act("Drawing &checks", self._run_checks,
                              "forces",
                              "Validate every tag, module, image and "
                              "connection on this page (F7)",
                              seq="F7", view_scope=True)
        self.act_close = act("&Close", self.close, seq="Ctrl+W")

        self.act_undo = self.undo.createUndoAction(self, "&Undo")
        self.act_undo.setIcon(make_icon("undo"))
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_redo = self.undo.createRedoAction(self, "&Redo")
        self.act_redo.setIcon(make_icon("redo"))
        self.act_redo.setShortcuts([QKeySequence.Redo,
                                    QKeySequence("Ctrl+Shift+Z")])
        for a in (self.act_undo, self.act_redo):
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            self.view.addAction(a)

        self.act_cut = act("Cu&t", self._cut, seq=QKeySequence.Cut,
                           view_scope=True)
        self.act_copy = act("&Copy", self._copy, seq=QKeySequence.Copy,
                            view_scope=True)
        self.act_paste = act("&Paste", self._paste, seq=QKeySequence.Paste,
                             view_scope=True)
        self.act_dup = act("&Duplicate", self._duplicate, seq="Ctrl+D",
                           view_scope=True)
        self.act_del = act("De&lete", self._delete_selected, "delete",
                           "Delete the selection (Del)",
                           seq=QKeySequence.Delete, view_scope=True)
        self.act_sel_all = act("Select &all", self._select_all,
                               seq="Ctrl+A", view_scope=True)

        self.act_conn = act("&Connector", lambda: self._arm("connector"),
                            "connector",
                            "Connect two symbols with an arrow-headed "
                            "line: press on the source, drag, release on "
                            "the target (C)", seq="C", view_scope=True,
                            checkable=True)
        self.act_pipe = act("&Pipe", lambda: self._arm("pipe"), "pipe",
                            "Draw a free line: click waypoints, "
                            "double-click to finish (P)", seq="P",
                            view_scope=True, checkable=True)

        align_specs = (("al_l", "Align left edges", "left"),
                       ("al_c", "Align horizontal centres", "hcenter"),
                       ("al_r", "Align right edges", "right"),
                       ("al_t", "Align top edges", "top"),
                       ("al_m", "Align vertical centres", "vcenter"),
                       ("al_b", "Align bottom edges", "bottom"))
        self.acts_align = [act(tip, lambda m=mode: self._align(m),
                               icon, tip)
                           for icon, tip, mode in align_specs]
        self.act_dist_h = act("Distribute &horizontally",
                              lambda: self._align("dist_h"), "dist_h")
        self.act_dist_v = act("Distribute &vertically",
                              lambda: self._align("dist_v"), "dist_v")
        self.act_match_w = act("Match &width",
                               lambda: self._match_size("w"), None,
                               "Match width to the last-selected symbol")
        self.act_match_h = act("Match hei&ght",
                               lambda: self._match_size("h"), None,
                               "Match height to the last-selected symbol")
        self.act_rot_l = act("Rotate &left 90°",
                             lambda: self._rotate_sel(-90), "rot_l")
        self.act_rot_r = act("Rotate &right 90°",
                             lambda: self._rotate_sel(90), "rot_r")
        self.act_flip = act("&Mirror", self._flip_sel, "flip")
        self.act_front = act("Bring to &front",
                             lambda: self._reorder("front"), "front",
                             seq="Ctrl+Shift+]", view_scope=True)
        self.act_fwd = act("Bring f&orward",
                           lambda: self._reorder("forward"),
                           seq="Ctrl+]", view_scope=True)
        self.act_bwd = act("Send bac&kward",
                           lambda: self._reorder("backward"),
                           seq="Ctrl+[", view_scope=True)
        self.act_back = act("Send to &back",
                            lambda: self._reorder("back"), "back_z",
                            seq="Ctrl+Shift+[", view_scope=True)
        self.act_line_align = act("&Align line endpoints",
                                  self._conn_align, "line_align",
                                  "Move the target symbol so the "
                                  "selected line's ends share an axis")
        self.act_straighten = act("&Straighten line",
                                  self._conn_straighten)
        self.act_reroute = act("&Re-route line", self._conn_reset)
        self.act_fit = act("&Fit", self.view.fit, "fit",
                           "Fit the selection or the page (Ctrl+0)",
                           seq="Ctrl+0", view_scope=True)
        self.act_zin = act("Zoom &in", lambda: self.view.zoom_step(1),
                           "zoom_in", seq="Ctrl+=", view_scope=True)
        self.act_zout = act("Zoom &out", lambda: self.view.zoom_step(-1),
                            "zoom_out", seq="Ctrl+-", view_scope=True)

        # ---------------------------------------------------------- palette
        # pipes and connectors are tools on the Draw band, not stencils
        self.palette = QListWidget()
        for key, spec in SPECS.items():
            if key in ("pipe", "connector"):
                continue
            item = QListWidgetItem(spec.label)
            item.setData(Qt.UserRole, key)
            self.palette.addItem(item)
        self.palette.itemClicked.connect(self._palette_clicked)
        self.palette.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.palette.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._fit_palette()
        # and again once laid out: sizeHintForRow does not know about
        # the stylesheet's row padding, which clipped the last entry
        QTimer.singleShot(0, self._fit_palette)
        self.hint = QLabel("Pick a symbol and click the canvas - "
                           "right-click anything for actions.")
        self.hint.setObjectName("builderHint")

        # ---------------------------------------------- P&ID symbol library
        self.sym_search = QLineEdit()
        self.sym_search.setPlaceholderText("Filter symbols...")
        self.sym_search.textChanged.connect(self._sym_filter)
        self.sym_tree = QTreeWidget()
        self.sym_tree.setHeaderHidden(True)
        self.sym_tree.setIconSize(QSize(26, 26))
        # no fixed width: the panel is a resizable dock, and pinning
        # the trees narrower than it left a dead strip beside them and
        # truncated the symbol names
        self.sym_tree.setMinimumWidth(150)
        self.sym_tree.itemClicked.connect(self._sym_clicked)
        self._fill_symbols()

        def _header(text: str) -> QLabel:
            lb = QLabel(text)
            lb.setObjectName("panelHeader")
            lb.setTextFormat(Qt.PlainText)   # '&' is a character here
            return lb

        left_w = QWidget()
        left_w.setMinimumWidth(190)
        left = QVBoxLayout(left_w)
        left.setContentsMargins(6, 6, 6, 6)
        left.addWidget(_header("DYNAMOS & PVMS"))
        left.addWidget(self.palette)
        left.addWidget(_header("P&ID SYMBOLS"))
        left.addWidget(self.sym_search)
        left.addWidget(self.sym_tree, 1)

        # ------------------------------------------------------- properties
        self.form_holder = QWidget()
        self.form_holder.setObjectName("formCard")
        self.form = QFormLayout(self.form_holder)
        self.form.setContentsMargins(10, 10, 10, 10)
        scroll = QScrollArea()
        scroll.setWidget(self.form_holder)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(280)
        self._build_form(None)

        # -------------------------------------------------- docks & central
        self.setCentralWidget(self.view)
        feats = (QDockWidget.DockWidgetMovable
                 | QDockWidget.DockWidgetFloatable
                 | QDockWidget.DockWidgetClosable)
        self.dock_sym = QDockWidget("Symbols", self)
        self.dock_sym.setObjectName("SymbolsDock")
        self.dock_sym.setFeatures(feats)
        self.dock_sym.setWidget(left_w)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_sym)
        self.dock_fmt = QDockWidget("Format", self)
        self.dock_fmt.setObjectName("FormatDock")
        self.dock_fmt.setFeatures(feats)
        self.dock_fmt.setWidget(scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock_fmt)

        # ------------------------------------------------ objects panel
        obj_w = QWidget()
        obj_l = QVBoxLayout(obj_w)
        obj_l.setContentsMargins(6, 6, 6, 6)
        self.obj_search = QLineEdit()
        self.obj_search.setPlaceholderText("Filter objects...")
        self.obj_search.textChanged.connect(self._fill_objects)
        self.obj_tree = QTreeWidget()
        self.obj_tree.setHeaderLabels(["Object", "Tag / text"])
        self.obj_tree.setRootIsDecorated(False)
        self.obj_tree.setAlternatingRowColors(True)
        self.obj_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.obj_tree.itemSelectionChanged.connect(self._objects_picked)
        self.obj_tree.itemDoubleClicked.connect(self._objects_reveal)
        obj_l.addWidget(self.obj_search)
        obj_l.addWidget(self.obj_tree, 1)
        self.dock_obj = QDockWidget("Objects", self)
        self.dock_obj.setObjectName("ObjectsDock")
        self.dock_obj.setFeatures(feats)
        self.dock_obj.setWidget(obj_w)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_obj)
        self.tabifyDockWidget(self.dock_sym, self.dock_obj)
        self.dock_sym.raise_()

        # ---------------------------------------------- drawing checks
        self.check_list = QListWidget()
        self.check_list.itemDoubleClicked.connect(self._check_reveal)
        self.dock_chk = QDockWidget("Drawing checks", self)
        self.dock_chk.setObjectName("ChecksDock")
        self.dock_chk.setFeatures(feats)
        self.dock_chk.setWidget(self.check_list)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_chk)
        self.dock_chk.hide()

        # --------------------------------------------------------- menu bar
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.act_new, self.act_open, self.act_save,
                  self.act_save_as):
            m.addAction(a)
        self.menu_recent = m.addMenu("Open &recent")
        self.menu_recent.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        m.addAction(self.act_import_ov)
        m.addSeparator()
        m.addAction(self.act_png)
        m.addAction(self.act_svg)
        m.addSeparator()
        m.addAction(self.act_close)
        m = mb.addMenu("&Edit")
        for a in (self.act_undo, self.act_redo):
            m.addAction(a)
        m.addSeparator()
        for a in (self.act_cut, self.act_copy, self.act_paste,
                  self.act_dup, self.act_del):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_sel_all)
        m = mb.addMenu("&Arrange")
        for a in self.acts_align:
            m.addAction(a)
        m.addSeparator()
        for a in (self.act_dist_h, self.act_dist_v, self.act_match_w,
                  self.act_match_h):
            m.addAction(a)
        m.addSeparator()
        for a in (self.act_rot_l, self.act_rot_r, self.act_flip):
            m.addAction(a)
        m.addSeparator()
        for a in (self.act_front, self.act_fwd, self.act_bwd,
                  self.act_back):
            m.addAction(a)
        m.addSeparator()
        for a in (self.act_line_align, self.act_straighten,
                  self.act_reroute):
            m.addAction(a)
        m = mb.addMenu("&View")
        for a in (self.act_fit, self.act_zin, self.act_zout):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_checks)
        m.addSeparator()
        for d in (self.dock_sym, self.dock_obj, self.dock_fmt):
            m.addAction(d.toggleViewAction())

        # ---------------------------------------------------------- toolbar
        tb = QToolBar("Tools", self)
        tb.setObjectName("BuilderToolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)
        for group in ((self.act_new, self.act_open, self.act_save),
                      (self.act_undo, self.act_redo),
                      (self.act_conn, self.act_pipe),
                      tuple(self.acts_align)
                      + (self.act_dist_h, self.act_dist_v),
                      (self.act_rot_l, self.act_rot_r, self.act_flip),
                      (self.act_front, self.act_back,
                       self.act_line_align),
                      (self.act_fit, self.act_zin, self.act_zout),
                      (self.act_png, self.act_checks)):
            for a in group:
                tb.addAction(a)
            tb.addSeparator()
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        tb.addWidget(spacer)
        tb.addAction(self.act_del)

        # ------------------------------------------------------- status bar
        sb = self.statusBar()
        sb.addWidget(self.hint, 1)
        self.sel_lbl = QLabel("")
        sb.addPermanentWidget(self.sel_lbl)
        self.coord_lbl = QLabel("")
        sb.addPermanentWidget(self.coord_lbl)
        self.zoom_lbl = QLabel("100%")
        sb.addPermanentWidget(self.zoom_lbl)
        self.view.zoomChanged.connect(
            lambda s: self.zoom_lbl.setText(f"{s * 100:.0f}%"))

        sc_esc = QShortcut(QKeySequence(Qt.Key_Escape), self.view,
                           self._cancel_pending)
        sc_esc.setContext(Qt.WidgetWithChildrenShortcut)
        for key, dx, dy in ((Qt.Key_Left, -1, 0), (Qt.Key_Right, 1, 0),
                            (Qt.Key_Up, 0, -1), (Qt.Key_Down, 0, 1)):
            for seq, mult in ((QKeySequence(key), 1),
                              (QKeySequence(Qt.SHIFT | key), 10)):
                sc = QShortcut(seq, self.view,
                               lambda dx=dx, dy=dy, m=mult:
                               self._nudge(dx * m, dy * m))
                sc.setContext(Qt.WidgetWithChildrenShortcut)

        self._tag_names = sorted(t.name for t in db.all())
        self._module_names = sorted(getattr(controller, "loops", {}) or [])
        self.undo.setClean()
        self._sync_title()
        self._fill_objects()

    def _fit_palette(self) -> None:
        """Show every stencil: size the list to its laid-out rows."""
        n = self.palette.count()
        if not n:
            return
        self.palette.ensurePolished()
        bottom = self.palette.visualItemRect(self.palette.item(n - 1))
        need = bottom.bottom()
        if need <= 0:                       # not laid out yet
            need = sum(self.palette.sizeHintForRow(i) for i in range(n))
        self.palette.setFixedHeight(
            need + 2 * self.palette.frameWidth() + 4)

    # ------------------------------------------------------------ placement
    def _palette_clicked(self, item) -> None:
        self._arm(item.data(Qt.UserRole))

    def _arm(self, key) -> None:
        # clicking an armed tool again disarms it
        if self._pending is not None and self._pending[0] == key \
                and key in ("connector", "pipe"):
            self._cancel_pending()
            return
        self.act_conn.setChecked(key == "connector")
        self.act_pipe.setChecked(key == "pipe")
        if self.scene.hover_item is not None:
            self.scene.hover_item = None
            self.scene.hover_port = None
            self.scene.update()
        if key == "image":
            path, _ = QFileDialog.getOpenFileName(
                self, "Import image", str(DISPLAY_DIR),
                "Images (*.svg *.png *.jpg *.jpeg *.bmp)")
            if not path:
                self.palette.clearSelection()
                return
            self._pending = (key, {"path": _relativize(path)})
            self.hint.setText("Click the canvas to place the image.")
            return
        if key == "pipe":
            self._pending = (key, {})
            self._pipe_pts = []
            self.hint.setText("Click the waypoints; double-click to "
                              "finish, Esc cancels.")
            return
        if key == "connector":
            self._pending = (key, {})
            self._conn_first = None
            # the resize grips would sit on top of the ports
            self.overlay.set_target(None, set(), True)
            self.view.viewport().setCursor(Qt.CrossCursor)
            self.hint.setText("Press on a symbol (or a line), drag to "
                              "the target and release. The tool stays "
                              "on; Esc stops.")
            return
        self._pending = (key, {})
        self.hint.setText(f"Click the canvas to place: {SPECS[key].label}")

    def _fill_symbols(self) -> None:
        """The curated P&ID/ISA library, categorised with live icons."""
        if not SYMBOL_DIR.is_dir():
            self.sym_tree.hide()
            self.sym_search.hide()
            return
        for cat in sorted(d for d in SYMBOL_DIR.iterdir() if d.is_dir()):
            top = QTreeWidgetItem([cat.name.replace("_", " ").title()])
            self.sym_tree.addTopLevelItem(top)
            for f in sorted(cat.glob("*.svg")):
                name = re.sub(r"^(pid|isa)_", "", f.stem).replace("_", " ")
                leaf = QTreeWidgetItem([name])
                leaf.setIcon(0, QIcon(str(f)))
                leaf.setData(0, Qt.UserRole, _relativize(str(f)))
                top.addChild(leaf)
            top.setExpanded(True)

    def _sym_clicked(self, item, _col) -> None:
        path = item.data(0, Qt.UserRole)
        if not path:
            return
        self.palette.clearSelection()
        self._pending = ("image", {"path": path})
        self.hint.setText(f"Click the canvas to place: {item.text(0)}")

    def _sym_filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.sym_tree.topLevelItemCount()):
            top = self.sym_tree.topLevelItem(i)
            any_hit = False
            for j in range(top.childCount()):
                leaf = top.child(j)
                hit = not text or text in leaf.text(0).lower()
                leaf.setHidden(not hit)
                any_hit = any_hit or hit
            top.setHidden(not any_hit)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        # a failure in here must never escape: Qt calls the filter from
        # inside paint delivery, and a propagating exception turns into
        # a recursive-repaint storm plus a re-entrant error dialog
        try:
            return self._filter_canvas_event(obj, ev)
        except Exception:
            log.exception("Builder event filter failed")
            return False

    def _filter_canvas_event(self, obj, ev) -> bool:
        if obj is not self.view.viewport():
            return super().eventFilter(obj, ev)
        if ev.type() == QEvent.ContextMenu:
            self._context_menu(self.view.mapToScene(ev.pos()),
                               ev.globalPos())
            return True
        if ev.type() == QEvent.MouseMove:
            sp = self.view.mapToScene(ev.position().toPoint())
            self.coord_lbl.setText(f"{sp.x():.0f}, {sp.y():.0f}")
            armed = (self._pending is not None
                     and self._pending[0] == "connector")
            if (self._bend_drag is None and self._alt_drag is None
                    and self._seg_drag is None
                    and not (ev.buttons() & Qt.LeftButton)
                    and (self._pending is None or armed)):
                self._update_hover(sp)
        if self._pending is not None:
            if (ev.type() == QEvent.MouseButtonPress
                    and ev.button() == Qt.LeftButton):
                sp = self.view.mapToScene(ev.position().toPoint())
                key, props = self._pending
                if key == "pipe":
                    self._pipe_pts.append(
                        (round(sp.x() / GRID) * GRID,
                         round(sp.y() / GRID) * GRID))
                    return True
                if key == "connector":
                    self._connector_press(sp)
                    return True
                self._place(key, sp.x(), sp.y(), props)
                return True
            if (self._pending[0] == "connector"
                    and self._conn_first is not None
                    and self._conn_pressed):
                if ev.type() == QEvent.MouseMove:
                    self._connector_track(
                        self.view.mapToScene(ev.position().toPoint()))
                    return True
                if (ev.type() == QEvent.MouseButtonRelease
                        and ev.button() == Qt.LeftButton):
                    self._connector_release(
                        self.view.mapToScene(ev.position().toPoint()))
                    return True
            if (ev.type() == QEvent.MouseButtonDblClick
                    and self._pending[0] == "pipe"):
                self._finish_pipe()
                return True
            return super().eventFilter(obj, ev)
        # bend editing on connectors
        if (ev.type() == QEvent.MouseButtonPress
                and ev.button() == Qt.LeftButton
                and not (ev.modifiers() & Qt.AltModifier)):
            sp = self.view.mapToScene(ev.position().toPoint())
            if self._bend_press(sp):
                return True
            if self._seg_press(sp):
                return True
            if self._try_smart_press():
                return True
        if self._bend_drag is not None:
            sp = self.view.mapToScene(ev.position().toPoint()) \
                if ev.type() in (QEvent.MouseMove,
                                 QEvent.MouseButtonRelease) else None
            if ev.type() == QEvent.MouseMove:
                self._bend_move(sp, ev.modifiers())
                return True
            if (ev.type() == QEvent.MouseButtonRelease
                    and ev.button() == Qt.LeftButton):
                self._bend_release()
                return True
        if self._seg_drag is not None:
            if ev.type() == QEvent.MouseMove:
                self._seg_move(
                    self.view.mapToScene(ev.position().toPoint()),
                    ev.modifiers())
                return True
            if (ev.type() == QEvent.MouseButtonRelease
                    and ev.button() == Qt.LeftButton):
                self._seg_release()
                return True
        if (ev.type() == QEvent.MouseButtonDblClick
                and ev.button() == Qt.LeftButton):
            sp = self.view.mapToScene(ev.position().toPoint())
            if self._bend_dblclick(sp):
                return True
        # Alt-drag duplicates the selection and drags the copies
        if (ev.type() == QEvent.MouseButtonPress
                and ev.button() == Qt.LeftButton
                and ev.modifiers() & Qt.AltModifier):
            sp = self.view.mapToScene(ev.position().toPoint())
            if self._start_alt_drag(sp):
                return True
        elif self._alt_drag is not None:
            if ev.type() == QEvent.MouseMove:
                sp = self.view.mapToScene(ev.position().toPoint())
                d = sp - self._alt_drag["start"]
                for i, (x, y) in self._alt_drag["orig"].items():
                    it = self._ids.get(i)
                    if it is not None:
                        it.setPos(x + d.x(), y + d.y())
                return True
            if (ev.type() == QEvent.MouseButtonRelease
                    and ev.button() == Qt.LeftButton):
                self._finish_alt_drag()
                return True
        return super().eventFilter(obj, ev)

    def _start_alt_drag(self, sp) -> bool:
        hit = self._symbol_at(sp)
        if hit is None:
            return False
        if not hit.isSelected():
            self.scene.clearSelection()
            hit.setSelected(True)
        recs = [self._recs[i] for i in self._selected_ids()
                if i in self._recs]
        clones = clone_recs(recs, self._take_id)
        if not clones:
            return False
        self.undo.beginMacro("Duplicate")
        self.undo.push(AddCmd(self, clones))
        self.scene.clearSelection()
        for r in clones:
            it = self._ids.get(r["id"])
            if it is not None:
                it.setSelected(True)
        self._alt_drag = {
            "start": sp,
            "orig": {r["id"]: (r["x"], r["y"]) for r in clones
                     if r["type"] != "connector"}}
        return True

    def _finish_alt_drag(self) -> None:
        moves = []
        for i, (x0, y0) in self._alt_drag["orig"].items():
            it = self._ids.get(i)
            if it is None:
                continue
            x = round(it.pos().x() / GRID) * GRID
            y = round(it.pos().y() / GRID) * GRID
            it.setPos(x, y)
            moves.append((i, (x0, y0), (x, y)))
        if moves:
            self.undo.push(MoveCmd(self, moves))
        self.undo.endMacro()
        self._alt_drag = None

    def _place(self, key, x, y, props=None) -> None:
        rec = self._new_rec(key, round(x / GRID) * GRID,
                            round(y / GRID) * GRID, props)
        self.undo.push(AddCmd(self, [rec], text=f"Place {key}"))
        self._cancel_pending()
        it = self._ids.get(rec["id"])
        if it is not None:
            self.scene.clearSelection()
            it.setSelected(True)

    # ------------------------------------------------------- record store
    # Records - the JSON shape - are the source of truth; undo commands
    # mutate them and rebuild live items through these primitives.
    def _new_rec(self, key, x, y, props=None, item_id=None,
                 rot: float = 0.0, flip: bool = False, z=None,
                 lock_aspect=None) -> dict:
        merged = {n: d for n, _c, _k, d in SPECS[key].props}
        merged.update(props or {})
        if item_id is None:
            item_id = self._next_id
        self._next_id = max(self._next_id, item_id + 1)
        return {"type": key, "id": item_id, "x": float(x), "y": float(y),
                "props": merged, "rot": rot, "flip": flip, "z": z,
                "lock_aspect": lock_aspect}

    def _restore(self, rec: dict):
        """Create and register the live item for a record."""
        it = _make_item(rec["type"], rec["x"], rec["y"], rec["props"],
                        self.ctx)
        if it is None:
            return None
        self.scene.add(it)
        backdrop = (rec["type"] == "image"
                    and rec["props"].get("backdrop"))
        if rec["type"] != "connector" and not backdrop:
            it.setFlag(QGraphicsItem.ItemIsMovable, True)
            it.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        if not backdrop:              # a background never grabs clicks
            it.setFlag(QGraphicsItem.ItemIsSelectable, True)
        elif rec.get("z") is None:
            it.setZValue(-50.0)
        it._spec_key, it._props = rec["type"], rec["props"]
        it._item_id = rec["id"]
        _apply_placement(it, rec.get("rot", 0.0), rec.get("flip", False),
                         rec.get("z"))
        self._next_id = max(self._next_id, rec["id"] + 1)
        self._ids[rec["id"]] = it
        self._recs[rec["id"]] = rec
        self._placed.append(it)
        self._objects_dirty()
        return it

    def _delete_by_id(self, item_id) -> List[dict]:
        """Remove a record and its live item; a symbol takes its
        connectors with it. Returns the removed records, dependent
        connectors first."""
        removed: List[dict] = []
        rec = self._recs.get(item_id)
        if rec is None:
            return removed
        # anything connected to it goes too - including branches that
        # tap a deleted line
        for cid, crec in list(self._recs.items()):
            if cid != item_id and crec["type"] == "connector" \
                    and item_id in (crec["props"].get("from_id"),
                                    crec["props"].get("to_id")):
                removed += self._delete_by_id(cid)
        it = self._ids.pop(item_id, None)
        if it is not None:
            if getattr(self, "overlay", None) is not None \
                    and self.overlay.target is it:
                self.overlay.set_target(None, set(), True)
            if self.scene.hover_item is it:
                self.scene.hover_item = None
                self.scene.hover_port = None
            self.scene.removeItem(it)
            if isinstance(it, HmiItem) and it in self.scene.live:
                self.scene.live.remove(it)
            if it in self._placed:
                self._placed.remove(it)
        self._recs.pop(item_id, None)
        removed.append(rec)
        self._objects_dirty()
        return removed

    def _restore_many(self, recs) -> None:
        """Restore records with dependency retries, so a branch whose
        parent line is later in the list still comes back."""
        pending = list(recs)
        while pending:
            rest = [r for r in pending if self._restore(r) is None]
            if len(rest) == len(pending):
                for r in rest:
                    log.warning("Could not restore %s %s", r.get("type"),
                                r.get("id"))
                break
            pending = rest

    def _rebuild_item(self, item_id):
        """Re-create one item (and its attached connectors) from its
        record after the record changed."""
        removed = self._delete_by_id(item_id)
        if not removed:
            return None
        it = self._restore(removed[-1])
        self._restore_many(removed[:-1])
        return it

    def _push_move(self, moves) -> None:
        self.undo.push(MoveCmd(self, moves))

    def _take_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def _selected_ids(self) -> List[int]:
        return [it._item_id for it in self.scene.selectedItems()
                if hasattr(it, "_item_id")]

    # -------------------------------------------------- clipboard & arrange
    def _copy(self) -> None:
        self._clipboard = [copy_rec(self._recs[i])
                           for i in self._selected_ids()
                           if i in self._recs]
        if self._clipboard:
            self.hint.setText(f"Copied {len(self._clipboard)} item(s).")

    def _cut(self) -> None:
        self._copy()
        self._delete_selected()

    def _paste(self) -> None:
        hosts = [r for r in self._clipboard if r["type"] != "connector"]
        if not hosts:
            return
        vp = self.view.viewport()
        lp = vp.mapFromGlobal(QCursor.pos())
        at = self.view.mapToScene(
            lp if vp.rect().contains(lp) else vp.rect().center())
        cx = sum(r["x"] for r in hosts) / len(hosts)
        cy = sum(r["y"] for r in hosts) / len(hosts)
        dx = round((at.x() - cx) / GRID) * GRID
        dy = round((at.y() - cy) / GRID) * GRID
        self._add_clones(clone_recs(self._clipboard, self._take_id, dx, dy),
                         "Paste")

    def _duplicate(self) -> None:
        recs = [self._recs[i] for i in self._selected_ids()
                if i in self._recs]
        self._add_clones(clone_recs(recs, self._take_id, 20.0, 20.0),
                         "Duplicate")

    def _add_clones(self, clones, text) -> None:
        if not clones:
            return
        self.undo.push(AddCmd(self, clones, text=text))
        self.scene.clearSelection()
        for r in clones:
            it = self._ids.get(r["id"])
            if it is not None:
                it.setSelected(True)

    def _nudge(self, dx, dy) -> None:
        moves = []
        for i in self._selected_ids():
            rec = self._recs.get(i)
            if rec is None or rec["type"] == "connector":
                continue
            moves.append((i, (rec["x"], rec["y"]),
                          (rec["x"] + dx, rec["y"] + dy)))
        if moves:
            self.undo.push(MoveCmd(self, moves, text="Nudge",
                                   mergeable=True))

    def _align(self, mode) -> None:
        entries = []
        for i in self._selected_ids():
            rec, it = self._recs.get(i), self._ids.get(i)
            if rec is None or it is None or rec["type"] == "connector":
                continue
            entries.append((i, (rec["x"], rec["y"]),
                            it.sceneBoundingRect()))
        moves = align_moves(entries, mode)
        if moves:
            self.undo.push(MoveCmd(self, moves, text="Align"))

    def _commit_geometry(self, old: dict, new: dict) -> None:
        item_id = getattr(self.overlay.target, "_item_id", None)
        rec = self._recs.get(item_id) if item_id is not None else None
        if rec is None:
            return
        o, n = {}, {}
        if abs(new["rot"] - old["rot"]) > 1e-9:
            o["rot"], n["rot"] = rec.get("rot", 0.0), new["rot"] % 360.0
        opr, npr = {}, {}
        mapping = SIZING.get(rec["type"], {})
        for dim in ("w", "h"):
            if dim not in mapping or abs(new[dim] - old[dim]) < 1e-9:
                continue
            prop, factor, offset = mapping[dim]
            opr[prop] = rec["props"].get(prop)
            npr[prop] = new[dim] * factor + offset
        if opr:
            o["props"], n["props"] = opr, npr
        if (abs(new["cx"] - old["cx"]) > 1e-9
                or abs(new["cy"] - old["cy"]) > 1e-9):
            o["x"], o["y"] = rec["x"], rec["y"]
            n["x"], n["y"] = new["cx"], new["cy"]
        if n:
            text = "Rotate" if "rot" in n and "props" not in n else "Resize"
            self.undo.push(EditCmd(self, item_id, o, n, text=text))

    def _rotate_sel(self, delta: float) -> None:
        ids = [i for i in self._selected_ids()
               if i in self._recs and self._recs[i]["type"] != "connector"]
        if not ids:
            return
        self.undo.beginMacro("Rotate")
        for i in ids:
            r0 = self._recs[i].get("rot", 0.0)
            self.undo.push(EditCmd(self, i, {"rot": r0},
                                   {"rot": (r0 + delta) % 360.0},
                                   text="Rotate"))
        self.undo.endMacro()

    def _flip_sel(self) -> None:
        ids = [i for i in self._selected_ids()
               if i in self._recs and self._recs[i]["type"] != "connector"]
        if not ids:
            return
        self.undo.beginMacro("Mirror")
        for i in ids:
            f0 = bool(self._recs[i].get("flip", False))
            self.undo.push(EditCmd(self, i, {"flip": f0},
                                   {"flip": not f0}, text="Mirror"))
        self.undo.endMacro()

    def _match_size(self, dim: str) -> None:
        """Give the selection the last-selected symbol's width/height."""
        ids = [i for i in self._sel_order
               if i in self._recs and i in self._ids]
        if len(ids) < 2:
            return
        ref = self._ids[ids[-1]]
        target_px = getattr(ref, "_w" if dim == "w" else "_h", None)
        if target_px is None:
            return
        in_macro = False
        for i in ids[:-1]:
            rec = self._recs[i]
            mapping = SIZING.get(rec["type"], {}).get(dim)
            if mapping is None:
                continue
            prop, factor, offset = mapping
            new_v = target_px * factor + offset
            if rec["props"].get(prop) == new_v:
                continue
            if not in_macro:
                self.undo.beginMacro("Match size")
                in_macro = True
            self.undo.push(EditCmd(
                self, i, {"props": {prop: rec["props"].get(prop)}},
                {"props": {prop: new_v}}, text="Match size"))
        if in_macro:
            self.undo.endMacro()

    def _reorder(self, mode) -> None:
        ids = [i for i in self._selected_ids() if i in self._recs]
        if not ids:
            return
        zs = [it.zValue() for it in self._placed] or [0.0]
        zmax, zmin = max(zs), min(zs)
        changes = []
        for n, i in enumerate(ids):
            z0 = self._ids[i].zValue()
            z1 = {"front": zmax + 1 + n, "back": zmin - 1 - n,
                  "forward": z0 + 1, "backward": z0 - 1}[mode]
            changes.append((i, z0, z1))
        self.undo.push(ZCmd(self, changes))

    def _symbol_at(self, sp):
        for it in self.scene.items(sp):
            if (isinstance(it, HmiItem) and hasattr(it, "_spec_key")
                    and it._spec_key != "connector"
                    and not it._props.get("backdrop")):
                return it
        return None

    # -------------------------------------------------------- context menu
    def _context_menu(self, sp, gp) -> None:
        menu = QMenu(self)
        target = self._symbol_at(sp)
        conn = next((it for it in self.scene.items(sp)
                     if isinstance(it, Connector)
                     and hasattr(it, "_item_id")), None)
        if target is not None:
            if not target.isSelected():
                self.scene.clearSelection()
                target.setSelected(True)
            menu.addAction("Connect from here",
                           lambda: self._arm_connect_from(target, sp))
            trec = self._recs.get(getattr(target, "_item_id", None))
            if trec is not None and trec["type"] == "image":
                menu.addAction(
                    "Fit to sheet && lock as background",
                    lambda: self._make_backdrop(trec["id"]))
            menu.addSeparator()
            menu.addAction("Cut\tCtrl+X", self._cut)
            menu.addAction("Copy\tCtrl+C", self._copy)
            menu.addAction("Duplicate\tCtrl+D", self._duplicate)
            menu.addSeparator()
            menu.addAction("Rotate left 90°",
                           lambda: self._rotate_sel(-90))
            menu.addAction("Rotate right 90°",
                           lambda: self._rotate_sel(90))
            menu.addAction("Mirror", self._flip_sel)
            order = menu.addMenu("Order")
            for text, mode in (("Bring to front", "front"),
                               ("Bring forward", "forward"),
                               ("Send backward", "backward"),
                               ("Send to back", "back")):
                order.addAction(text,
                                lambda m=mode: self._reorder(m))
            menu.addSeparator()
            menu.addAction("Delete\tDel", self._delete_selected)
        elif conn is not None:
            self.scene.clearSelection()
            conn.setSelected(True)
            menu.addAction("Branch from here",
                           lambda: self._arm_branch_from(conn, sp))
            menu.addAction("Align endpoints (straight line)",
                           self._conn_align)
            menu.addAction("Add bend here",
                           lambda: self._bend_dblclick(sp))
            menu.addAction("Straighten", self._conn_straighten)
            menu.addAction("Re-route", self._conn_reset)
            menu.addSeparator()
            menu.addAction("Delete\tDel", self._delete_selected)
        else:
            act = menu.addAction("Paste here\tCtrl+V", self._paste)
            act.setEnabled(bool(self._clipboard))
            menu.addAction("Select all", self._select_all)
            menu.addAction("Fit view\tCtrl+0", self.view.fit)
            act = menu.addAction("Unlock background images",
                                 self._unlock_backdrops)
            act.setEnabled(any(
                r["type"] == "image" and r["props"].get("backdrop")
                for r in self._recs.values()))
        menu.exec(gp)

    def _make_backdrop(self, item_id: int) -> None:
        """Stretch the image over the sheet, drop it behind everything
        and take it out of the way of clicks and connections."""
        rec = self._recs.get(item_id)
        if rec is None:
            return
        r = self.scene.sceneRect()
        pr = rec["props"]
        self.undo.push(EditCmd(
            self, item_id,
            {"props": {"w": pr.get("w"), "h": pr.get("h"),
                       "backdrop": pr.get("backdrop", False)},
             "x": rec["x"], "y": rec["y"]},
            {"props": {"w": r.width(), "h": r.height(),
                       "backdrop": True},
             "x": r.center().x(), "y": r.center().y()},
            text="Background"))

    def _unlock_backdrops(self) -> None:
        ids = [i for i, r in self._recs.items()
               if r["type"] == "image" and r["props"].get("backdrop")]
        if not ids:
            return
        self.undo.beginMacro("Unlock background")
        for i in ids:
            self.undo.push(EditCmd(self, i,
                                   {"props": {"backdrop": True}},
                                   {"props": {"backdrop": False}},
                                   text="Unlock background"))
        self.undo.endMacro()

    def _arm_branch_from(self, conn, sp) -> None:
        self._pending = ("connector", {})
        self._conn_first = (conn,
                            (self._line_param(conn, sp), 0.0, "LINE"))
        self._conn_pressed = False
        self.hint.setText("Click the target symbol or line; "
                          "Esc cancels.")

    def _arm_connect_from(self, item, sp) -> None:
        self._pending = ("connector", {})
        self._conn_first = (item, _edge_anchor(item, sp))
        self._conn_pressed = False
        self.hint.setText("Click a point on the target symbol; "
                          "Esc cancels.")

    def _select_all(self) -> None:
        for it in self._placed:
            it.setSelected(True)

    # ------------------------------------------------- smart hover ports
    @staticmethod
    def _item_ports(it) -> dict:
        """side -> (fx, fy): the symbol's declared connection anchors,
        falling back to the edge midpoints."""
        declared = getattr(it, "ports", None) or {}
        return {side: declared.get(side, (fx, fy))
                for side, (fx, fy, _s) in _PORTS.items()}

    def _port_of(self, it, sp, radius: float = 11.0):
        if it is None:
            return None
        w = getattr(it, "_w", 60.0)
        h = getattr(it, "_h", 60.0)
        best, bd = None, radius
        for side, (fx, fy) in self._item_ports(it).items():
            c = it.mapToScene(QPointF(-w / 2 + fx * w, -h / 2 + fy * h))
            d = math.hypot(c.x() - sp.x(), c.y() - sp.y())
            if d <= bd:
                best, bd = side, d
        return best

    def _armed_connector(self) -> bool:
        return (self._pending is not None
                and self._pending[0] == "connector")

    def _target_at(self, sp, radius: float = 26.0):
        """Magnetic targeting: the symbol under the pointer, else the
        nearest one within reach, so a release near a symbol still
        lands on it."""
        it = self._symbol_at(sp)
        if it is not None:
            return it
        best, bd = None, radius
        for cand in self._placed:
            if (not isinstance(cand, HmiItem)
                    or not hasattr(cand, "_spec_key")
                    or cand._spec_key == "connector"
                    or cand._props.get("backdrop")):
                continue
            r = cand.sceneBoundingRect()
            dx = max(r.left() - sp.x(), 0.0, sp.x() - r.right())
            dy = max(r.top() - sp.y(), 0.0, sp.y() - r.bottom())
            d = math.hypot(dx, dy)
            if d < bd:
                best, bd = cand, d
        return best

    def _update_hover(self, sp) -> None:
        armed = self._armed_connector()
        it = self._symbol_at(sp)
        if it is None and self.scene.hover_item is not None:
            # the dots stick out a little past the edge - keep the
            # hover alive while the pointer is on one of them
            if self._port_of(self.scene.hover_item, sp) is not None:
                it = self.scene.hover_item
        if it is None and armed:
            it = self._target_at(sp)      # magnetic while drawing
        if it is not None and it.isSelected() and not armed:
            it = None                 # a selected item shows grips instead
        port = self._port_of(it, sp)
        # with the tool armed, a line under the pointer is a branch
        # source: show where the branch would tee off
        pt = None
        if armed and it is None:
            line = self._line_at(sp)
            if line is not None:
                pt = _nearest_on(line._pts, sp)
        if (it is not self.scene.hover_item
                or port != self.scene.hover_port
                or pt != self.scene.conn_hover_pt):
            self.scene.hover_item = it
            self.scene.hover_port = port
            self.scene.conn_hover_pt = pt
            self.scene.update()
        self.view.viewport().setCursor(
            Qt.CrossCursor if (port or armed) else Qt.ArrowCursor)

    def _try_smart_press(self) -> bool:
        it, port = self.scene.hover_item, self.scene.hover_port
        if it is None or port is None:
            return False
        fx, fy = self._item_ports(it)[port]
        self._pending = ("connector", {})
        self._conn_first = (it, (fx, fy, port))
        self._conn_pressed = True
        self._smart_gesture = True
        self.act_conn.setChecked(True)
        self.scene.hover_item = None
        self.scene.hover_port = None
        self.scene.update()
        self.hint.setText("Release on the target symbol.")
        return True

    def _connector_press(self, sp) -> None:
        if self._conn_first is None:
            target = self._target_at(sp)
            if target is not None:
                port = self._port_of(target, sp)
                if port is not None:      # snap to the port dot
                    fx, fy = self._item_ports(target)[port]
                    anchor = (fx, fy, port)
                else:
                    anchor = _edge_anchor(target, sp)
                self._conn_first = (target, anchor)
            else:
                line = self._line_at(sp)
                if line is None:
                    self.hint.setText("Press on a symbol or a line to "
                                      "start; Esc stops drawing.")
                    return
                # a branch can start on an existing line
                self._conn_first = (line,
                                    (self._line_param(line, sp), 0.0,
                                     "LINE"))
            self._conn_pressed = True
            self.hint.setText("Drag to the target and release - "
                              "or click it.")
            return
        self._connector_finish(sp)

    def _connector_click(self, sp) -> None:
        """Two-click form, also used by scripts: arm, then complete."""
        if self._conn_first is None:
            self._connector_press(sp)
            self._conn_pressed = False
        else:
            self._connector_finish(sp)

    def _line_at(self, sp):
        for it in self.scene.items(sp):
            if isinstance(it, Connector) and hasattr(it, "_item_id"):
                return it
        return None

    @staticmethod
    def _line_param(conn, sp) -> float:
        """0..1 arc-length position of sp projected onto the line."""
        pts = conn._pts
        total = sum(math.hypot(b.x() - a.x(), b.y() - a.y())
                    for a, b in zip(pts, pts[1:])) or 1.0
        return max(0.0, min(1.0, _arc_t(pts, sp) / total))

    @staticmethod
    def _preview_route(p1, ah, p2, bh) -> List[QPointF]:
        """The same orthogonal rules the Connector routes by, so what
        you see while pulling is what you get on release."""
        if ah and bh:
            mx = (p1.x() + p2.x()) / 2
            return [p1, QPointF(mx, p1.y()), QPointF(mx, p2.y()), p2]
        if not ah and not bh:
            my = (p1.y() + p2.y()) / 2
            return [p1, QPointF(p1.x(), my), QPointF(p2.x(), my), p2]
        if ah:
            return [p1, QPointF(p2.x(), p1.y()), p2]
        return [p1, QPointF(p1.x(), p2.y()), p2]

    def _connector_track(self, sp) -> None:
        a_it, (ax, ay, side) = self._conn_first
        if side == "LINE":
            p1, seg_h = Connector._line_point(a_it, ax)
            ah = not seg_h
        else:
            w = getattr(a_it, "_w", 60.0)
            h = getattr(a_it, "_h", 60.0)
            p1 = a_it.mapToScene(QPointF(-w / 2 + ax * w,
                                         -h / 2 + ay * h))
            ah = Connector._eff_side(side, a_it) in "LR"
        target = self._target_at(sp)
        if target is a_it:
            target = None
        self.scene.conn_hover = target
        self.scene.conn_hover_pt = None
        p2, bh = sp, not ah
        if target is not None:
            # show exactly where the line would land on the target
            bx, by, b_side = _edge_anchor(target, sp)
            w = getattr(target, "_w", 60.0)
            h = getattr(target, "_h", 60.0)
            p2 = target.mapToScene(
                QPointF(-w / 2 + bx * w, -h / 2 + by * h))
            bh = Connector._eff_side(b_side, target) in "LR"
            self.scene.conn_hover_pt = p2
        else:
            line = self._line_at(sp)
            if line is not None and line is not a_it:
                p2 = _nearest_on(line._pts, sp)
                t = self._line_param(line, sp)
                _pt, seg_h2 = Connector._line_point(line, t)
                bh = not seg_h2
                self.scene.conn_hover_pt = p2
        self.scene.conn_preview = self._preview_route(p1, ah, p2, bh)
        self.scene.update()

    def _connector_release(self, sp) -> None:
        self._conn_pressed = False
        self.scene.conn_preview = None
        self.scene.conn_hover = None
        self.scene.conn_hover_pt = None
        self.scene.update()
        src = self._conn_first[0]
        target = self._target_at(sp)
        line = self._line_at(sp)
        if (target is not None and target is not src) \
                or (target is None and line is not None
                    and line is not src):
            self._connector_finish(sp)
        elif self._smart_gesture:
            self._cancel_pending()    # a port click without a target
        else:
            self.hint.setText("Now click the target symbol or line.")

    def _connector_finish(self, sp) -> None:
        a_it, (ax, ay, a_side) = self._conn_first
        target = self._target_at(sp)
        if target is a_it:
            return
        props = {"from_id": a_it._item_id}
        if a_side == "LINE":
            props["at"] = ax
        else:
            props.update({"ax": ax, "ay": ay, "a_side": a_side})
        if target is not None:
            bx, by, b_side = _edge_anchor(target, sp)
            props.update({"to_id": target._item_id, "bx": bx,
                          "by": by, "b_side": b_side})
        else:
            line = self._line_at(sp)
            if line is None or line is a_it:
                return
            props.update({"to_id": line._item_id,
                          "bt": self._line_param(line, sp)})
        rec = self._new_rec("connector", 0.0, 0.0, props)
        self.undo.push(AddCmd(self, [rec], text="Connect"))
        if self._smart_gesture:
            self._cancel_pending()    # a one-shot drag from a port
        else:
            # the tool stays armed for the next connection, the way
            # Visio and draw.io keep it
            self._conn_first = None
            self._conn_pressed = False
            self.scene.conn_preview = None
            self.scene.conn_hover = None
            self.scene.conn_hover_pt = None
            self.scene.update()
            self.hint.setText("Connected. Draw another, or press Esc "
                              "to stop.")

    # ------------------------------------------------------ connector bends
    def _sel_connector(self):
        for it in self.scene.selectedItems():
            if isinstance(it, Connector) and hasattr(it, "_item_id"):
                return it
        return None

    def _bend_press(self, sp) -> bool:
        conn = self._sel_connector()
        if conn is None:
            return False
        for idx, bp in enumerate(conn.bends):
            if abs(bp.x() - sp.x()) <= 8 and abs(bp.y() - sp.y()) <= 8:
                rec = self._recs.get(conn._item_id)
                if rec is None:
                    return False
                orig = [list(b) for b in rec["props"].get("bends", [])]
                self._bend_drag = {"id": conn._item_id, "idx": idx,
                                   "orig": orig,
                                   "cur": [list(b) for b in orig]}
                return True
        return False

    def _bend_move(self, sp, mods) -> None:
        d = self._bend_drag
        conn = self._ids.get(d["id"])
        if conn is None:
            self._bend_drag = None
            return
        x, y = sp.x(), sp.y()
        if not (mods & Qt.AltModifier):
            x = round(x / GRID) * GRID
            y = round(y / GRID) * GRID
        d["cur"][d["idx"]] = [x, y]
        conn.set_bends(d["cur"])
        self.scene.update()

    def _bend_release(self) -> None:
        d, self._bend_drag = self._bend_drag, None
        if d is None or d["cur"] == d["orig"]:
            return
        self.undo.push(EditCmd(self, d["id"],
                               {"props": {"bends": d["orig"]}},
                               {"props": {"bends": d["cur"]}},
                               text="Move bend"))

    # ---------------------------------------------------- segment sliding
    @staticmethod
    def _seg_dist(a, b, q) -> float:
        abx, aby = b.x() - a.x(), b.y() - a.y()
        l2 = abx * abx + aby * aby
        if l2 < 1e-9:
            return math.hypot(q.x() - a.x(), q.y() - a.y())
        t = max(0.0, min(1.0, ((q.x() - a.x()) * abx
                               + (q.y() - a.y()) * aby) / l2))
        return math.hypot(q.x() - (a.x() + abx * t),
                          q.y() - (a.y() + aby * t))

    def _seg_press(self, sp) -> bool:
        """Grab a middle leg of the selected line and slide it
        perpendicular, the way Visio and AutoCAD move segments."""
        conn = self._sel_connector()
        if conn is None or not conn.ortho:
            return False
        for bp in conn.bends:          # bend diamonds take precedence
            if math.hypot(bp.x() - sp.x(), bp.y() - sp.y()) <= 10:
                return False
        pts = conn._pts
        rec = self._recs.get(conn._item_id)
        if rec is None:
            return False
        best = None
        for k, (a, b) in enumerate(zip(pts, pts[1:])):
            dist = self._seg_dist(a, b, sp)
            if dist > 6.0:
                continue
            if len(pts) == 2:
                mode = "jog"           # a straight line grows a jog
            else:
                mode = "slide"         # any leg slides; the anchor
                #                        stub regenerates as needed
            if math.hypot(b.x() - a.x(), b.y() - a.y()) < 1e-6:
                # a collapsed leg: perpendicular to its neighbour
                pa, pb = pts[k - 1], pts[k]
                horiz = abs(pb.y() - pa.y()) > abs(pb.x() - pa.x())
            else:
                horiz = abs(b.y() - a.y()) <= abs(b.x() - a.x())
            interior = 1 <= k <= len(pts) - 3
            key = (round(dist, 3), 0 if interior else 1)
            if best is None or key < best[0]:
                best = (key, k, horiz, mode)
        if best is None:
            return False
        _key, k, horiz, mode = best
        self._seg_drag = {
            "id": conn._item_id, "k": k, "horiz": horiz, "mode": mode,
            "orig": [list(bd) for bd in rec["props"].get("bends", [])],
            "base": [[p.x(), p.y()] for p in pts],
            "cur": None}
        return True

    def _seg_move(self, sp, mods) -> None:
        d = self._seg_drag
        conn = self._ids.get(d["id"])
        if not isinstance(conn, Connector):
            self._seg_drag = None
            return
        v = sp.y() if d["horiz"] else sp.x()
        if not (mods & Qt.AltModifier):
            v = round(v / GRID) * GRID
        base, k = d["base"], d["k"]
        if d["mode"] == "jog":
            mx = (base[0][0] + base[1][0]) / 2.0
            my = (base[0][1] + base[1][1]) / 2.0
            nb = [[mx, v]] if d["horiz"] else [[v, my]]
        else:
            # materialize the interior points as bends, then move the
            # two that bound the grabbed leg
            nb = [list(p) for p in base[1:-1]]
            for idx in (k - 1, k):
                if 0 <= idx < len(nb):
                    nb[idx][1 if d["horiz"] else 0] = v
        d["cur"] = nb
        conn.set_bends(nb)
        self.scene.update()

    def _seg_release(self) -> None:
        d, self._seg_drag = self._seg_drag, None
        if d is None or d["cur"] is None or d["cur"] == d["orig"]:
            return
        self.undo.push(EditCmd(self, d["id"],
                               {"props": {"bends": d["orig"]}},
                               {"props": {"bends": d["cur"]}},
                               text="Slide segment"))

    def _bend_dblclick(self, sp) -> bool:
        conn = next((it for it in self.scene.items(sp)
                     if isinstance(it, Connector)
                     and hasattr(it, "_item_id")), None)
        if conn is None:
            return False
        rec = self._recs.get(conn._item_id)
        if rec is None:
            return False
        bends = [list(b) for b in rec["props"].get("bends", [])]
        for idx, b in enumerate(bends):
            if abs(b[0] - sp.x()) <= 9 and abs(b[1] - sp.y()) <= 9:
                nb = bends[:idx] + bends[idx + 1:]
                self.undo.push(EditCmd(self, conn._item_id,
                                       {"props": {"bends": bends}},
                                       {"props": {"bends": nb}},
                                       text="Remove bend"))
                return True
        x = round(sp.x() / GRID) * GRID
        y = round(sp.y() / GRID) * GRID
        t_new = _arc_t(conn._pts, sp)
        ts = [_arc_t(conn._pts, QPointF(b[0], b[1])) for b in bends]
        idx = sum(1 for t in ts if t <= t_new)
        nb = bends[:idx] + [[x, y]] + bends[idx:]
        self.undo.push(EditCmd(self, conn._item_id,
                               {"props": {"bends": bends}},
                               {"props": {"bends": nb}}, text="Add bend"))
        return True

    def _conn_align(self) -> None:
        """Move each selected connector's target symbol so both anchors
        share an axis - the line becomes dead straight."""
        moves = []
        for i in self._selected_ids():
            rec = self._recs.get(i)
            if rec is None or rec["type"] != "connector":
                continue
            conn = self._ids.get(i)
            if not isinstance(conn, Connector) or conn.b_is_line \
                    or conn.a_is_line:
                continue
            p1 = conn._endpoint(conn.a_item, conn.a_anchor)
            p2 = conn._endpoint(conn.b_item, conn.b_anchor)
            ah = conn._eff_side(conn.a_side, conn.a_item) in "LR"
            bh = conn._eff_side(conn.b_side, conn.b_item) in "LR"
            if ah and bh:
                d = (0.0, p1.y() - p2.y())
            elif not ah and not bh:
                d = (p1.x() - p2.x(), 0.0)
            else:
                continue                # a corner route has no shared axis
            if abs(d[0]) < 1e-6 and abs(d[1]) < 1e-6:
                continue
            hid = rec["props"].get("to_id")
            hrec = self._recs.get(hid)
            if hrec is None:
                continue
            old = (hrec["x"], hrec["y"])
            moves.append((hid, old, (old[0] + d[0], old[1] + d[1])))
        if moves:
            self.undo.push(MoveCmd(self, moves, text="Align line"))

    def _conn_straighten(self) -> None:
        self._conn_route_cmd(False)

    def _conn_reset(self) -> None:
        self._conn_route_cmd(True)

    def _conn_route_cmd(self, reset_anchors: bool) -> None:
        mids = {"L": (0.0, 0.5), "R": (1.0, 0.5),
                "T": (0.5, 0.0), "B": (0.5, 1.0)}
        in_macro = False
        for i in self._selected_ids():
            rec = self._recs.get(i)
            if rec is None or rec["type"] != "connector":
                continue
            pr = rec["props"]
            conn = self._ids.get(i)
            o, n = {}, {}
            if pr.get("bends"):
                o["bends"] = [list(b) for b in pr["bends"]]
                n["bends"] = []
            if reset_anchors and isinstance(conn, Connector):
                ends = []
                if not conn.a_is_line:
                    ends.append(("a", conn.a_item))
                if not conn.b_is_line:
                    ends.append(("b", conn.b_item))
                for pre, host in ends:
                    side = pr.get(f"{pre}_side", "R")
                    # heal to the symbol's declared port if it has one
                    ports = getattr(host, "ports", None) or {}
                    mx, my = ports.get(side,
                                       mids.get(side, (0.5, 0.5)))
                    if (pr.get(f"{pre}x") != mx
                            or pr.get(f"{pre}y") != my):
                        o[f"{pre}x"] = pr.get(f"{pre}x")
                        o[f"{pre}y"] = pr.get(f"{pre}y")
                        n[f"{pre}x"], n[f"{pre}y"] = mx, my
            if n:
                if not in_macro:
                    self.undo.beginMacro("Re-route")
                    in_macro = True
                self.undo.push(EditCmd(self, i, {"props": o},
                                       {"props": n}, text="Re-route"))
        if in_macro:
            self.undo.endMacro()

    def _finish_pipe(self) -> None:
        pts = self._pipe_pts
        if len(pts) >= 2 and pts[-1] == pts[-2]:
            pts.pop()
        if len(pts) >= 2:
            x0, y0 = pts[0]
            rel = [[px - x0, py - y0] for px, py in pts]
            self._place("pipe", x0, y0, {"pts": rel})
        else:
            self._cancel_pending()

    def _cancel_pending(self) -> None:
        was_armed = self._pending is not None
        self._pending = None
        self._pipe_pts = []
        self._conn_first = None
        self._conn_pressed = False
        self._smart_gesture = False
        self.act_conn.setChecked(False)
        self.act_pipe.setChecked(False)
        self.view.viewport().unsetCursor()
        if was_armed:                 # give the selection its grips back
            self._sel_changed()
        if self.scene.conn_preview is not None \
                or self.scene.conn_hover is not None \
                or self.scene.conn_hover_pt is not None:
            self.scene.conn_preview = None
            self.scene.conn_hover = None
            self.scene.conn_hover_pt = None
            self.scene.update()
        self.palette.clearSelection()
        self.hint.setText("Pick a symbol and click the canvas - "
                          "right-click anything for actions.")

    def _delete_selected(self) -> None:
        ids = [it._item_id for it in self.scene.selectedItems()
               if hasattr(it, "_item_id")]
        if ids:
            self.undo.push(RemoveCmd(self, ids))
        self._queue_form(None)

    def _set_title(self, text: str) -> None:
        self._title = text.strip() or "USER DISPLAY"
        self._sync_title()

    def _resize_canvas(self, axis: int, value: float) -> None:
        r = self.scene.sceneRect()
        w = float(value) if axis == 0 else r.width()
        h = float(value) if axis == 1 else r.height()
        self.scene.setSceneRect(QRectF(0, 0, w, h))

    # ----------------------------------------------------------- properties
    def _sel_changed(self) -> None:
        try:
            items = [i for i in self.scene.selectedItems()
                     if hasattr(i, "_spec_key")]
        except RuntimeError:      # scene already gone at teardown
            return
        current = [i._item_id for i in items if hasattr(i, "_item_id")]
        self._sel_order = ([i for i in self._sel_order if i in current]
                           + [i for i in current
                              if i not in self._sel_order])
        one = items[0] if len(items) == 1 else None
        rec = (self._recs.get(getattr(one, "_item_id", None))
               if one is not None else None)
        if getattr(self, "sel_lbl", None) is not None:
            self.sel_lbl.setText(
                f"{len(current)} selected" if current
                else f"{len(self._recs)} objects")
        self._sync_overlay(one, rec)
        self._queue_form(one)
        if getattr(self, "obj_tree", None) is not None \
                and not self._syncing_objects:
            self._objects_dirty()

    def _queue_form(self, it) -> None:
        """Rebuild the form on the next event-loop turn: a commit can
        arrive from a form widget's own signal, and rebuilding there
        would delete the emitting widget mid-signal."""
        self._form_item = it
        if not self._form_queued:
            self._form_queued = True
            QTimer.singleShot(0, self._deferred_form)

    def _deferred_form(self) -> None:
        self._form_queued = False
        self._build_form(self._form_item)

    def _resync_form(self, _index=None) -> None:
        """Keep the form current after undo/redo from the canvas."""
        if not self._loading and not self._dirty:
            self._sel_changed()

    def _sync_overlay(self, one, rec) -> None:
        if (rec is not None and isinstance(one, HmiItem)
                and rec["type"] != "connector"):
            self.overlay.set_target(one, set(SIZING.get(rec["type"], {})),
                                    _keeps_aspect(rec))
        else:
            self.overlay.set_target(None, set(), True)

    def _build_form(self, it) -> None:
        """The live property form: every edit commits one undo command
        (line edits on editingFinished, spinboxes after a 400 ms pause,
        checks and choices immediately). No Apply button."""
        self._flush_dirty()          # commit what the last item had pending
        self._loading = True
        while self.form.rowCount():
            self.form.removeRow(0)
        self._editors = {}
        self._edited = it
        self._edited_id = getattr(it, "_item_id", None)
        rec = (self._recs.get(self._edited_id)
               if self._edited_id is not None else None)
        if rec is None:
            # nothing selected: the sheet's own settings, like the
            # reference tool's Format panel
            self.form.addRow(QLabel("<b>Sheet</b>"))
            t = QLineEdit(self._title)
            t.editingFinished.connect(
                lambda w=t: self._set_title(w.text()))
            self.form.addRow("Title", t)
            r = self.scene.sceneRect()
            for cap, axis, val in (("Width", 0, r.width()),
                                   ("Height", 1, r.height())):
                sp = QSpinBox()
                sp.setRange(400, 6000)
                sp.setButtonSymbols(QAbstractSpinBox.NoButtons)
                sp.setValue(int(val))
                sp.valueChanged.connect(
                    lambda v, a=axis: self._resize_canvas(a, v))
                self.form.addRow(cap, sp)
            cb = QPushButton(self.scene.backgroundBrush().color().name())
            cb.setFixedHeight(24)

            def _restyle_sheet_btn():
                v = self.scene.backgroundBrush().color()
                cb.setText(v.name())
                cb.setStyleSheet(
                    f"background: {v.name()}; border: 1px solid #B9BDC4;"
                    f" border-radius: 4px; color: "
                    f"{'#FFFFFF' if v.lightness() < 128 else '#1A1C1E'};")

            def _pick_sheet(_=False):
                c = QColorDialog.getColor(
                    self.scene.backgroundBrush().color(), self,
                    "Sheet colour")
                if c.isValid():
                    self.scene.setBackgroundBrush(c)
                    self.scene.update()
                    _restyle_sheet_btn()

            _restyle_sheet_btn()
            cb.clicked.connect(_pick_sheet)
            self.form.addRow("Colour", cb)
            note = QLabel("Select an item to edit\nits properties.")
            note.setWordWrap(True)
            self.form.addRow(note)
            self._loading = False
            return
        spec = SPECS[rec["type"]]
        self.form.addRow(QLabel(f"<b>{spec.label}</b>"))
        if rec["type"] != "connector":
            self._add_editor("__x", "X", "float", rec["x"])
            self._add_editor("__y", "Y", "float", rec["y"])
            self._add_editor("__rot", "Rotation (deg)", "float",
                             rec.get("rot", 0.0))
            self._add_editor("__flip", "Mirror", "bool",
                             rec.get("flip", False))
            if rec["type"] in SIZING and len(SIZING[rec["type"]]) == 2:
                self._add_editor("__aspect", "Keep aspect ratio", "bool",
                                 _keeps_aspect(rec))
        for name, caption, kind, default in spec.props:
            if kind == "hidden":
                continue
            self._add_editor(name, caption, kind,
                             rec["props"].get(name, default))
        self._loading = False

    def _add_editor(self, name, caption, kind, val) -> None:
        if kind in ("tag", "module", "text"):
            w = QLineEdit(str(val))
            names = (self._tag_names if kind == "tag"
                     else self._module_names if kind == "module" else [])
            if names:
                comp = QCompleter(names, w)
                comp.setCaseSensitivity(Qt.CaseInsensitive)
                w.setCompleter(comp)
            w.editingFinished.connect(
                lambda n=name: self._field_changed(n, immediate=True))
        elif kind == "float":
            w = QDoubleSpinBox()
            w.setRange(-100000.0, 100000.0)
            w.setDecimals(1)
            w.setButtonSymbols(QAbstractSpinBox.NoButtons)
            w.setValue(float(val))
            w.valueChanged.connect(lambda _v, n=name: self._field_changed(n))
            w.editingFinished.connect(self._flush_dirty)
        elif kind == "int":
            w = QSpinBox()
            w.setRange(0, 10000)
            w.setButtonSymbols(QAbstractSpinBox.NoButtons)
            w.setValue(int(val))
            w.valueChanged.connect(lambda _v, n=name: self._field_changed(n))
            w.editingFinished.connect(self._flush_dirty)
        elif kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(val))
            w.toggled.connect(
                lambda _c, n=name: self._field_changed(n, immediate=True))
        elif kind.startswith("choice:"):
            w = QComboBox()
            w.addItems(kind.split(":", 1)[1].split("|"))
            w.setCurrentText(str(val))
            w.currentTextChanged.connect(
                lambda _t, n=name: self._field_changed(n, immediate=True))
        elif kind == "color":
            w = self._color_button(name, str(val))
        else:
            return
        self._editors[name] = (kind, w)
        self.form.addRow(caption, w)

    def _color_button(self, name: str, val: str) -> QPushButton:
        w = QPushButton()
        w.setFixedHeight(24)
        w._value = val
        w.setToolTip("Click to pick a colour; right-click resets to "
                     "the library style")

        def refresh():
            v = w._value
            w.setText(v or "Library")
            w.setStyleSheet(
                f"background: {v}; border: 1px solid #B9BDC4; "
                f"border-radius: 4px; color: "
                f"{'#FFFFFF' if QColor(v).lightness() < 128 else '#1A1C1E'};"
                if v else "")

        from PySide6.QtGui import QColor
        refresh()

        def pick(_=False):
            c = QColorDialog.getColor(
                QColor(w._value) if w._value else QColor("#24407C"),
                self, "Pick colour")
            if c.isValid():
                w._value = c.name()
                refresh()
                self._field_changed(name, immediate=True)

        def reset(_pos):
            if w._value:
                w._value = ""
                refresh()
                self._field_changed(name, immediate=True)

        w.clicked.connect(pick)
        w.setContextMenuPolicy(Qt.CustomContextMenu)
        w.customContextMenuRequested.connect(reset)
        return w

    def _field_changed(self, name, immediate: bool = False) -> None:
        if self._loading or self._edited_id is None:
            return
        self._dirty.add(name)
        if immediate:
            self._flush_dirty()
        else:
            self._debounce.start(400)

    @staticmethod
    def _editor_value(kind, w):
        if kind in ("tag", "module", "text"):
            return w.text().strip()
        if kind == "float":
            return float(w.value())
        if kind == "int":
            return int(w.value())
        if kind == "bool":
            return bool(w.isChecked())
        if kind == "color":
            return w._value
        return w.currentText()

    def _flush_dirty(self) -> None:
        self._debounce.stop()
        names, self._dirty = self._dirty, set()
        if not names or self._edited_id is None:
            return
        rec = self._recs.get(self._edited_id)
        if rec is None:
            return
        o, n, opr, npr = {}, {}, {}, {}
        for name in names:
            kind, w = self._editors.get(name, (None, None))
            if w is None:
                continue
            v = self._editor_value(kind, w)
            if name == "__x":
                if v != rec["x"]:
                    o["x"], n["x"] = rec["x"], v
                    o.setdefault("y", rec["y"])
                    n.setdefault("y", rec["y"])
            elif name == "__y":
                if v != rec["y"]:
                    o["y"], n["y"] = rec["y"], v
                    o.setdefault("x", rec["x"])
                    n.setdefault("x", rec["x"])
            elif name == "__rot":
                v = float(v) % 360.0
                if abs(v - rec.get("rot", 0.0)) > 1e-9:
                    o["rot"], n["rot"] = rec.get("rot", 0.0), v
            elif name == "__flip":
                if bool(v) != bool(rec.get("flip", False)):
                    o["flip"] = rec.get("flip", False)
                    n["flip"] = bool(v)
            elif name == "__aspect":
                if bool(v) != _keeps_aspect(rec):
                    o["lock_aspect"] = rec.get("lock_aspect")
                    n["lock_aspect"] = bool(v)
            elif rec["props"].get(name) != v:
                opr[name] = rec["props"].get(name)
                npr[name] = v
        if opr:
            o["props"], n["props"] = opr, npr
        if n:
            self.undo.push(EditCmd(self, self._edited_id, o, n))

    # -------------------------------------------------------------- export
    def _export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PNG", str(DISPLAY_DIR / "display.png"),
            "PNG image (*.png)")
        if path:
            export_png(self.scene, path)
            self.hint.setText(f"Exported {Path(path).name}")

    def _export_svg(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export SVG", str(DISPLAY_DIR / "display.svg"),
            "SVG image (*.svg)")
        if path:
            export_svg(self.scene, path)
            self.hint.setText(f"Exported {Path(path).name}")

    # ------------------------------------------- built-in overview import
    def _import_overview(self) -> None:
        """The built-in plant overview as an editable page: the live
        scene is rebuilt and every item converted to a builder record,
        so the import can never go stale against the code."""
        try:
            src = build_overview(self.ctx.controller, self.ctx.alarms)
        except Exception:
            log.exception("Could not build the overview to import")
            QMessageBox.warning(self, "Import failed",
                                "The built-in overview could not be "
                                "built; see the log.")
            return
        if not self._maybe_discard():
            return
        self._clear()
        self.undo.clear()
        self._path = None
        self._set_title("PLANT OVERVIEW")
        r = src.sceneRect()
        self.scene.setSceneRect(QRectF(0, 0, r.width(), r.height()))
        count = skipped = 0
        for it in reversed(src.items()):
            rec = self._builtin_rec(it)
            if rec is None:
                skipped += 1
                continue
            if self._restore(rec) is not None:
                count += 1
        self.undo.clear()
        self.undo.resetClean()        # imported but not yet saved
        self._queue_form(None)
        self.view.fit()
        self.hint.setText(f"Imported the built-in overview: {count} "
                          f"items ({skipped} decorative elements "
                          "skipped). Edit it, then Save as...")

    def _builtin_rec(self, it) -> Optional[dict]:
        x, y = it.pos().x(), it.pos().y()
        if isinstance(it, VBar):
            return self._new_rec("vbar", x, y, {
                "tag": it.tag_name, "unit": it.unit_txt,
                "lo": it.span[0], "hi": it.span[1],
                "height": it._h - 34.0, "decimals": it.dec})
        if isinstance(it, TrendTile):
            return self._new_rec("trend", x, y, {
                "ref": it.ref, "caption": it.caption,
                "unit": it.unit_txt, "mini": it.mini})
        if isinstance(it, MeasBox):
            return self._new_rec("meas", x, y, {
                "tag": it.tag_name, "unit": it.unit_txt,
                "decimals": it.dec})
        if isinstance(it, StateLamp):
            return self._new_rec("lamp", x, y, {
                "tag": it.tag_name, "caption": it.caption,
                "on_text": it.on_text, "off_text": it.off_text,
                "size": it._h - 30.0})
        if isinstance(it, PumpISA):
            return self._new_rec("pump", x, y, {
                "run_tag": it.run_tag, "pair": ",".join(it.pair),
                "size": it._w, "flip": it.flip})
        if isinstance(it, ValveISA):
            return self._new_rec("valve", x, y, {
                "tag": it.pos_tag, "vertical": it.vertical,
                "show_pct": it.show_pct})
        if isinstance(it, MovISA):
            return self._new_rec("mov", x, y, {"base": it.base})
        if isinstance(it, Junction):
            return self._new_rec("junction", x, y, {
                "text": it.text, "radius": it._w / 2.0})
        if isinstance(it, TextBox):
            return self._new_rec("textbox", x, y, {
                "text": it.text, "w": it._w, "h": it._h})
        if isinstance(it, Equip):
            return self._new_rec("equip", x, y, {
                "number": it.number, "name": it.name, "kind": it.kind,
                "w": it._w, "h": it._h, "level_tag": it.level_tag,
                "ref": it.ref, "outline": it.outline,
                "trays": it.trays})
        if isinstance(it, Flag):
            return self._new_rec("textbox", x, y, {
                "text": it.text, "w": it._w + 14.0, "h": 26.0})
        if isinstance(it, Label):
            return self._new_rec("label", x, y, {
                "text": it.text, "size": it.size, "bold": it.bold,
                "navy": it.colour == NAVY})
        if isinstance(it, Pipe):
            if not it.pts:
                return None
            px, py = it.pts[0].x(), it.pts[0].y()
            rel = [[p.x() - px, p.y() - py] for p in it.pts]
            return self._new_rec("pipe", px, py, {
                "pts": rel, "width": it.width,
                "arrow": it.arrow_at is not None,
                "navy": it.colour == NAVY,
                "color": "" if it.colour == NAVY
                else it.colour.name()})
        return None      # EsdBanner, signal paths, overlays

    # --------------------------------------------------- document lifecycle
    def _sync_title(self) -> None:
        try:
            dirty = "" if self.undo.isClean() else "*"
            where = self._path.name if self._path else "unsaved"
            self.setWindowTitle(f"{dirty}{self._title} - {where}"
                                "  ·  Display builder  ·  AzeoPlant")
        except RuntimeError:      # torn down while a signal was queued
            pass

    def _maybe_discard(self) -> bool:
        """True when it is safe to throw the current page away."""
        if self.undo.isClean():
            return True
        ans = QMessageBox.question(
            self, "Unsaved changes",
            f"{self._title} has unsaved changes.\n\n"
            "Save them before continuing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save)
        if ans == QMessageBox.Cancel:
            return False
        if ans == QMessageBox.Save:
            self._save()
            return self.undo.isClean()
        return True

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self._maybe_discard():
            self._clear_autosave()
            ev.accept()
        else:
            ev.ignore()

    # ------------------------------------------------- autosave & recovery
    def _write_autosave(self) -> None:
        if self.undo.isClean() or not self._recs:
            return
        try:
            AUTOSAVE.parent.mkdir(parents=True, exist_ok=True)
            data = self._to_json()
            data["__autosave_of"] = str(self._path) if self._path else ""
            AUTOSAVE.write_text(json.dumps(data, indent=2),
                                encoding="utf-8")
        except OSError:
            log.debug("Autosave failed", exc_info=True)

    def _clear_autosave(self) -> None:
        try:
            AUTOSAVE.unlink(missing_ok=True)
        except OSError:
            pass

    def offer_recovery(self) -> None:
        """Called once after construction: a leftover autosave means the
        last session ended without saving."""
        if not AUTOSAVE.is_file():
            return
        try:
            data = json.loads(AUTOSAVE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._clear_autosave()
            return
        of = data.get("__autosave_of") or ""
        name = Path(of).name if of else "an unsaved page"
        ans = QMessageBox.question(
            self, "Recover unsaved work",
            f"The builder closed with unsaved changes to {name}.\n\n"
            "Recover them?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ans != QMessageBox.Yes:
            self._clear_autosave()
            return
        self._load_data(data, Path(of) if of else None,
                        "Recovered unsaved work - save it now.")
        self.undo.resetClean()          # a recovery is unsaved by nature
        self._sync_title()

    # ------------------------------------------------------- recent pages
    @staticmethod
    def _recent_paths() -> List[str]:
        s = QSettings("AzeoPlant", "AzeoPlant Simulator")
        v = s.value("builder/recent", [])
        return [str(p) for p in (v or []) if Path(str(p)).is_file()]

    def _remember_recent(self, path: Path) -> None:
        keep = [str(path)] + [p for p in self._recent_paths()
                              if p != str(path)]
        s = QSettings("AzeoPlant", "AzeoPlant Simulator")
        s.setValue("builder/recent", keep[:RECENT_MAX])

    def _fill_recent(self) -> None:
        self.menu_recent.clear()
        paths = self._recent_paths()
        if not paths:
            a = self.menu_recent.addAction("(nothing yet)")
            a.setEnabled(False)
            return
        for p in paths:
            a = self.menu_recent.addAction(Path(p).name)
            a.setToolTip(p)
            a.triggered.connect(
                lambda _c=False, q=p: self._open_path(Path(q)))

    # -------------------------------------------------------- objects panel
    def _objects_dirty(self) -> None:
        if not self._obj_timer.isActive():
            self._obj_timer.start(60)

    @staticmethod
    def _rec_label(rec: dict) -> str:
        pr = rec["props"]
        for key in ("tag", "module", "ref", "run_tag", "base", "number",
                    "text", "caption", "level_tag"):
            v = pr.get(key)
            if v:
                return str(v)
        if rec["type"] == "image":
            return Path(str(pr.get("path", ""))).stem or "image"
        if rec["type"] == "connector":
            return f"{pr.get('from_id')} → {pr.get('to_id')}"
        return ""

    def _fill_objects(self) -> None:
        if not hasattr(self, "obj_tree"):
            return
        needle = self.obj_search.text().strip().lower()
        self._syncing_objects = True
        self.obj_tree.clear()
        sel = set(self._selected_ids())
        for rec in sorted(self._recs.values(), key=lambda r: r["id"]):
            spec = SPECS.get(rec["type"])
            kind = spec.label.split("(")[0].strip() if spec else rec["type"]
            label = self._rec_label(rec)
            if needle and needle not in kind.lower() \
                    and needle not in label.lower():
                continue
            it = QTreeWidgetItem([kind, label])
            it.setData(0, Qt.UserRole, rec["id"])
            self.obj_tree.addTopLevelItem(it)
            if rec["id"] in sel:
                it.setSelected(True)
        self.obj_tree.resizeColumnToContents(0)
        self._syncing_objects = False
        self.dock_obj.setWindowTitle(
            f"Objects ({len(self._recs)})")

    def _objects_picked(self) -> None:
        if self._syncing_objects:
            return
        ids = {it.data(0, Qt.UserRole)
               for it in self.obj_tree.selectedItems()}
        self.scene.clearSelection()
        for i in ids:
            it = self._ids.get(i)
            if it is not None:
                it.setSelected(True)

    def _objects_reveal(self, item, _col) -> None:
        it = self._ids.get(item.data(0, Qt.UserRole))
        if it is not None:
            self.view.centerOn(it)

    # ------------------------------------------------------ drawing checks
    def _run_checks(self) -> None:
        db = self.ctx.db
        loops = getattr(self.ctx.controller, "loops", {}) or {}
        sheet = self.scene.sceneRect()
        issues: List[Tuple[str, Optional[int], str]] = []
        for rec in sorted(self._recs.values(), key=lambda r: r["id"]):
            spec = SPECS.get(rec["type"])
            if spec is None:
                continue
            label = self._rec_label(rec) or spec.label
            for name, _cap, kind, _d in spec.props:
                v = rec["props"].get(name)
                if kind == "tag":
                    if not v:
                        if rec["type"] in ("vbar", "meas", "trend",
                                           "lamp"):
                            issues.append(
                                ("advisory", rec["id"],
                                 f"{spec.label}: no tag configured"))
                    elif v not in db and v not in loops:
                        issues.append(
                            ("error", rec["id"],
                             f"{label}: tag '{v}' is not in the "
                             "database"))
                elif kind == "module" and v and v not in loops:
                    issues.append(
                        ("error", rec["id"],
                         f"{label}: module '{v}' is not a loop"))
            if rec["type"] == "image":
                p = str(rec["props"].get("path", ""))
                if not p or not Path(_resolve_path(p)).is_file():
                    issues.append(("error", rec["id"],
                                   f"{label}: image file is missing"))
            if rec["type"] == "connector":
                for end in ("from_id", "to_id"):
                    if rec["props"].get(end) not in self._recs:
                        issues.append(
                            ("error", rec["id"],
                             f"{label}: {end} endpoint no longer exists"))
            it = self._ids.get(rec["id"])
            if it is not None and rec["type"] not in ("connector",):
                if not sheet.intersects(it.sceneBoundingRect()):
                    issues.append(("advisory", rec["id"],
                                   f"{label}: sits outside the sheet"))
        self.check_list.clear()
        if not issues:
            self.check_list.addItem(
                f"No issues found - {len(self._recs)} objects checked.")
        else:
            order = {"error": 0, "advisory": 1}
            for sev, item_id, text in sorted(
                    issues, key=lambda i: order.get(i[0], 2)):
                mark = "ERROR" if sev == "error" else "advisory"
                li = QListWidgetItem(f"[{mark}]  {text}")
                li.setForeground(theme.ALARM_CRITICAL if sev == "error"
                                 else theme.MUTED_TEXT)
                li.setData(Qt.UserRole, item_id)
                self.check_list.addItem(li)
        errs = sum(1 for i in issues if i[0] == "error")
        self.dock_chk.setWindowTitle(
            f"Drawing checks ({errs} errors, "
            f"{len(issues) - errs} advisories)")
        self.dock_chk.show()
        self.dock_chk.raise_()
        self.resizeDocks([self.dock_chk], [150], Qt.Vertical)
        self.hint.setText(f"Checked {len(self._recs)} objects: "
                          f"{errs} errors, {len(issues) - errs} "
                          "advisories.")

    def _check_reveal(self, item) -> None:
        it = self._ids.get(item.data(Qt.UserRole))
        if it is None:
            return
        self.scene.clearSelection()
        it.setSelected(True)
        self.view.centerOn(it)

    # ------------------------------------------------------------ save/load
    def _to_json(self) -> dict:
        items = []
        for rec in sorted(self._recs.values(), key=lambda r: r["id"]):
            out = {"type": rec["type"], "id": rec["id"],
                   "x": rec["x"], "y": rec["y"], "props": rec["props"]}
            if rec.get("rot"):
                out["rot"] = rec["rot"]
            if rec.get("flip"):
                out["flip"] = rec["flip"]
            if rec.get("z") is not None:
                out["z"] = rec["z"]
            if rec.get("lock_aspect") is not None:
                out["lock_aspect"] = rec["lock_aspect"]
            items.append(out)
        r = self.scene.sceneRect()
        out = {"title": self._title,
               "size": [r.width(), r.height()], "items": items}
        bg = self.scene.backgroundBrush().color().name()
        if bg.lower() != CANVAS.name().lower():
            out["background"] = bg
        return out

    def _save(self) -> None:
        if self._path is None:
            self._save_as()
            return
        try:
            self._path.write_text(json.dumps(self._to_json(), indent=2),
                                  encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        self.undo.setClean()
        self._clear_autosave()
        self._remember_recent(self._path)
        self._sync_title()
        self.hint.setText(f"Saved {self._path.name}")

    def _save_as(self) -> None:
        DISPLAY_DIR.mkdir(parents=True, exist_ok=True)
        start = self._path or (DISPLAY_DIR / "my_display.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save display", str(start), "Displays (*.json)")
        if not path:
            return
        self._path = Path(path)
        self._save()

    def _open(self) -> None:
        DISPLAY_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open display", str(DISPLAY_DIR), "Displays (*.json)")
        if path:
            self._open_path(Path(path))

    def _open_path(self, path: Path) -> None:
        if self._maybe_discard():
            self._load(path)

    def _load(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Open failed", str(exc))
            return
        self._load_data(data, path, f"Opened {path.name}")
        self._remember_recent(path)

    def _load_data(self, data: dict, path: Optional[Path],
                   message: str) -> None:
        self._clear()
        self._path = path
        self._set_title(str(data.get("title")
                            or (path.stem.upper() if path
                                else "USER DISPLAY")))
        w, h = (data.get("size") or [1560, 900])[:2]
        self.scene.setSceneRect(QRectF(0, 0, float(w), float(h)))
        self.scene.setBackgroundBrush(
            QColor(data["background"]) if data.get("background")
            else CANVAS)
        recs = []
        for rec in sorted(data.get("items", []), key=conn_rank):
            if rec.get("type") not in SPECS:
                log.warning("Unknown display item type %r skipped",
                            rec.get("type"))
                continue
            recs.append(self._new_rec(
                rec["type"], rec.get("x", 0.0), rec.get("y", 0.0),
                rec.get("props", {}), item_id=rec.get("id"),
                rot=rec.get("rot", 0.0), flip=rec.get("flip", False),
                z=rec.get("z"), lock_aspect=rec.get("lock_aspect")))
        self._restore_many(recs)
        self.undo.clear()
        self.undo.setClean()
        self._queue_form(None)
        self._sync_title()
        self.hint.setText(message)

    def _new(self) -> None:
        if not self._maybe_discard():
            return
        self._clear()
        self.undo.clear()
        self.undo.setClean()
        self._path = None
        self.scene.setBackgroundBrush(CANVAS)
        self._clear_autosave()
        self._set_title("USER DISPLAY")

    def _clear(self) -> None:
        for item_id in list(self._recs):
            self._delete_by_id(item_id)
        self._queue_form(None)

    # -------------------------------------------------------------- refresh
    def refresh(self, snap) -> None:
        self.scene.refresh(snap)


# ------------------------------------------------------------------- viewer
class UserDisplay(QWidget):
    """A saved builder page as a live operator display: title band,
    fitted graphic, every dynamo clickable through to its faceplate."""

    def __init__(self, path, db, controller=None, alarms=None,
                 open_ref=None, context_provider=None) -> None:
        super().__init__()
        self.scene = build_user_scene(path, db, controller, alarms)
        self.scene.on_open = open_ref
        self.scene.context_provider = context_provider
        wire_scene_signals(self.scene, controller)

        self.setWindowTitle(f"{self.scene.title}  ·  AzeoPlant")
        self.setStyleSheet(theme.STYLESHEET)
        self.resize(1200, 800)

        header = QLabel(self.scene.title)
        header.setAlignment(Qt.AlignCenter)
        header.setFixedHeight(30)
        header.setFont(theme.font(13, bold=True))
        header.setStyleSheet(
            f"background: {BAND.name()}; color: {TEXT.name()};"
            "border-bottom: 1px solid #C2C6CB;")

        view = _FitView()
        view.setRenderHint(QPainter.Antialiasing, True)
        view.setScene(self.scene)
        view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(view, 1)

    def refresh(self, snap) -> None:
        self.scene.refresh(snap)
