"""Project explorer-style project tree for the strategy designer.

Provides a Azeo Explorer-style hierarchical view of the project.  Two
sibling branches hang off the project root — the *logical* plant hierarchy
(areas and units) and the *physical* control network (nodes):

  Project (<plugin display name>)
    Area 1
      Units
        Unit 1
          Control Modules  — strategy .json files
        Unassigned         — modules with no unit assignment
      SFC Modules          — SFC .json files
      Equipment Modules    — equipment module definitions
    Area 2
      ...
    Control Network
      CTLR-<sim>           — modules assigned to that controller/node
      Unassigned           — modules with no node assignment

Areas, nodes and node assignments are stored in ``_project.json`` alongside
the strategy files.  Files remain in their original locations on disk; areas
and nodes are a logical overlay.  Building the tree never writes anything —
``_project.json`` is only rewritten by an explicit user action.

Node labels are the module's real name (the ``name`` field inside the module
JSON, falling back to the file stem verbatim) so tags such as ``FIC-101``
survive intact, and every node type carries its own vector icon rather than
an emoji baked into the label text.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import copy
import json
import logging
import math
import shutil
import uuid
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QMenu, QMessageBox, QPushButton,
    QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout,
    QWidget,
)

from azeo_control_trainer.core.strategy.serialization import strategy_io as _sio
from azeo_control_trainer.core.strategy.serialization.strategy_io import (
    list_strategy_folders, set_last_strategy,
)
from azeo_control_trainer.core.strategy.serialization.equipment_io import (
    list_equipment_modules, load_equipment_module, save_equipment_module,
    resolve_child_paths,
)


def _strategy_dir():
    """Current strategy directory.

    Read through the module rather than from-imported once: the launcher sets
    ``strategy_io.STRATEGY_DIR`` per plugin *after* import, so a bound copy
    stays pointing at the strategies root — which made the tree list
    ``strategies/<plugin>/…`` and grow a redundant folder named after the
    plugin under "Control Modules".
    """
    return _sio.STRATEGY_DIR

log = logging.getLogger("strategy.project_tree")

# ── Node type roles ──────────────────────────────────────────────────
# "project" | "area" | "units" | "unit" | "unassigned_unit" | "folder"
# | "strategy" | "equipment" | "em_child" | "network" | "node"
# | "unassigned"
_ROLE_NODE_TYPE   = Qt.UserRole + 1
_ROLE_FILE_PATH   = Qt.UserRole + 2   # Path to .json file (strategy nodes)
_ROLE_AREA_ID     = Qt.UserRole + 3   # Area id for area nodes & their children
# "cm" | "sfc" — module flavour behind the shared "strategy" node type.  The
# context menu used to infer this by comparing the parent folder item, which
# breaks as soon as the same module also appears under a network node.
_ROLE_MODULE_KIND = Qt.UserRole + 4
_ROLE_NODE_ID     = Qt.UserRole + 5   # Controller/node id for "node" items
_ROLE_UNIT_NAME   = Qt.UserRole + 6   # Unit name; "" means Unassigned
_ROLE_FOLDER_KIND = Qt.UserRole + 7   # "cm" | "sfc" | "equipment"

# Storage sub-directories that must not become tree levels — the parent
# category folder already conveys them.
_STORAGE_DIRS = frozenset({"control", "sequence", "equipment"})

# Node assignment: the id used when ``_project.json`` declares no nodes at
# all.  Everything runs in the one simulation runtime, so an unconfigured
# project shows every module under a single synthesized controller rather
# than pretending nothing is assigned.
_DEFAULT_NODE_ID = "node_default"


# ── Vector icons ─────────────────────────────────────────────────────
# Emoji prefixes baked into the label text made control modules and SFC
# modules indistinguishable and made the label un-copyable.  Each node type
# now gets a QPainter-drawn icon (same technique as the ribbon's icon
# factory) plus an optional runtime-state badge.

_ICON_SIZE = 16

# Tree kinds -> shared icon-set names (core/presentation/icon_set.py).
_ICON_SET_NAMES = {
    "project": "project", "area": "area", "folder": "folder", "cm": "module",
    "sfc": "sfc", "equipment": "equipment", "em_child": "em_child",
    "network": "network", "node": "node", "unassigned": "unassigned",
    "units": "folder", "unit": "area", "unassigned_unit": "unassigned",
}

_ICON_COLOR = {kind: UI.blue for kind in (
    'project', 'area', 'units', 'unit', 'unassigned_unit', 'folder', 'cm',
    'sfc', 'equipment', 'em_child', 'network', 'node', 'unassigned',
)}

# Runtime state → badge colour / label / text colour
_STATE_BADGE = {
    "online":   "#2D8E3C",
    "modified": "#B8962A",
    "offline":  "#9BADC6",
}
_STATE_TEXT = {
    "online":   "#2D8E3C",
    "modified": "#8A6F14",
    "offline":  "#1B2130",
}
_STATE_LABEL = {
    "online":   "On scan",
    "modified": "On scan — edited since download",
    "offline":  "Off scan",
}
_TEXT_DEFAULT = "#1B2130"

_ICON_CACHE: dict[tuple, QIcon] = {}


def _draw_project(p, s, c):
    p.setPen(QPen(c, 1.2))
    p.setBrush(QColor(c).lighter(175))
    p.drawRect(QRectF(s * 0.12, s * 0.52, s * 0.76, s * 0.34))
    p.setBrush(c)
    p.drawRect(QRectF(s * 0.24, s * 0.22, s * 0.13, s * 0.30))
    p.drawRect(QRectF(s * 0.54, s * 0.12, s * 0.13, s * 0.40))


def _draw_area(p, s, c):
    p.setPen(QPen(c, 1.1, Qt.DashLine))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.10, s * 0.14, s * 0.80, s * 0.72), 2, 2)
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawRect(QRectF(s * 0.28, s * 0.38, s * 0.16, s * 0.24))
    p.drawRect(QRectF(s * 0.56, s * 0.38, s * 0.16, s * 0.24))


def _draw_folder(p, s, c):
    p.setPen(QPen(c, 1.1))
    p.setBrush(QColor(c).lighter(175))
    path = QPainterPath()
    path.moveTo(s * 0.10, s * 0.82)
    path.lineTo(s * 0.10, s * 0.24)
    path.lineTo(s * 0.42, s * 0.24)
    path.lineTo(s * 0.50, s * 0.36)
    path.lineTo(s * 0.90, s * 0.36)
    path.lineTo(s * 0.90, s * 0.82)
    path.closeSubpath()
    p.drawPath(path)


def _draw_cm(p, s, c):
    """Function-block glyph: a body with input pins and one output pin."""
    p.setPen(QPen(c, 1.2))
    p.setBrush(QColor(c).lighter(180))
    p.drawRect(QRectF(s * 0.28, s * 0.20, s * 0.44, s * 0.56))
    p.setPen(QPen(c, 1.0))
    p.drawLine(QPointF(s * 0.10, s * 0.34), QPointF(s * 0.28, s * 0.34))
    p.drawLine(QPointF(s * 0.10, s * 0.62), QPointF(s * 0.28, s * 0.62))
    p.drawLine(QPointF(s * 0.72, s * 0.48), QPointF(s * 0.92, s * 0.48))


def _draw_sfc(p, s, c):
    """SFC glyph: two steps joined by a transition bar."""
    p.setPen(QPen(c, 1.2))
    p.setBrush(QColor(c).lighter(180))
    p.drawRect(QRectF(s * 0.24, s * 0.10, s * 0.52, s * 0.22))
    p.drawRect(QRectF(s * 0.24, s * 0.68, s * 0.52, s * 0.22))
    p.setPen(QPen(c, 1.2))
    p.drawLine(QPointF(s * 0.50, s * 0.32), QPointF(s * 0.50, s * 0.68))
    p.drawLine(QPointF(s * 0.32, s * 0.50), QPointF(s * 0.68, s * 0.50))


def _draw_equipment(p, s, c):
    p.setPen(QPen(c, 1.2))
    p.setBrush(Qt.NoBrush)
    cx = cy = s / 2.0
    r = s * 0.24
    p.drawEllipse(QPointF(cx, cy), r, r)
    for i in range(6):
        a = math.radians(i * 60.0)
        p.drawLine(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)),
                   QPointF(cx + (r + s * 0.13) * math.cos(a),
                           cy + (r + s * 0.13) * math.sin(a)))


def _draw_em_child(p, s, c):
    p.setPen(QPen(c, 1.2))
    p.drawLine(QPointF(s * 0.14, s * 0.50), QPointF(s * 0.70, s * 0.50))
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawPolygon(QPolygonF([QPointF(s * 0.66, s * 0.32),
                             QPointF(s * 0.90, s * 0.50),
                             QPointF(s * 0.66, s * 0.68)]))


def _draw_network(p, s, c):
    p.setPen(QPen(c, 1.1))
    p.drawLine(QPointF(s * 0.16, s * 0.28), QPointF(s * 0.84, s * 0.28))
    for x in (0.22, 0.50, 0.78):
        p.drawLine(QPointF(s * x, s * 0.28), QPointF(s * x, s * 0.60))
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    for x in (0.22, 0.50, 0.78):
        p.drawRect(QRectF(s * x - s * 0.10, s * 0.60, s * 0.20, s * 0.24))


def _draw_node(p, s, c):
    """Controller card: a rack module with a run LED."""
    p.setPen(QPen(c, 1.2))
    p.setBrush(QColor(c).lighter(185))
    p.drawRect(QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.68))
    p.setPen(QPen(c, 1.0))
    for y in (0.36, 0.52, 0.68):
        p.drawLine(QPointF(s * 0.26, s * y), QPointF(s * 0.60, s * y))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#2D8E3C"))
    p.drawEllipse(QPointF(s * 0.72, s * 0.30), s * 0.07, s * 0.07)


def _draw_unassigned(p, s, c):
    p.setPen(QPen(c, 1.3))
    p.setBrush(Qt.NoBrush)
    p.drawPolygon(QPolygonF([QPointF(s * 0.50, s * 0.14),
                             QPointF(s * 0.90, s * 0.84),
                             QPointF(s * 0.10, s * 0.84)]))
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawRect(QRectF(s * 0.46, s * 0.38, s * 0.08, s * 0.22))
    p.drawEllipse(QPointF(s * 0.50, s * 0.72), s * 0.05, s * 0.05)


_DRAW_MAP = {
    "project":    _draw_project,
    "area":       _draw_area,
    "folder":     _draw_folder,
    "cm":         _draw_cm,
    "sfc":        _draw_sfc,
    "equipment":  _draw_equipment,
    "em_child":   _draw_em_child,
    "network":    _draw_network,
    "node":       _draw_node,
    "unassigned": _draw_unassigned,
}


def node_icon(kind: str, state: str | None = None,
              size: int = _ICON_SIZE) -> QIcon:
    """Return the vector icon for a tree node ``kind``.

    ``state`` (``"online"`` / ``"modified"`` / ``"offline"``) adds a badge in
    the lower-right corner so a module's runtime state is visible without
    mangling its label text.
    """
    key = (kind, state, size)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    app = QApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0
    px = QPixmap(max(1, int(size * dpr)), max(1, int(size * dpr)))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.transparent)

    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing, True)
    color = QColor(_ICON_COLOR.get(kind, "#7B92AD"))
    from azeo_control_trainer.core.presentation.icon_set import (
        STYLE_LINE, default_style, has_icon, paint_colour, paint_line,
    )
    shared = _ICON_SET_NAMES.get(kind, kind)
    if has_icon(shared):
        if default_style() == STYLE_LINE:
            paint_line(p, shared, 0, 0, float(size), color.name())
        else:
            paint_colour(p, shared, 0, 0, float(size))
    else:
        _DRAW_MAP.get(kind, _draw_folder)(p, float(size), color)

    if state in _STATE_BADGE:
        p.setPen(QPen(QColor("#FFFFFF"), 1.2))
        p.setBrush(QColor(_STATE_BADGE[state]))
        r = size * 0.22
        p.drawEllipse(QPointF(size * 0.76, size * 0.76), r, r)

    p.end()

    icon = QIcon(px)
    _ICON_CACHE[key] = icon
    return icon


# ── Module display name ──────────────────────────────────────────────

_NAME_CACHE: dict[tuple[str, float], str] = {}


def module_display_name(path: Path | str) -> str:
    """Real module name for a strategy/SFC file.

    Prefers the ``name`` field stored inside the module JSON and falls back
    to the file stem **verbatim**.  The old behaviour derived the label from
    the filename with ``stem.replace("_", " ").title()``-style mangling,
    which turned the tag ``FIC-101`` into ``Fic 101``.
    """
    p = Path(path)
    try:
        key = (str(p), p.stat().st_mtime)
    except OSError:
        return p.stem
    cached = _NAME_CACHE.get(key)
    if cached is not None:
        return cached

    name = p.stem
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("name")
        if isinstance(raw, str) and raw.strip():
            name = raw.strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        pass

    _NAME_CACHE[key] = name
    return name

# ── ISA-101 Silver Theme styles ──────────────────────────────────────
_TREE_STYLE = f"""
QTreeWidget {{
    background-color: {UI.pane};
    border: none;
    font-size: 9pt;
    color: {UI.blue};
}}
QTreeWidget::item {{
    padding: 3px 2px;
    border: none;
}}
QTreeWidget::item:selected {{
    background-color: {UI.selection};
    color: {UI.blue};
}}
QTreeWidget::item:hover {{
    background-color: {UI.hover};
}}
QTreeWidget::indicator {{
    width: 13px;
    height: 13px;
}}
QTreeWidget::indicator:unchecked {{
    border: 1px solid {UI.border};
    background: white;
    border-radius: 2px;
}}
QTreeWidget::indicator:checked {{
    border: 1px solid {UI.blue};
    background: {UI.blue};
    border-radius: 2px;
}}
"""

_HEADER_STYLE = f"""
QLabel {{
    font-weight: bold;
    font-size: 9pt;
    color: {UI.blue};
    background: transparent;
    padding: 4px 6px 2px 6px;
    border: none;
}}
"""

_BTN_STYLE = (
    "QPushButton {"
    f"  background-color: {UI.chrome}; color: {UI.blue};"
    f"  border: 1px solid {UI.border}; border-radius: 3px;"
    "  font-size: 9pt; font-weight: bold; padding: 3px 8px;"
    "}"
    f"QPushButton:hover {{ background-color: {UI.selection}; }}"
    f"QPushButton:disabled {{ color: {UI.disabled}; border-color: {UI.border}; }}"
)


_CONTEXT_MENU_STYLE = f"""
QMenu {{
    background: {UI.chrome};
    border: 1px solid {UI.border};
    border-radius: 4px;
    padding: 4px 0;
    font-size: 9pt;
}}
QMenu::item {{
    padding: 6px 32px 6px 12px;
    color: {UI.blue};
    border-radius: 2px;
    margin: 1px 4px;
}}
QMenu::item:selected {{
    background: {UI.selection};
}}
QMenu::item:disabled {{
    color: {UI.disabled};
}}
QMenu::separator {{
    height: 1px;
    background: {UI.border};
    margin: 4px 8px;
}}
QMenu::icon {{
    padding-left: 8px;
}}
"""


# ── Project config persistence ────────────────────────────────────────

def _project_config_path() -> Path:
    return _strategy_dir() / "_project.json"


def _load_project_config() -> dict:
    """Load the project config (areas, etc.) from _project.json."""
    path = _project_config_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_project_config(config: dict) -> None:
    """Save the project config to _project.json."""
    _strategy_dir().mkdir(parents=True, exist_ok=True)
    path = _project_config_path()
    # A torn project index can hide every module created by a successful bulk
    # operation.  Use the same deterministic atomic writer as module files.
    _sio.write_json_transactional(path, config)


def _ensure_default_areas(config: dict) -> dict:
    """Ensure config has at least one area. If empty, create a 'Default'
    area containing all existing files on disk."""
    if config.get("areas"):
        return config

    # Gather all existing files
    cm_files = []
    sfc_files = []
    folders = list_strategy_folders()
    for folder_name, paths in folders.items():
        for p in paths:
            try:
                rel = str(p.relative_to(_strategy_dir())).replace("\\", "/")
            except ValueError:
                rel = str(p)
            if folder_name.lower() == "sequence":
                sfc_files.append(rel)
            else:
                cm_files.append(rel)

    em_files = []
    for em_path in list_equipment_modules():
        try:
            em_files.append(str(em_path.relative_to(_strategy_dir())).replace(
                "\\", "/"))
        except ValueError:
            em_files.append(str(em_path))

    config["areas"] = [{
        "name": "Default",
        "area_id": uuid.uuid4().hex[:12],
        "description": "Default area",
        "strategies": cm_files,
        "sfc_modules": sfc_files,
        "equipment_modules": em_files,
    }]
    return config


class ProjectTree(QWidget):
    """Project explorer tree with area/unit and controller hierarchies.

    Signals:
        strategyLoadRequested(str): emitted with file path when user wants to load
        strategyDeleteRequested(str): emitted with file path when user wants to delete
    """

    strategyLoadRequested = Signal(str)    # file path
    strategyDeleteRequested = Signal(str)  # file path

    def __init__(self, plugin=None, parent=None):
        super().__init__(parent)
        self._module_states: dict[str, str] = {}
        self._plugin = plugin
        self._project_config: dict = {}
        self._area_nodes: dict[str, QTreeWidgetItem] = {}  # area_id -> area tree node
        # The area-level entry points at the Units branch so area-wide export
        # selection still covers every unit. Individual CM folders are keyed
        # separately for unit-scoped creation and selection.
        self._area_cm_folders: dict[str, QTreeWidgetItem] = {}
        self._unit_cm_folders: dict[tuple[str, str], QTreeWidgetItem] = {}
        self._area_sfc_folders: dict[str, QTreeWidgetItem] = {}
        self._area_em_folders: dict[str, QTreeWidgetItem] = {}
        # Modules discovered while populating the area branch, reused to build
        # the control-network branch: {"path", "kind", "area_id", "rel"}
        self._modules: list[dict] = []
        # Checkboxes are an export affordance, not a Azeo Explorer feature —
        # they only appear while the user is explicitly in export mode.
        self._export_mode = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QLabel("PROJECT EXPLORER")
        header.setStyleSheet(_HEADER_STYLE)
        layout.addWidget(header)

        # Tree
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setAnimated(True)
        self._tree.setIndentation(16)
        self._tree.setStyleSheet(_TREE_STYLE)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._tree)

        # Export toolbar row.  Only the mode button shows normally; the
        # selection controls (and the per-module checkboxes they drive)
        # appear once the user opts into export mode.
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(4, 3, 4, 3)
        btn_row.setSpacing(4)

        self._btn_export_mode = QPushButton("Export Modules…")
        self._btn_export_mode.setStyleSheet(_BTN_STYLE)
        self._btn_export_mode.setFixedHeight(22)
        self._btn_export_mode.setToolTip(
            "Select modules to export (shows selection checkboxes)")
        self._btn_export_mode.clicked.connect(
            lambda: self.set_export_mode(True))
        btn_row.addWidget(self._btn_export_mode)

        self._btn_select_all = QPushButton("All")
        self._btn_select_all.setStyleSheet(_BTN_STYLE)
        self._btn_select_all.setFixedHeight(22)
        self._btn_select_all.setToolTip("Select all strategies")
        self._btn_select_all.clicked.connect(self._select_all)
        btn_row.addWidget(self._btn_select_all)

        self._btn_select_none = QPushButton("None")
        self._btn_select_none.setStyleSheet(_BTN_STYLE)
        self._btn_select_none.setFixedHeight(22)
        self._btn_select_none.setToolTip("Deselect all strategies")
        self._btn_select_none.clicked.connect(self._select_none)
        btn_row.addWidget(self._btn_select_none)

        self._btn_cancel_export = QPushButton("Cancel")
        self._btn_cancel_export.setStyleSheet(_BTN_STYLE)
        self._btn_cancel_export.setFixedHeight(22)
        self._btn_cancel_export.setToolTip("Leave export mode")
        self._btn_cancel_export.clicked.connect(
            lambda: self.set_export_mode(False))
        btn_row.addWidget(self._btn_cancel_export)

        btn_row.addStretch()

        self._btn_export = QPushButton("Export Selected\u2026")
        self._btn_export.setStyleSheet(_BTN_STYLE)
        self._btn_export.setFixedHeight(22)
        self._btn_export.setToolTip("Export checked strategies to a folder")
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._export_selected)
        btn_row.addWidget(self._btn_export)

        for _b in (self._btn_select_all, self._btn_select_none,
                   self._btn_cancel_export, self._btn_export):
            _b.setVisible(False)

        layout.addLayout(btn_row)

        # Selection count label
        self._lbl_count = QLabel("")
        self._lbl_count.setStyleSheet(
            f"QLabel {{ font-size: 9pt; color: {UI.text_muted}; padding: 0 6px 2px 6px; }}"
        )
        layout.addWidget(self._lbl_count)

        self._populate()

    # ── Populate tree ─────────────────────────────────────────────────

    def _populate(self):
        """Build the full tree from disk + project config."""
        self._tree.blockSignals(True)
        self._tree.clear()
        self._area_nodes.clear()
        self._area_cm_folders.clear()
        self._unit_cm_folders.clear()
        self._area_sfc_folders.clear()
        self._area_em_folders.clear()
        self._modules.clear()

        # Load project config
        self._project_config = _load_project_config()
        self._project_config = _ensure_default_areas(self._project_config)

        # Root: Project
        _project_name = (self._plugin.display_name
                         if self._plugin else "Heater F-01")
        project = self._make_node(_project_name, "project")
        project.setFont(0, QFont("Segoe UI", 9, QFont.Bold))
        self._tree.addTopLevelItem(project)

        # Create area nodes
        for area_data in self._project_config.get("areas", []):
            area_id = area_data.get("area_id", uuid.uuid4().hex[:12])
            area_name = area_data.get("name", "Area")
            area_node = self._make_node(area_name, "area")
            area_node.setData(0, _ROLE_AREA_ID, area_id)
            area_node.setFont(0, QFont("Segoe UI", 9, QFont.Bold))
            desc = area_data.get("description", "")
            if desc:
                area_node.setToolTip(0, desc)
            project.addChild(area_node)
            self._area_nodes[area_id] = area_node

            # Logical hierarchy: control modules belong to process units. The
            # area still owns the files, while the unit mapping decides where
            # engineers find them in the project tree.
            units_folder = self._make_node("Units", "units")
            units_folder.setData(0, _ROLE_AREA_ID, area_id)
            units_folder.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            area_node.addChild(units_folder)
            self._area_cm_folders[area_id] = units_folder
            self._populate_units_branch(units_folder, area_data)

            # SFC Modules folder
            sfc_folder = self._make_node("SFC Modules", "folder")
            sfc_folder.setData(0, _ROLE_AREA_ID, area_id)
            sfc_folder.setData(0, _ROLE_FOLDER_KIND, "sfc")
            sfc_folder.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            area_node.addChild(sfc_folder)
            self._area_sfc_folders[area_id] = sfc_folder

            # Populate SFC folder
            self._populate_sfc_folder(sfc_folder, area_data.get("sfc_modules", []))

            # Equipment Modules folder
            em_folder = self._make_node("Equipment Modules", "folder")
            em_folder.setData(0, _ROLE_AREA_ID, area_id)
            em_folder.setData(0, _ROLE_FOLDER_KIND, "equipment")
            em_folder.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            area_node.addChild(em_folder)
            self._area_em_folders[area_id] = em_folder

            # Populate EM folder
            self._populate_em_folder(em_folder, area_data.get("equipment_modules", []))

            area_node.setExpanded(True)
            units_folder.setExpanded(True)

        # Physical hierarchy: which node runs what (Azeo assigns modules to
        # a controller; the tree used to stop at Area and never say where a
        # module actually runs).
        self._populate_network_branch(project)

        project.setExpanded(True)

        self._tree.blockSignals(False)
        self._update_selection_count()

    # ── Control network (node assignment) ─────────────────────────────

    @staticmethod
    def _norm_rel(rel: str) -> str:
        """Normalise a stored relative path for assignment lookups."""
        return str(rel).replace("\\", "/").lower()

    def _default_node_name(self) -> str:
        pid = getattr(self._plugin, "id", None) or "SIM"
        return f"CTLR-{str(pid).upper()[:10]}"

    def nodes(self) -> list[dict]:
        """Controller/node definitions, synthesizing a default when absent.

        A project that has never assigned anything still runs all of its
        modules in one runtime, so the synthesized node owns everything —
        that is more truthful than showing 22 "unassigned" modules.
        """
        nodes = self._project_config.get("nodes")
        if nodes:
            return list(nodes)
        return [{
            "node_id": _DEFAULT_NODE_ID,
            "name": self._default_node_name(),
            "type": "controller",
            "description": "Default controller (synthesized — not yet saved)",
        }]

    def areas(self) -> list[dict]:
        """Detached area descriptors for authoring dialogs."""
        return [dict(area) for area in self._project_config.get("areas", [])]

    def register_control_modules(
        self, paths: list[Path | str], area_id: str | None = None,
        unit_name: str | None = None,
    ) -> None:
        """Add generated control modules to one area and process unit.

        Callers may name the target unit explicitly. Otherwise a matching
        ``metadata.unit`` in the generated document is adopted when that unit
        exists in the project; modules with no trustworthy match remain under
        Unassigned.
        """
        # Work on a detached copy. A failed atomic write must not leave the UI
        # believing modules were registered when the on-disk index is intact.
        config = copy.deepcopy(self._project_config)
        areas = config.get("areas", [])
        if not areas:
            raise ValueError("The project has no area for generated modules")
        area = (next((item for item in areas
                      if item.get("area_id") == area_id), None)
                if area_id else areas[0])
        if area is None:
            raise ValueError("The selected project area no longer exists")
        entries = area.setdefault("strategies", [])
        normalized = {str(item).replace("\\", "/").casefold()
                      for item in entries}
        for raw_path in paths:
            path = Path(raw_path).resolve()
            try:
                rel = str(path.relative_to(_strategy_dir().resolve())).replace(
                    "\\", "/")
            except ValueError as exc:
                raise ValueError(
                    f"Generated module is outside this project: {path}") from exc
            if rel.casefold() not in normalized:
                entries.append(rel)
                normalized.add(rel.casefold())
            module_name = module_display_name(path)
            existing_unit = self._module_unit_membership(area, module_name)
            inferred_unit = self._declared_module_unit(path)
            target_unit = (
                unit_name if unit_name is not None
                else existing_unit or inferred_unit
            )
            if target_unit:
                self._set_module_unit_membership(
                    area, module_name, target_unit)
        _save_project_config(config)
        self._project_config = config

    def _assigned_node_id(self, rel: str) -> str:
        """Node id a module is assigned to, or ``""`` when unassigned."""
        assignments = self._project_config.get("assignments") or {}
        nid = assignments.get(self._norm_rel(rel))
        if nid:
            return str(nid)
        # No explicit nodes configured ⇒ everything lives on the default one.
        return "" if self._project_config.get("nodes") else _DEFAULT_NODE_ID

    def _populate_network_branch(self, project: QTreeWidgetItem):
        """Build ``Control Network → node → assigned modules``."""
        net = self._make_node("Control Network", "network")
        net.setFont(0, QFont("Segoe UI", 9, QFont.Bold))
        net.setToolTip(0, "Modules grouped by the controller/node they run on")
        project.addChild(net)

        node_items: dict[str, QTreeWidgetItem] = {}
        for nd in self.nodes():
            nid = str(nd.get("node_id", ""))
            item = self._make_node(nd.get("name", "Node"), "node")
            item.setData(0, _ROLE_NODE_ID, nid)
            item.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            net.addChild(item)
            node_items[nid] = item

        unassigned = self._make_node("Unassigned", "unassigned")
        unassigned.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
        unassigned.setToolTip(
            0, "Modules with no controller assignment — they will not be "
               "downloaded to a node")
        unassigned_used = False

        counts: dict[str, int] = {}
        for mod in sorted(self._modules,
                          key=lambda m: module_display_name(m["path"]).lower()):
            nid = self._assigned_node_id(mod["rel"])
            target = node_items.get(nid)
            if target is None:
                target = unassigned
                unassigned_used = True
                nid = ""
            counts[nid] = counts.get(nid, 0) + 1
            self._add_strategy_node(target, mod["path"], mod["area_id"],
                                    kind=mod["kind"], record=False,
                                    unit_name=mod.get("unit_name"))

        for nid, item in node_items.items():
            n = counts.get(nid, 0)
            item.setToolTip(
                0, f"Controller node — {n} module{'' if n == 1 else 's'} assigned")
            item.setExpanded(True)

        if unassigned_used:
            net.addChild(unassigned)
            unassigned.setExpanded(True)

        net.setExpanded(True)

    def _partition_control_modules_by_unit(
        self, area_data: dict,
    ) -> tuple[list[tuple[dict, list[str]]], list[str]]:
        """Resolve an area's strategy paths against ``units[].modules``.

        Historical projects store unit membership as module names, while a
        project author may reasonably use a relative path. Accept both forms,
        match case-insensitively, and assign each strategy at most once. Any
        missing, stale, or ambiguous mapping remains visible under Unassigned
        instead of disappearing from the engineering surface.
        """
        rel_paths = list(area_data.get("strategies") or [])
        candidates: dict[str, set[str]] = {}
        for rel in rel_paths:
            path = _strategy_dir() / rel
            normalized = self._norm_rel(rel)
            candidates[normalized] = {
                normalized,
                Path(rel).stem.casefold(),
                module_display_name(path).casefold(),
            }

        assigned: set[str] = set()
        groups: list[tuple[dict, list[str]]] = []
        for unit in area_data.get("units") or []:
            if not isinstance(unit, dict):
                continue
            unit_name = str(unit.get("name") or "").strip()
            if not unit_name:
                continue
            references: set[str] = set()
            for raw in unit.get("modules") or []:
                text = str(raw).strip().replace("\\", "/").casefold()
                if text:
                    references.add(text)
                    references.add(Path(text).stem.casefold())
            matched: list[str] = []
            for rel in rel_paths:
                normalized = self._norm_rel(rel)
                if normalized in assigned:
                    continue
                if candidates[normalized] & references:
                    matched.append(rel)
                    assigned.add(normalized)
            groups.append((unit, matched))

        unassigned = [
            rel for rel in rel_paths if self._norm_rel(rel) not in assigned
        ]
        return groups, unassigned

    def _populate_units_branch(
        self, units_folder: QTreeWidgetItem, area_data: dict,
    ) -> None:
        """Build ``Units -> Unit -> Control Modules`` for one area."""
        area_id = units_folder.data(0, _ROLE_AREA_ID)
        groups, unassigned = self._partition_control_modules_by_unit(area_data)

        for unit, rel_paths in groups:
            unit_name = str(unit.get("name") or "").strip()
            unit_node = self._make_node(unit_name, "unit")
            unit_node.setData(0, _ROLE_AREA_ID, area_id)
            unit_node.setData(0, _ROLE_UNIT_NAME, unit_name)
            unit_node.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            description = str(unit.get("description") or "").strip()
            count = len(rel_paths)
            summary = f"{count} control module{'' if count == 1 else 's'}"
            unit_node.setToolTip(
                0, f"{description}\n{summary}" if description else summary)

            cm_folder = self._make_node("Control Modules", "folder")
            cm_folder.setData(0, _ROLE_AREA_ID, area_id)
            cm_folder.setData(0, _ROLE_UNIT_NAME, unit_name)
            cm_folder.setData(0, _ROLE_FOLDER_KIND, "cm")
            cm_folder.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            unit_node.addChild(cm_folder)
            self._unit_cm_folders[(str(area_id), unit_name)] = cm_folder
            self._populate_cm_folder(cm_folder, rel_paths)
            units_folder.addChild(unit_node)

        # A project without unit definitions still gets one honest home for
        # its modules. When definitions exist, show this node only when it has
        # content so a fully assigned plant does not carry a false warning.
        if unassigned or not groups:
            unit_node = self._make_node("Unassigned", "unassigned_unit")
            unit_node.setData(0, _ROLE_AREA_ID, area_id)
            unit_node.setData(0, _ROLE_UNIT_NAME, "")
            unit_node.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            unit_node.setToolTip(
                0, "Control modules with no process-unit assignment")
            cm_folder = self._make_node("Control Modules", "folder")
            cm_folder.setData(0, _ROLE_AREA_ID, area_id)
            cm_folder.setData(0, _ROLE_UNIT_NAME, "")
            cm_folder.setData(0, _ROLE_FOLDER_KIND, "cm")
            cm_folder.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            unit_node.addChild(cm_folder)
            self._unit_cm_folders[(str(area_id), "")] = cm_folder
            self._populate_cm_folder(cm_folder, unassigned)
            units_folder.addChild(unit_node)
            unit_node.setExpanded(True)

        units_folder.setExpanded(True)

    def _populate_cm_folder(self, folder: QTreeWidgetItem, rel_paths: list[str]):
        """Populate a Control Modules folder with strategy nodes."""
        area_id = folder.data(0, _ROLE_AREA_ID)
        unit_name = folder.data(0, _ROLE_UNIT_NAME)

        if not rel_paths:
            empty = QTreeWidgetItem(["(no control modules)"])
            empty.setForeground(0, QColor("#9BADC6"))
            empty.setFlags(Qt.NoItemFlags)
            folder.addChild(empty)
            return

        # Group by subfolder
        top_level = []
        subfolders: dict[str, list[tuple[str, Path]]] = {}

        for rel in rel_paths:
            abs_path = _strategy_dir() / rel
            if not abs_path.exists():
                continue
            parts = Path(rel).parts
            if len(parts) > 1:
                subfolder_name = parts[0]
                subfolders.setdefault(subfolder_name, []).append((rel, abs_path))
            else:
                top_level.append((rel, abs_path))

        # Add top-level strategies
        for rel, abs_path in top_level:
            self._add_strategy_node(
                folder, abs_path, area_id, rel=rel, unit_name=unit_name)

        # Add subfolder groups. ``control/``, ``sequence/`` and ``equipment/``
        # are storage conventions, not user-facing groups — the parent folder
        # already says "Control Modules", so nesting a "Control" node under it
        # just buries every module one level deeper. Any other subfolder is a
        # deliberate grouping and keeps its node.
        for subfolder_name, items in sorted(subfolders.items()):
            if subfolder_name.lower() in _STORAGE_DIRS:
                for rel, abs_path in items:
                    self._add_strategy_node(
                        folder, abs_path, area_id, rel=rel,
                        unit_name=unit_name)
                continue
            display = subfolder_name.replace("_", " ").title()
            subfolder_node = self._make_node(display, "folder")
            subfolder_node.setData(0, _ROLE_AREA_ID, area_id)
            subfolder_node.setData(0, _ROLE_UNIT_NAME, unit_name)
            subfolder_node.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            for rel, abs_path in items:
                self._add_strategy_node(subfolder_node, abs_path, area_id,
                                        rel=rel, unit_name=unit_name)
            subfolder_node.setExpanded(True)
            folder.addChild(subfolder_node)

    def _populate_sfc_folder(self, folder: QTreeWidgetItem, rel_paths: list[str]):
        """Populate an SFC Modules folder."""
        area_id = folder.data(0, _ROLE_AREA_ID)

        if not rel_paths:
            empty = QTreeWidgetItem(["(no SFC modules)"])
            empty.setForeground(0, QColor("#9BADC6"))
            empty.setFlags(Qt.NoItemFlags)
            folder.addChild(empty)
            return

        for rel in rel_paths:
            abs_path = _strategy_dir() / rel
            if not abs_path.exists():
                continue
            self._add_strategy_node(folder, abs_path, area_id, kind="sfc",
                                    rel=rel)

        folder.setExpanded(True)

    def _populate_em_folder(self, folder: QTreeWidgetItem, rel_paths: list[str]):
        """Populate an Equipment Modules folder."""
        area_id = folder.data(0, _ROLE_AREA_ID)

        if not rel_paths:
            empty = QTreeWidgetItem(["(no equipment modules)"])
            empty.setForeground(0, QColor("#9BADC6"))
            empty.setFlags(Qt.NoItemFlags)
            folder.addChild(empty)
            return

        for rel in rel_paths:
            abs_path = _strategy_dir() / rel
            if not abs_path.exists():
                continue
            try:
                em_data = load_equipment_module(abs_path)
            except Exception:
                continue

            name = em_data.get("name", abs_path.stem)
            eq_type = em_data.get("equipment_type", "")
            display = f"{name}  [{eq_type}]" if eq_type else name

            em_node = self._make_node(display, "equipment")
            em_node.setData(0, _ROLE_FILE_PATH, str(abs_path))
            em_node.setData(0, _ROLE_AREA_ID, area_id)
            em_node.setToolTip(0, str(abs_path))
            em_node.setFont(0, QFont("Segoe UI", 8, QFont.Bold))

            # Child CM references
            children = resolve_child_paths(em_data)
            for cm_ref, cm_path in children.get("control_modules", []):
                display_cm = (module_display_name(cm_path) if cm_path
                              else Path(cm_ref).stem)
                child_node = self._make_node(display_cm, "em_child")
                if cm_path:
                    child_node.setData(0, _ROLE_FILE_PATH, str(cm_path))
                    child_node.setToolTip(0, str(cm_path))
                else:
                    child_node.setForeground(0, QColor("#C82A3A"))
                    child_node.setToolTip(0, f"Not found: {cm_ref}")
                child_node.setFlags(child_node.flags() & ~Qt.ItemIsUserCheckable)
                em_node.addChild(child_node)

            # Child SFC references
            for sfc_ref, sfc_path in children.get("sfc_modules", []):
                display_sfc = (module_display_name(sfc_path) if sfc_path
                               else Path(sfc_ref).stem)
                child_node = self._make_node(f"{display_sfc} (SFC)", "em_child")
                if sfc_path:
                    child_node.setData(0, _ROLE_FILE_PATH, str(sfc_path))
                    child_node.setToolTip(0, str(sfc_path))
                else:
                    child_node.setForeground(0, QColor("#C82A3A"))
                    child_node.setToolTip(0, f"Not found: {sfc_ref}")
                child_node.setFlags(child_node.flags() & ~Qt.ItemIsUserCheckable)
                em_node.addChild(child_node)

            # State info
            states = em_data.get("states", [])
            init_state = em_data.get("initial_state", "")
            if states:
                # Joined outside the f-string: a backslash escape in
                # an f-string expression is 3.12+ and the floor is 3.11.
                joined = " \u2192 ".join(states)
                state_text = f"States: {joined}"
                if init_state:
                    state_text += f"  (init: {init_state})"
                state_node = QTreeWidgetItem([state_text])
                state_node.setForeground(0, QColor("#7B92AD"))
                state_node.setFlags(Qt.NoItemFlags)
                f = QFont("Segoe UI", 7)
                f.setItalic(True)
                state_node.setFont(0, f)
                em_node.addChild(state_node)

            folder.addChild(em_node)

        folder.setExpanded(True)

    def _add_strategy_node(self, parent: QTreeWidgetItem, path: Path,
                           area_id: str | None = None, kind: str = "cm",
                           rel: str | None = None, record: bool = True,
                           unit_name: str | None = None):
        """Add a single strategy/SFC module node to the given parent.

        ``kind`` is ``"cm"`` or ``"sfc"`` — it selects the icon and is the
        authoritative CM/SFC discriminator for the context menu (the old
        parent-folder comparison cannot work now that a module also appears
        under its control-network node).
        """
        node = self._make_node(module_display_name(path), "strategy",
                               icon_kind=kind)
        node.setData(0, _ROLE_FILE_PATH, str(path))
        node.setData(0, _ROLE_MODULE_KIND, kind)
        tooltip = str(path)
        if kind == "cm":
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                link = document.get("module_class") or {}
                if link.get("definition_id"):
                    from azeo_control_trainer.core.strategy.module_classes import (
                        definition_state,
                        project_module_class_library,
                    )
                    state = definition_state(
                        document, project_module_class_library())
                    tooltip += (
                        f"\nClass: {link.get('definition_name', '?')} "
                        f"r{link.get('definition_revision', '?')} · "
                        f"{state.upper()}")
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                # A damaged module is handled by the ordinary open/validation
                # path; tree population remains available for recovery.
                pass
        node.setToolTip(0, tooltip)
        if self._export_mode:
            node.setFlags(node.flags() | Qt.ItemIsUserCheckable)
            node.setCheckState(0, Qt.Unchecked)
        else:
            node.setFlags(node.flags() & ~Qt.ItemIsUserCheckable)
        if area_id:
            node.setData(0, _ROLE_AREA_ID, area_id)
        if unit_name is not None:
            node.setData(0, _ROLE_UNIT_NAME, unit_name)
        parent.addChild(node)

        if record:
            if rel is None:
                try:
                    rel = str(path.relative_to(_strategy_dir())).replace(
                        "\\", "/")
                except ValueError:
                    rel = str(path)
            self._modules.append({"path": path, "kind": kind,
                                  "area_id": area_id, "rel": rel,
                                  "unit_name": unit_name})

    def refresh(self):
        """Public method to refresh the full tree."""
        self._populate()
        if self._module_states:
            self.set_module_states(self._module_states)

    # ── Module state (on scan / modified) ─────────────────────────────
    def set_module_states(self, states: dict[str, str]):
        """Mark modules with their runtime state, keyed by absolute file path.

        ``states`` values: ``"online"`` (on scan), ``"modified"`` (edited since
        download) or ``"offline"``. The tree previously conveyed no runtime
        state at all, so a project of 23 modules gave no clue which were
        running — Azeo shows this in the hierarchy.

        The state is carried by an icon badge plus the text colour; the label
        itself stays the module's exact name so a tag can still be read and
        copied verbatim.
        """
        self._module_states = dict(states)
        self._tree.blockSignals(True)
        it = QTreeWidgetItemIterator(self._tree)
        while it.value():
            node = it.value()
            if node.data(0, _ROLE_NODE_TYPE) == "strategy":
                path = node.data(0, _ROLE_FILE_PATH)
                kind = node.data(0, _ROLE_MODULE_KIND) or "cm"
                state = self._module_states.get(str(path))
                if state in _STATE_BADGE:
                    node.setIcon(0, node_icon(kind, state))
                    node.setForeground(0, QColor(_STATE_TEXT[state]))
                    node.setToolTip(0, f"{path}\n{_STATE_LABEL[state]}")
                else:
                    node.setIcon(0, node_icon(kind))
                    node.setForeground(0, QColor(_TEXT_DEFAULT))
                    node.setToolTip(0, str(path))
            it += 1
        self._tree.blockSignals(False)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _make_node(text: str, node_type: str,
                   icon_kind: str | None = None) -> QTreeWidgetItem:
        """Create a tree node with the given type role and vector icon.

        The label text is left exactly as given \u2014 the emoji prefixes this
        used to splice in were indistinguishable between control modules and
        SFC modules and contaminated the copied/searched text.  ``icon_kind``
        overrides the icon lookup, which is how the shared ``"strategy"``
        node type still renders distinct CM and SFC icons.
        """
        item = QTreeWidgetItem([text])
        item.setData(0, _ROLE_NODE_TYPE, node_type)
        item.setIcon(0, node_icon(icon_kind or node_type))
        return item

    # ── Checkbox helpers ──────────────────────────────────────────────

    def _get_checked_paths(self) -> list[Path]:
        """Return file paths for all checked strategy nodes across all areas.

        A module appears twice (under its area and under its network node),
        so the result is de-duplicated.
        """
        paths: list[Path] = []
        self._collect_checked(self._tree.invisibleRootItem(), paths)
        seen: set[str] = set()
        unique: list[Path] = []
        for p in paths:
            key = str(p)
            if key not in seen:
                seen.add(key)
                unique.append(p)
        return unique

    def _collect_checked(self, parent: QTreeWidgetItem, paths: list[Path]):
        """Recursively collect checked strategy paths."""
        for i in range(parent.childCount()):
            child = parent.child(i)
            if (child.data(0, _ROLE_NODE_TYPE) == "strategy"
                    and child.checkState(0) == Qt.Checked):
                p = child.data(0, _ROLE_FILE_PATH)
                if p:
                    paths.append(Path(p))
            self._collect_checked(child, paths)

    # ── Export mode ───────────────────────────────────────────────────

    def is_export_mode(self) -> bool:
        return self._export_mode

    def set_export_mode(self, on: bool):
        """Show/hide the per-module export checkboxes.

        Azeo's Explorer has no checkbox column — a checkbox next to a
        module reads as "download selection" or "enabled".  They now only
        exist while the user is explicitly picking modules to export.
        """
        on = bool(on)
        if on == self._export_mode:
            return
        self._export_mode = on
        self._btn_export_mode.setVisible(not on)
        for b in (self._btn_select_all, self._btn_select_none,
                  self._btn_cancel_export, self._btn_export):
            b.setVisible(on)
        self._populate()
        if self._module_states:
            self.set_module_states(self._module_states)

    def _update_selection_count(self):
        """Update the selection count label and export button state."""
        if not self._export_mode:
            self._lbl_count.setText("")
            self._btn_export.setEnabled(False)
            return
        checked = self._get_checked_paths()
        n = len(checked)
        if n == 0:
            self._lbl_count.setText("Check the modules to export")
            self._btn_export.setEnabled(False)
        else:
            self._lbl_count.setText(f"{n} strateg{'y' if n == 1 else 'ies'} selected")
            self._btn_export.setEnabled(True)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        """Handle checkbox state changes."""
        if item.data(0, _ROLE_NODE_TYPE) == "strategy":
            self._update_selection_count()

    def _select_all(self):
        """Check all strategy checkboxes across all areas."""
        self._tree.blockSignals(True)
        self._set_check_recursive(self._tree.invisibleRootItem(), Qt.Checked)
        self._tree.blockSignals(False)
        self._update_selection_count()

    def _select_none(self):
        """Uncheck all strategy checkboxes across all areas."""
        self._tree.blockSignals(True)
        self._set_check_recursive(self._tree.invisibleRootItem(), Qt.Unchecked)
        self._tree.blockSignals(False)
        self._update_selection_count()

    def _set_check_recursive(self, parent: QTreeWidgetItem, state):
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.data(0, _ROLE_NODE_TYPE) == "strategy":
                child.setCheckState(0, state)
            self._set_check_recursive(child, state)

    # ── Export ────────────────────────────────────────────────────────

    def _export_selected(self):
        """Export checked strategies to a user-chosen folder."""
        checked = self._get_checked_paths()
        if not checked:
            return

        dest = QFileDialog.getExistingDirectory(
            self, "Export Strategies To\u2026",
            str(Path.home()),
        )
        if not dest:
            return

        dest_dir = Path(dest)
        copied = []
        errors = []
        for src in checked:
            try:
                dst = dest_dir / src.name
                shutil.copy2(str(src), str(dst))
                copied.append(src.name)
            except Exception as e:
                errors.append(f"{src.name}: {e}")

        msg_parts = []
        if copied:
            msg_parts.append(
                f"Exported {len(copied)} strateg{'y' if len(copied) == 1 else 'ies'}:"
                f"\n  \u2022 " + "\n  \u2022 ".join(copied))
        if errors:
            msg_parts.append("Errors:\n  " + "\n  ".join(errors))

        QMessageBox.information(
            self, "Export Complete",
            "\n\n".join(msg_parts),
        )
        self.set_export_mode(False)

    # ── Project config helpers ────────────────────────────────────────

    def _get_area_data(self, area_id: str) -> dict | None:
        """Get area dict from project config by area_id."""
        for area in self._project_config.get("areas", []):
            if area.get("area_id") == area_id:
                return area
        return None

    def _save_config(self):
        """Persist current project config."""
        _save_project_config(self._project_config)

    def _get_area_names(self) -> list[str]:
        """Return list of all area names."""
        return [a.get("name", "") for a in self._project_config.get("areas", [])]

    def _find_area_node(self, item: QTreeWidgetItem) -> QTreeWidgetItem | None:
        """Walk up to find the area node that contains this item."""
        current = item
        while current is not None:
            if current.data(0, _ROLE_NODE_TYPE) == "area":
                return current
            current = current.parent()
        return None

    def _get_area_id_for_item(self, item: QTreeWidgetItem) -> str | None:
        """Get the area_id for any item in the tree."""
        area_id = item.data(0, _ROLE_AREA_ID)
        if area_id:
            return area_id
        area_node = self._find_area_node(item)
        if area_node:
            return area_node.data(0, _ROLE_AREA_ID)
        return None

    # ── Events ────────────────────────────────────────────────────────

    def _on_double_click(self, item: QTreeWidgetItem, column: int):
        node_type = item.data(0, _ROLE_NODE_TYPE)
        if node_type == "strategy":
            path = item.data(0, _ROLE_FILE_PATH)
            if path:
                self.strategyLoadRequested.emit(path)
        elif node_type == "equipment":
            self._edit_equipment_module(item)
        elif node_type == "em_child":
            path = item.data(0, _ROLE_FILE_PATH)
            if path:
                self.strategyLoadRequested.emit(path)

    def _on_context_menu(self, pos):
        item = self._tree.itemAt(pos)

        from azeo_control_trainer.core.presentation.menu_style import studio_menu

        if item is None:
            menu = studio_menu("PROJECT", parent=self)
        else:
            menu = studio_menu(item.text(0),
                               str(item.data(0, _ROLE_NODE_TYPE) or ""),
                               parent=self)

        if item is None:
            menu.addAction("Refresh All", self._populate)
            menu.addSeparator()
            menu.addAction("Expand All", self._tree.expandAll)
            menu.addAction("Collapse All", self._tree.collapseAll)
            menu.exec_transient(self._tree.viewport().mapToGlobal(pos))
            return

        node_type = item.data(0, _ROLE_NODE_TYPE)

        if node_type == "project":
            # ── Project root ──
            new_menu = menu.addMenu("New")
            new_menu.setStyleSheet(_CONTEXT_MENU_STYLE)
            new_menu.addAction("Area\u2026", self._new_area)
            menu.addSeparator()
            new_menu.addAction("Controller Node…", self._new_node)
            menu.addSeparator()
            menu.addAction("Refresh All", self._populate)
            menu.addSeparator()
            menu.addAction("Export Modules…",
                           lambda: self.set_export_mode(True))
            menu.addSeparator()
            menu.addAction("Expand All", self._tree.expandAll)
            menu.addAction("Collapse All", self._tree.collapseAll)

        elif node_type == "network":
            # ── Control network root ──
            menu.addAction("New Controller Node…", self._new_node)
            menu.addSeparator()
            menu.addAction("Expand All",
                           lambda: self._expand_recursive(item, True))
            menu.addAction("Collapse All",
                           lambda: self._expand_recursive(item, False))
            menu.addSeparator()
            menu.addAction("Refresh", self._populate)

        elif node_type == "node":
            # ── Controller / node ──
            node_id = item.data(0, _ROLE_NODE_ID)
            menu.addAction("Rename Node…",
                           lambda: self._rename_node(node_id))
            menu.addSeparator()
            menu.addAction("Expand All",
                           lambda: self._expand_recursive(item, True))
            menu.addAction("Collapse All",
                           lambda: self._expand_recursive(item, False))
            menu.addSeparator()
            act_del = menu.addAction(
                "Delete Node…", lambda: self._delete_node(node_id))
            act_del.setEnabled(bool(self._project_config.get("nodes")))
            act_del.setData("destructive")

        elif node_type == "unassigned":
            menu.addAction("Expand All",
                           lambda: self._expand_recursive(item, True))
            menu.addAction("Collapse All",
                           lambda: self._expand_recursive(item, False))
            menu.addSeparator()
            menu.addAction("Refresh", self._populate)

        elif node_type == "units":
            menu.addAction("Expand All",
                           lambda: self._expand_recursive(item, True))
            menu.addAction("Collapse All",
                           lambda: self._expand_recursive(item, False))
            menu.addSeparator()
            menu.addAction("Refresh", self._populate)

        elif node_type in {"unit", "unassigned_unit"}:
            area_id = item.data(0, _ROLE_AREA_ID)
            unit_name = item.data(0, _ROLE_UNIT_NAME)
            menu.addAction(
                "New Control Module\u2026",
                lambda: self._new_strategy(area_id, unit_name))
            menu.addAction(
                "Import Module\u2026",
                lambda: self._import_strategy(
                    area_id=area_id, unit_name=unit_name))
            menu.addSeparator()
            menu.addAction(
                "Select All in Unit",
                lambda: self._select_unit(area_id, unit_name, True))
            menu.addAction(
                "Deselect All in Unit",
                lambda: self._select_unit(area_id, unit_name, False))
            menu.addSeparator()
            menu.addAction("Expand All",
                           lambda: self._expand_recursive(item, True))
            menu.addAction("Collapse All",
                           lambda: self._expand_recursive(item, False))
            menu.addSeparator()
            menu.addAction("Refresh", self._populate)

        elif node_type == "area":
            # ── Area node ──
            area_id = item.data(0, _ROLE_AREA_ID)
            new_menu = menu.addMenu("New")
            new_menu.setStyleSheet(_CONTEXT_MENU_STYLE)
            new_menu.addAction("Control Module\u2026",
                               lambda: self._new_strategy(area_id))
            new_menu.addAction("SFC Module\u2026",
                               lambda: self._new_sfc_module(area_id))
            new_menu.addAction("Equipment Module\u2026",
                               lambda: self._new_equipment_module(area_id))
            menu.addSeparator()
            menu.addAction("Rename Area\u2026",
                           lambda: self._rename_area(area_id))
            menu.addSeparator()
            menu.addAction("Expand All",
                           lambda: self._expand_recursive(item, True))
            menu.addAction("Collapse All",
                           lambda: self._expand_recursive(item, False))
            menu.addSeparator()
            # Only allow delete if more than one area
            n_areas = len(self._project_config.get("areas", []))
            act_del = menu.addAction("Delete Area\u2026",
                                     lambda: self._delete_area(area_id))
            act_del.setEnabled(n_areas > 1)
            act_del.setData("destructive")

        elif node_type == "folder":
            # Determine which area this folder belongs to
            area_id = item.data(0, _ROLE_AREA_ID)
            folder_kind = item.data(0, _ROLE_FOLDER_KIND)
            is_cm = folder_kind == "cm"
            is_sfc = folder_kind == "sfc"
            is_em = folder_kind == "equipment"

            if is_cm:
                unit_name = item.data(0, _ROLE_UNIT_NAME)
                menu.addAction("New Control Module\u2026",
                               lambda: self._new_strategy(
                                   area_id, unit_name))
                menu.addAction("Import Module\u2026",
                               lambda: self._import_strategy(
                                   area_id=area_id, unit_name=unit_name))
                menu.addSeparator()
                menu.addAction("Select All in Unit",
                               lambda: self._select_unit(
                                   area_id, unit_name, True))
                menu.addAction("Deselect All in Unit",
                               lambda: self._select_unit(
                                   area_id, unit_name, False))
            elif is_sfc:
                menu.addAction("New SFC Module\u2026",
                               lambda: self._new_sfc_module(area_id))
                menu.addAction("Import SFC Module\u2026",
                               lambda: self._import_strategy(
                                   subfolder="sequence", area_id=area_id))
            elif is_em:
                menu.addAction("New Equipment Module\u2026",
                               lambda: self._new_equipment_module(area_id))
                menu.addAction("Import Equipment Module\u2026",
                               lambda: self._import_equipment_module(area_id))
            else:
                menu.addAction("Expand", lambda: item.setExpanded(True))
                menu.addAction("Collapse", lambda: item.setExpanded(False))

            menu.addSeparator()
            menu.addAction("Refresh", self._populate)

        elif node_type == "equipment":
            # ── Equipment module node ──
            menu.addAction("Open Properties\u2026",
                           lambda: self._edit_equipment_module(item))
            menu.addSeparator()
            menu.addAction("Add Control Module\u2026",
                           lambda: self._add_child_to_em(item, "cm"))
            menu.addAction("Add SFC Module\u2026",
                           lambda: self._add_child_to_em(item, "sfc"))
            menu.addSeparator()
            # Move to area
            area_id = self._get_area_id_for_item(item)
            self._add_move_to_area_menu(menu, item, "equipment_modules", area_id)
            menu.addSeparator()
            menu.addAction("Rename\u2026",
                           lambda: self._rename_equipment_module(item))
            menu.addAction("Duplicate\u2026",
                           lambda: self._duplicate_equipment_module(item))
            menu.addSeparator()
            menu.addAction("Open Containing Folder",
                           lambda: self._open_containing_folder(item))
            menu.addAction("Copy Path", lambda: self._copy_path(item))
            menu.addSeparator()
            act_del = menu.addAction(
                "Delete\u2026",
                lambda: self._delete_equipment_module(item))
            act_del.setData("destructive")

        elif node_type == "em_child":
            # ── Child reference within equipment module ──
            path = item.data(0, _ROLE_FILE_PATH)
            if path:
                menu.addAction(
                    "Open in Designer",
                    lambda: self.strategyLoadRequested.emit(path))
            menu.addSeparator()
            menu.addAction(
                "Remove from Equipment Module",
                lambda: self._remove_child_from_em(item))

        elif node_type == "strategy":
            # ── Strategy / SFC module node ──
            path = item.data(0, _ROLE_FILE_PATH)
            menu.addAction("Open",
                           lambda: self.strategyLoadRequested.emit(path))
            menu.addSeparator()
            # Move to area / assign to controller node
            area_id = self._get_area_id_for_item(item)
            kind = item.data(0, _ROLE_MODULE_KIND) or "cm"
            config_key = "sfc_modules" if kind == "sfc" else "strategies"
            self._add_move_to_area_menu(menu, item, config_key, area_id)
            if kind == "cm" and area_id:
                self._add_move_to_unit_menu(menu, item, area_id)
            self._add_assign_node_menu(menu, item)
            menu.addSeparator()
            menu.addAction("Rename\u2026", lambda: self._rename_strategy(item))
            menu.addAction("Duplicate\u2026",
                           lambda: self._duplicate_strategy(item))
            menu.addSeparator()
            menu.addAction("Open Containing Folder",
                           lambda: self._open_containing_folder(item))
            menu.addAction("Copy Path", lambda: self._copy_path(item))
            menu.addSeparator()
            act_del = menu.addAction(
                "Delete\u2026", lambda: self._delete_strategy(item))
            act_del.setData("destructive")

        if menu.actions():
            menu.exec_transient(self._tree.viewport().mapToGlobal(pos))

    def _expand_recursive(self, item: QTreeWidgetItem, expand: bool):
        """Expand or collapse an item and all its children."""
        item.setExpanded(expand)
        for i in range(item.childCount()):
            self._expand_recursive(item.child(i), expand)

    def _add_move_to_area_menu(self, menu: QMenu, item: QTreeWidgetItem,
                               config_key: str, current_area_id: str | None):
        """Add a 'Move to Area...' submenu for moving items between areas."""
        areas = self._project_config.get("areas", [])
        if len(areas) < 2:
            return
        move_menu = menu.addMenu("Move to Area")
        move_menu.setStyleSheet(_CONTEXT_MENU_STYLE)
        for area in areas:
            aid = area.get("area_id")
            if aid == current_area_id:
                continue
            aname = area.get("name", "Area")
            move_menu.addAction(
                aname,
                lambda _aid=aid, _key=config_key: self._move_item_to_area(
                    item, _key, current_area_id, _aid))

    def _add_move_to_unit_menu(
        self, menu: QMenu, item: QTreeWidgetItem, area_id: str,
    ) -> None:
        """Add the process-unit assignment menu for a control module."""
        area = self._get_area_data(area_id)
        if not area:
            return
        units = [unit for unit in area.get("units") or []
                 if isinstance(unit, dict) and str(unit.get("name") or "").strip()]
        if not units:
            return
        current = item.data(0, _ROLE_UNIT_NAME)
        if current is None:
            current = self._module_unit_membership(
                area, module_display_name(item.data(0, _ROLE_FILE_PATH)))

        sub = menu.addMenu("Move to Unit")
        sub.setStyleSheet(_CONTEXT_MENU_STYLE)
        for unit in units:
            unit_name = str(unit.get("name") or "").strip()
            action = sub.addAction(
                unit_name,
                lambda _checked=False, _unit=unit_name: self._move_strategy_to_unit(
                    item, area_id, _unit))
            action.setCheckable(True)
            action.setChecked(unit_name == current)
        sub.addSeparator()
        action = sub.addAction(
            "Unassigned",
            lambda: self._move_strategy_to_unit(item, area_id, ""))
        action.setCheckable(True)
        action.setChecked(not current)

    @staticmethod
    def _module_reference_matches(reference: object, module_name: str) -> bool:
        text = str(reference or "").strip().replace("\\", "/")
        wanted = module_name.strip().casefold()
        return bool(text) and (
            text.casefold() == wanted or Path(text).stem.casefold() == wanted)

    def _module_unit_membership(
        self, area: dict, module_name: str,
    ) -> str:
        for unit in area.get("units") or []:
            if not isinstance(unit, dict):
                continue
            if any(self._module_reference_matches(ref, module_name)
                   for ref in unit.get("modules") or []):
                return str(unit.get("name") or "").strip()
        return ""

    @staticmethod
    def _declared_module_unit(path: Path | str) -> str:
        """Read a generated module's advisory process-unit metadata."""
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, TypeError, json.JSONDecodeError):
            return ""
        if not isinstance(document, dict):
            return ""
        metadata = document.get("metadata")
        if isinstance(metadata, dict):
            return str(metadata.get("unit") or "").strip()
        return ""

    def _set_module_unit_membership(
        self, area: dict, module_name: str, unit_name: str | None,
    ) -> None:
        """Set unit membership without changing the executable module file."""
        target = str(unit_name or "").strip()
        target_unit = None
        for unit in area.get("units") or []:
            if not isinstance(unit, dict):
                continue
            modules = unit.setdefault("modules", [])
            modules[:] = [
                ref for ref in modules
                if not self._module_reference_matches(ref, module_name)
            ]
            if str(unit.get("name") or "").strip() == target:
                target_unit = unit
        if target and target_unit is not None:
            target_unit.setdefault("modules", []).append(module_name)

    def _move_strategy_to_unit(
        self, item: QTreeWidgetItem, area_id: str, unit_name: str,
    ) -> None:
        path = item.data(0, _ROLE_FILE_PATH)
        area = self._get_area_data(area_id)
        if not path or not area:
            return
        self._set_module_unit_membership(
            area, module_display_name(path), unit_name)
        self._save_config()
        self._populate()

    def _move_item_to_area(self, item: QTreeWidgetItem, config_key: str,
                           from_area_id: str, to_area_id: str):
        """Move an item from one area to another in the config."""
        path = item.data(0, _ROLE_FILE_PATH)
        if not path:
            return
        try:
            rel = str(Path(path).relative_to(_strategy_dir())).replace(
                "\\", "/")
        except ValueError:
            rel = path

        # Remove from source area
        from_area = self._get_area_data(from_area_id)
        if from_area and rel in from_area.get(config_key, []):
            from_area[config_key].remove(rel)
        if from_area and config_key == "strategies":
            self._set_module_unit_membership(
                from_area, module_display_name(path), None)

        # Add to target area
        to_area = self._get_area_data(to_area_id)
        if to_area:
            to_area.setdefault(config_key, []).append(rel)

        self._save_config()
        self._populate()

    # ── Node assignment actions ───────────────────────────────────────

    def _add_assign_node_menu(self, menu: QMenu, item: QTreeWidgetItem):
        """Add an 'Assign to Node' submenu for a module."""
        path = item.data(0, _ROLE_FILE_PATH)
        if not path:
            return
        rel = self._rel_for_path(path)
        current = self._assigned_node_id(rel)

        sub = menu.addMenu("Assign to Node")
        sub.setStyleSheet(_CONTEXT_MENU_STYLE)
        for nd in self.nodes():
            nid = str(nd.get("node_id", ""))
            act = sub.addAction(
                nd.get("name", "Node"),
                lambda _nid=nid: self._assign_to_node(item, _nid))
            act.setCheckable(True)
            act.setChecked(nid == current)
        sub.addSeparator()
        act_un = sub.addAction("Unassigned",
                               lambda: self._assign_to_node(item, ""))
        act_un.setCheckable(True)
        act_un.setChecked(current == "")
        sub.addSeparator()
        sub.addAction("New Node…", lambda: self._assign_to_new_node(item))

    def _rel_for_path(self, path: Path | str) -> str:
        try:
            return str(Path(path).relative_to(_strategy_dir())).replace(
                "\\", "/")
        except ValueError:
            return str(path)

    def _materialize_nodes(self):
        """Persist the synthesized default node + current assignments.

        Called before the first explicit assignment so that pinning one
        module does not silently orphan the other 21 (which would happen if
        ``nodes`` appeared in the config while ``assignments`` covered only
        the module just touched).
        """
        if self._project_config.get("nodes"):
            return
        self._project_config["nodes"] = self.nodes()
        assignments = self._project_config.setdefault("assignments", {})
        for mod in self._modules:
            assignments.setdefault(self._norm_rel(mod["rel"]), _DEFAULT_NODE_ID)

    def _assign_to_node(self, item: QTreeWidgetItem, node_id: str):
        """Assign a module to a controller node (``""`` = unassigned)."""
        path = item.data(0, _ROLE_FILE_PATH)
        if not path:
            return
        self._materialize_nodes()
        rel = self._norm_rel(self._rel_for_path(path))
        assignments = self._project_config.setdefault("assignments", {})
        if node_id:
            assignments[rel] = node_id
        else:
            assignments[rel] = ""
        self._save_config()
        self.refresh()

    def _assign_to_new_node(self, item: QTreeWidgetItem):
        node_id = self._new_node()
        if node_id:
            self._assign_to_node(item, node_id)

    def _new_node(self) -> str | None:
        """Create a controller node; returns its node_id."""
        name, ok = QInputDialog.getText(
            self, "New Controller Node", "Node name:", text="CTLR-2")
        if not ok or not name.strip():
            return None
        name = name.strip()
        self._materialize_nodes()
        nodes = self._project_config.setdefault("nodes", [])
        if any(n.get("name") == name for n in nodes):
            QMessageBox.warning(self, "Already Exists",
                                f"A node named '{name}' already exists.")
            return None
        node_id = uuid.uuid4().hex[:12]
        nodes.append({"node_id": node_id, "name": name, "type": "controller",
                      "description": ""})
        self._save_config()
        self.refresh()
        return node_id

    def _rename_node(self, node_id: str):
        self._materialize_nodes()
        nodes = self._project_config.get("nodes", [])
        target = next((n for n in nodes if n.get("node_id") == node_id), None)
        if target is None:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename Node", "New name:", text=target.get("name", ""))
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if any(n.get("name") == new_name and n is not target for n in nodes):
            QMessageBox.warning(self, "Already Exists",
                                f"A node named '{new_name}' already exists.")
            return
        target["name"] = new_name
        self._save_config()
        self.refresh()

    def _delete_node(self, node_id: str):
        """Delete a controller node; its modules become unassigned."""
        nodes = self._project_config.get("nodes", [])
        target = next((n for n in nodes if n.get("node_id") == node_id), None)
        if target is None:
            return
        reply = QMessageBox.question(
            self, "Delete Node",
            f"Delete node '{target.get('name', node_id)}'?\n\n"
            f"Its modules become unassigned.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        nodes[:] = [n for n in nodes if n.get("node_id") != node_id]
        assignments = self._project_config.get("assignments", {})
        for rel, nid in list(assignments.items()):
            if nid == node_id:
                assignments[rel] = ""
        self._save_config()
        self.refresh()

    def _select_area(self, area_id: str, checked: bool):
        """Check/uncheck all strategy nodes in an area."""
        self.set_export_mode(True)
        cm_folder = self._area_cm_folders.get(area_id)
        if not cm_folder:
            return
        self._tree.blockSignals(True)
        state = Qt.Checked if checked else Qt.Unchecked
        self._set_check_recursive(cm_folder, state)
        self._tree.blockSignals(False)
        self._update_selection_count()

    def _select_unit(
        self, area_id: str, unit_name: str | None, checked: bool,
    ) -> None:
        """Check or uncheck the modules below one process unit."""
        self.set_export_mode(True)
        folder = self._unit_cm_folders.get(
            (str(area_id), str(unit_name or "")))
        if not folder:
            return
        self._tree.blockSignals(True)
        state = Qt.Checked if checked else Qt.Unchecked
        self._set_check_recursive(folder, state)
        self._tree.blockSignals(False)
        self._update_selection_count()

    # ── Area Actions ─────────────────────────────────────────────────

    def _new_area(self):
        """Create a new empty area."""
        name, ok = QInputDialog.getText(
            self, "New Area",
            "Area name:",
            text="New Area")
        if not ok or not name.strip():
            return

        name = name.strip()
        if name in self._get_area_names():
            QMessageBox.warning(
                self, "Already Exists",
                f"An area named '{name}' already exists.")
            return

        area_data = {
            "name": name,
            "area_id": uuid.uuid4().hex[:12],
            "description": "",
            "strategies": [],
            "sfc_modules": [],
            "equipment_modules": [],
        }
        self._project_config.setdefault("areas", []).append(area_data)
        self._save_config()
        self._populate()

    def _rename_area(self, area_id: str):
        """Rename an area."""
        area = self._get_area_data(area_id)
        if not area:
            return
        old_name = area.get("name", "")
        new_name, ok = QInputDialog.getText(
            self, "Rename Area",
            "New name:", text=old_name)
        if not ok or not new_name.strip():
            return

        new_name = new_name.strip()
        existing = self._get_area_names()
        if new_name in existing and new_name != old_name:
            QMessageBox.warning(
                self, "Already Exists",
                f"An area named '{new_name}' already exists.")
            return

        area["name"] = new_name
        self._save_config()
        self._populate()

    def _delete_area(self, area_id: str):
        """Delete an area, moving its contents to another area."""
        area = self._get_area_data(area_id)
        if not area:
            return

        areas = self._project_config.get("areas", [])
        if len(areas) <= 1:
            QMessageBox.warning(
                self, "Cannot Delete",
                "Cannot delete the last remaining area.")
            return

        name = area.get("name", "Area")
        # Find a target area for orphaned items
        other_areas = [a for a in areas if a.get("area_id") != area_id]
        target = other_areas[0]

        has_items = (area.get("strategies") or area.get("sfc_modules")
                     or area.get("equipment_modules"))
        msg = f"Delete area '{name}'?"
        if has_items:
            msg += (f"\n\nIts contents will be moved to "
                    f"'{target.get('name', 'Area')}'.")

        reply = QMessageBox.question(
            self, "Delete Area", msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        # Move contents to target
        for key in ("strategies", "sfc_modules", "equipment_modules"):
            items = area.get(key, [])
            target.setdefault(key, []).extend(items)

        # Remove area
        areas[:] = [a for a in areas if a.get("area_id") != area_id]
        self._save_config()
        self._populate()

    # ── Strategy Actions ──────────────────────────────────────────────

    def _new_strategy(
        self, area_id: str | None = None, unit_name: str | None = None,
    ):
        """Create a new empty strategy in the selected area and unit."""
        name, ok = QInputDialog.getText(
            self, "New Control Module",
            "Strategy name:",
            text="New_Strategy")
        if not ok or not name.strip():
            return

        safe_name = name.strip().replace(" ", "_").replace("/", "_")
        path = _strategy_dir() / f"{safe_name}.json"
        if path.exists():
            QMessageBox.warning(
                self, "Already Exists",
                f"A strategy named '{safe_name}' already exists.")
            return

        _strategy_dir().mkdir(parents=True, exist_ok=True)
        data = {"name": name.strip(), "blocks": [], "wires": []}
        if unit_name:
            data["metadata"] = {"unit": str(unit_name)}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        # Add to area config
        rel = f"{safe_name}.json"
        if area_id:
            area = self._get_area_data(area_id)
            if area:
                area.setdefault("strategies", []).append(rel)
                self._set_module_unit_membership(
                    area, name.strip(), unit_name)
                self._save_config()
        else:
            # Add to first area
            areas = self._project_config.get("areas", [])
            if areas:
                areas[0].setdefault("strategies", []).append(rel)
                self._set_module_unit_membership(
                    areas[0], name.strip(), unit_name)
                self._save_config()

        self._populate()
        self.strategyLoadRequested.emit(str(path))

    def _new_sfc_module(self, area_id: str | None = None):
        """Create a new empty SFC module in the sequence subfolder."""
        name, ok = QInputDialog.getText(
            self, "New SFC Module",
            "SFC module name:",
            text="New_SFC_Sequence")
        if not ok or not name.strip():
            return

        safe_name = name.strip().replace(" ", "_").replace("/", "_")
        seq_dir = _strategy_dir() / "sequence"
        seq_dir.mkdir(parents=True, exist_ok=True)
        path = seq_dir / f"{safe_name}.json"
        if path.exists():
            QMessageBox.warning(
                self, "Already Exists",
                f"An SFC module named '{safe_name}' already exists.")
            return

        data = {
            "name": name.strip(),
            "description": "SFC sequential function chart",
            "blocks": [],
            "wires": [],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        # Add to area config
        rel = f"sequence/{safe_name}.json"
        if area_id:
            area = self._get_area_data(area_id)
            if area:
                area.setdefault("sfc_modules", []).append(rel)
                self._save_config()
        else:
            areas = self._project_config.get("areas", [])
            if areas:
                areas[0].setdefault("sfc_modules", []).append(rel)
                self._save_config()

        self._populate()
        self.strategyLoadRequested.emit(str(path))

    def _rename_strategy(self, item: QTreeWidgetItem):
        """Rename a strategy file."""
        path = Path(item.data(0, _ROLE_FILE_PATH))
        from azeo_control_trainer.core.configuration.workspace import draft_root
        if draft_root(path):
            # A file-only rename would lose the repository identity and leave
            # graphics consumers pointing at the old module address.
            rename = getattr(self.window(), "_configuration_rename", None)
            if callable(rename):
                rename(path)
            else:
                from azeo_control_trainer.core.presentation.headless import is_headless
                if not is_headless():
                    QMessageBox.information(self, "Shared module rename",
                                            "Open this draft through Shared editing to review its rename consumers.")
            return
        # Prefill with the module's real name, not a de-underscored filename —
        # accepting the prefill used to rewrite FIC-101 as "FIC 101".
        old_name = module_display_name(path)

        new_name, ok = QInputDialog.getText(
            self, "Rename Strategy",
            "New name:", text=old_name)
        if not ok or not new_name.strip():
            return

        safe_name = new_name.strip().replace(" ", "_").replace("/", "_")
        new_path = path.parent / f"{safe_name}.json"
        if new_path.exists() and new_path != path:
            QMessageBox.warning(
                self, "Already Exists",
                f"A strategy named '{safe_name}' already exists.")
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["name"] = new_name.strip()
            with open(new_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            if new_path != path:
                path.unlink()
            set_last_strategy(new_path)
        except Exception as e:
            QMessageBox.critical(self, "Rename Error", str(e))
            return

        # Update area config
        area_id = self._get_area_id_for_item(item)
        area = self._get_area_data(area_id) if area_id else None
        if area:
            current_unit = self._module_unit_membership(area, old_name)
            self._set_module_unit_membership(area, old_name, None)
            self._set_module_unit_membership(
                area, new_name.strip(), current_unit)
        self._update_path_in_config(path, new_path)
        self._populate()

    def _delete_strategy(self, item: QTreeWidgetItem):
        """Delete a strategy file."""
        path = Path(item.data(0, _ROLE_FILE_PATH))
        module_name = module_display_name(path)
        reply = QMessageBox.question(
            self, "Delete Strategy",
            f"Delete '{path.stem}'?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        try:
            path.unlink()
        except Exception as e:
            QMessageBox.critical(self, "Delete Error", str(e))
            return

        # Remove from area config
        self._remove_path_from_config(path, module_name=module_name)
        self._populate()
        self.strategyDeleteRequested.emit(str(path))

    def _duplicate_strategy(self, item: QTreeWidgetItem):
        """Create a copy of the strategy file."""
        src = Path(item.data(0, _ROLE_FILE_PATH))
        base_name = src.stem
        counter = 1
        while True:
            new_name = f"{base_name}_Copy{counter if counter > 1 else ''}"
            dest = src.parent / f"{new_name}.json"
            if not dest.exists():
                break
            counter += 1

        try:
            with open(src, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["name"] = new_name          # verbatim — no "_" → " " mangling
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "Duplicate Error", str(e))
            return

        # Add to same area
        area_id = self._get_area_id_for_item(item)
        if area_id:
            try:
                rel = str(dest.relative_to(_strategy_dir())).replace(
                    "\\", "/")
            except ValueError:
                rel = str(dest)
            area = self._get_area_data(area_id)
            if area:
                kind = item.data(0, _ROLE_MODULE_KIND) or "cm"
                key = "sfc_modules" if kind == "sfc" else "strategies"
                area.setdefault(key, []).append(rel)
                if kind == "cm":
                    current_unit = item.data(0, _ROLE_UNIT_NAME)
                    if current_unit is None:
                        current_unit = self._module_unit_membership(
                            area, module_display_name(src))
                    self._set_module_unit_membership(
                        area, new_name, current_unit)
                self._save_config()

        self._populate()
        self.strategyLoadRequested.emit(str(dest))

    def _import_strategy(
        self, subfolder: str = "", area_id: str | None = None,
        unit_name: str | None = None,
    ):
        """Import a strategy .json file from an external location."""
        src_path, _ = QFileDialog.getOpenFileName(
            self, "Import Strategy",
            str(Path.home()),
            "Strategy JSON (*.json)")
        if not src_path:
            return

        src = Path(src_path)
        if subfolder:
            dest_dir = _strategy_dir() / subfolder
        else:
            dest_dir = _strategy_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name

        if dest.exists():
            reply = QMessageBox.question(
                self, "File Exists",
                f"'{src.name}' already exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        try:
            shutil.copy2(str(src), str(dest))
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))
            return

        # Add to area config
        try:
            rel = str(dest.relative_to(_strategy_dir())).replace("\\", "/")
        except ValueError:
            rel = str(dest)

        target_area_id = area_id
        if not target_area_id:
            areas = self._project_config.get("areas", [])
            if areas:
                target_area_id = areas[0].get("area_id")

        if target_area_id:
            area = self._get_area_data(target_area_id)
            if area:
                key = "sfc_modules" if subfolder == "sequence" else "strategies"
                if rel not in area.get(key, []):
                    area.setdefault(key, []).append(rel)
                if key == "strategies":
                    module_name = module_display_name(dest)
                    target_unit = (
                        unit_name if unit_name is not None
                        else self._declared_module_unit(dest)
                    )
                    self._set_module_unit_membership(
                        area, module_name, target_unit)
                self._save_config()

        self._populate()
        self.strategyLoadRequested.emit(str(dest))

    # ── Config path management ────────────────────────────────────────

    def _update_path_in_config(self, old_path: Path, new_path: Path):
        """Update a file path in the area config after rename."""
        try:
            old_rel = str(old_path.relative_to(_strategy_dir())).replace(
                "\\", "/")
            new_rel = str(new_path.relative_to(_strategy_dir())).replace(
                "\\", "/")
        except ValueError:
            return

        for area in self._project_config.get("areas", []):
            for key in ("strategies", "sfc_modules", "equipment_modules"):
                refs = area.get(key, [])
                for i, ref in enumerate(refs):
                    if str(ref).replace("\\", "/") == old_rel:
                        refs[i] = new_rel
                        break
        self._save_config()

    def _remove_path_from_config(
        self, path: Path, module_name: str | None = None,
    ):
        """Remove a file path from all areas in the config."""
        try:
            rel = str(path.relative_to(_strategy_dir())).replace("\\", "/")
        except ValueError:
            return

        for area in self._project_config.get("areas", []):
            for key in ("strategies", "sfc_modules", "equipment_modules"):
                refs = area.get(key, [])
                refs[:] = [ref for ref in refs
                           if str(ref).replace("\\", "/") != rel]
            if module_name:
                self._set_module_unit_membership(area, module_name, None)
        self._save_config()

    # ── File system helpers ──────────────────────────────────────────

    def _open_containing_folder(self, item: QTreeWidgetItem):
        """Open the file's parent folder in the OS file manager."""
        path = Path(item.data(0, _ROLE_FILE_PATH))
        import subprocess
        import sys
        folder = str(path.parent)
        if sys.platform == "win32":
            import os
            shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
            subprocess.Popen([str(shell), "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", folder])

    def _copy_path(self, item: QTreeWidgetItem):
        """Copy the file path to the clipboard."""
        path = item.data(0, _ROLE_FILE_PATH)
        if path:
            QApplication.clipboard().setText(str(path))
            item.setToolTip(0, "Path copied!")

    # ── Equipment Module Actions ──────────────────────────────────────

    def _new_equipment_module(self, area_id: str | None = None):
        """Create a new equipment module via the properties dialog."""
        from azeo_control_trainer.azeo_control_designer.dialogs.equipment_module_dialog import (
            EquipmentModuleDialog,
        )
        dlg = EquipmentModuleDialog(existing_data=None, parent=self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)

        def _on_accepted():
            data = dlg.get_data()
            em_path = save_equipment_module(data)
            # Add to area config
            target = area_id
            if not target:
                areas = self._project_config.get("areas", [])
                if areas:
                    target = areas[0].get("area_id")
            if target and em_path:
                area = self._get_area_data(target)
                if area:
                    try:
                        rel = str(em_path.relative_to(_strategy_dir())).replace(
                            "\\", "/")
                    except (ValueError, TypeError):
                        rel = str(em_path)
                    area.setdefault("equipment_modules", []).append(rel)
                    self._save_config()
            self._populate()

        dlg.accepted.connect(_on_accepted)
        dlg.show()

    def _edit_equipment_module(self, item: QTreeWidgetItem):
        """Open equipment module properties dialog for editing."""
        path = Path(item.data(0, _ROLE_FILE_PATH))
        try:
            data = load_equipment_module(path)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))
            return

        from azeo_control_trainer.azeo_control_designer.dialogs.equipment_module_dialog import (
            EquipmentModuleDialog,
        )
        dlg = EquipmentModuleDialog(existing_data=data, parent=self)
        dlg.setAttribute(Qt.WA_DeleteOnClose)

        def _on_accepted():
            updated = dlg.get_data()
            save_equipment_module(updated, path)
            self._populate()

        dlg.accepted.connect(_on_accepted)
        dlg.show()

    def _delete_equipment_module(self, item: QTreeWidgetItem):
        """Delete an equipment module file."""
        path = Path(item.data(0, _ROLE_FILE_PATH))
        reply = QMessageBox.question(
            self, "Delete Equipment Module",
            f"Delete '{path.stem}'?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            path.unlink()
        except Exception as e:
            QMessageBox.critical(self, "Delete Error", str(e))
            return
        self._remove_path_from_config(path)
        self._populate()

    def _rename_equipment_module(self, item: QTreeWidgetItem):
        """Rename an equipment module."""
        path = Path(item.data(0, _ROLE_FILE_PATH))
        try:
            data = load_equipment_module(path)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))
            return

        old_name = data.get("name", path.stem)
        new_name, ok = QInputDialog.getText(
            self, "Rename Equipment Module",
            "New name:", text=old_name)
        if not ok or not new_name.strip():
            return

        safe_name = new_name.strip().replace(" ", "_").replace("/", "_")
        new_path = path.parent / f"{safe_name}.json"
        if new_path.exists() and new_path != path:
            QMessageBox.warning(
                self, "Already Exists",
                f"An equipment module named '{safe_name}' already exists.")
            return

        data["name"] = new_name.strip()
        try:
            save_equipment_module(data, new_path)
            if new_path != path:
                path.unlink()
        except Exception as e:
            QMessageBox.critical(self, "Rename Error", str(e))
            return

        self._update_path_in_config(path, new_path)
        self._populate()

    def _duplicate_equipment_module(self, item: QTreeWidgetItem):
        """Duplicate an equipment module."""
        src = Path(item.data(0, _ROLE_FILE_PATH))
        try:
            data = load_equipment_module(src)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))
            return

        counter = 1
        while True:
            new_name = f"{src.stem}_Copy{counter if counter > 1 else ''}"
            dest = src.parent / f"{new_name}.json"
            if not dest.exists():
                break
            counter += 1

        data["name"] = new_name.replace("_", " ")
        try:
            save_equipment_module(data, dest)
        except Exception as e:
            QMessageBox.critical(self, "Duplicate Error", str(e))
            return

        # Add to same area
        area_id = self._get_area_id_for_item(item)
        if area_id:
            area = self._get_area_data(area_id)
            if area:
                try:
                    rel = str(dest.relative_to(_strategy_dir())).replace(
                        "\\", "/")
                except ValueError:
                    rel = str(dest)
                area.setdefault("equipment_modules", []).append(rel)
                self._save_config()

        self._populate()

    def _import_equipment_module(self, area_id: str | None = None):
        """Import an equipment module JSON from an external location."""
        src_path, _ = QFileDialog.getOpenFileName(
            self, "Import Equipment Module",
            str(Path.home()),
            "Equipment Module JSON (*.json)")
        if not src_path:
            return
        src = Path(src_path)
        eq_dir = _strategy_dir() / "equipment"
        eq_dir.mkdir(parents=True, exist_ok=True)
        dest = eq_dir / src.name

        if dest.exists():
            reply = QMessageBox.question(
                self, "File Exists",
                f"'{src.name}' already exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        try:
            shutil.copy2(str(src), str(dest))
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))
            return

        # Add to area config
        target = area_id
        if not target:
            areas = self._project_config.get("areas", [])
            if areas:
                target = areas[0].get("area_id")
        if target:
            area = self._get_area_data(target)
            if area:
                try:
                    rel = str(dest.relative_to(_strategy_dir())).replace(
                        "\\", "/")
                except ValueError:
                    rel = str(dest)
                if rel not in area.get("equipment_modules", []):
                    area.setdefault("equipment_modules", []).append(rel)
                    self._save_config()

        self._populate()

    def _add_child_to_em(self, em_item: QTreeWidgetItem, child_type: str):
        """Quick-add a CM or SFC reference to an equipment module."""
        path = Path(em_item.data(0, _ROLE_FILE_PATH))
        try:
            data = load_equipment_module(path)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))
            return

        folders = list_strategy_folders()
        choices = []
        for folder_name, paths in sorted(folders.items()):
            if child_type == "sfc" and folder_name != "sequence":
                continue
            if child_type == "cm" and folder_name == "sequence":
                continue
            for p in paths:
                rel = str(p.relative_to(_strategy_dir())).replace("\\", "/")
                display = module_display_name(p)
                if folder_name:
                    display = f"[{folder_name}] {display}"
                choices.append((display, rel))

        if not choices:
            QMessageBox.information(
                self, "No Modules",
                f"No {'SFC' if child_type == 'sfc' else 'control'} modules found.")
            return

        display_list = [c[0] for c in choices]
        selected, ok = QInputDialog.getItem(
            self, f"Add {'SFC' if child_type == 'sfc' else 'Control'} Module",
            "Select module:", display_list, 0, False)
        if not ok:
            return

        idx = display_list.index(selected)
        rel_path = choices[idx][1]

        key = "sfc_modules" if child_type == "sfc" else "control_modules"
        if rel_path not in data.get(key, []):
            data.setdefault(key, []).append(rel_path)
            save_equipment_module(data, path)
            self._populate()

    def _remove_child_from_em(self, child_item: QTreeWidgetItem):
        """Remove a CM/SFC reference from its parent equipment module."""
        parent = child_item.parent()
        if not parent or parent.data(0, _ROLE_NODE_TYPE) != "equipment":
            return

        em_path = Path(parent.data(0, _ROLE_FILE_PATH))
        child_path_str = child_item.data(0, _ROLE_FILE_PATH)

        try:
            data = load_equipment_module(em_path)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))
            return

        if child_path_str:
            child_path = Path(child_path_str)
            try:
                rel = str(child_path.relative_to(_strategy_dir())).replace(
                    "\\", "/")
            except ValueError:
                rel = child_path_str

            for key in ("control_modules", "sfc_modules"):
                refs = data.get(key, [])
                for r in list(refs):
                    if r == rel or Path(r).name == child_path.name:
                        refs.remove(r)
                        break

        save_equipment_module(data, em_path)
        self._populate()
