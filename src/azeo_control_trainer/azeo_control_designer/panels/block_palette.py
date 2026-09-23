"""Block palette — categorized tree of available function block types.

Users drag blocks from the palette onto the strategy canvas.
Categories are collapsible/expandable with block counts.

Enhanced with Honeywell Control Builder-style features:
  - Favorites section (starred blocks for quick access)
  - Recently Used section (last N blocks placed)
  - Rich block preview tooltips (terminal diagram, description, config params)

Azeo familiarity (presentation layer only — the model is untouched):
  - Blocks are grouped into the Azeo Control Designer *palettes*
    (I/O, Analog Control, Math, Timer/Counter, Energy Metering, Logical,
    Advanced Control, Advanced, Special Items) via ``_PALETTE_OF``.
    Product-specific helpers are kept in a separate Azeo Utilities palette,
    so they cannot be mistaken for native Azeo special items.
    Anything unmapped — including future blocks — falls back to its
    ``BlockCategory`` so nothing can ever go missing from the tree.
  - Every row shows the Azeo mnemonic, and the search box matches the
    display name, the ``block_type`` id *and* the mnemonic, so typing
    ``SCLR`` / ``OND`` / ``RTO`` finds Scaler / Timer On-Delay / Ratio.

The ``block_type`` ids themselves are NEVER renamed — shipped strategy
JSON files reference them.  The mnemonics below are search/display aliases
transcribed from ``doc/AZEO_FUNCTION_BLOCKS.md`` headings
(``## <Name> function block (<MNEMONIC>)``).
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QMimeData, QByteArray, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.strategy.model.block_base import BlockCategory
from azeo_control_trainer.core.strategy.model.block_registry import registry
from azeo_control_trainer.core.strategy.block_catalog import (
    AZEO_MNEMONICS as _PRIMARY_MNEMONICS,
)
from azeo_control_trainer.config.paths import data_dir

log = logging.getLogger("strategy.palette")

# Persistent settings file for favorites
_PALETTE_SETTINGS = data_dir() / "palette_settings.json"

# Maximum recently used entries
_MAX_RECENT = 8

# ─────────────────────────────────────────────────────────────────────
#  Azeo mnemonic aliases  (mnemonic → our block_type)
#
#  Sourced from doc/AZEO_FUNCTION_BLOCKS.md "## <Name> function block
#  (<MNEMONIC>)" headings.  Our block_type ids are deliberately NOT the
#  Azeo mnemonics (SCALER vs SCLR, TIMER_ON vs OND, RATIO vs RTO) and
#  must not be renamed — strategy JSONs depend on them — so the mnemonics
#  live here as search/display aliases instead.
#
#  Manual mnemonics with no counterpart in this platform are omitted:
#  MPC_SIM, FLC, NN, DIAG, INSPECT.
# ─────────────────────────────────────────────────────────────────────
_LEGACY_AZEO_MNEMONICS: dict[str, str] = {
    # ── I/O blocks (analog / pulse) ──
    "AI": "AI",
    "AO": "AO",
    "PIN": "PIN",
    "FFMAI_RMT": "AI",        # fieldbus multiplexed variant of AI
    "FFMAO": "AO",            # fieldbus multiplexed variant of AO
    # ── I/O blocks (discrete) ──
    "DI": "DI",
    "DO": "DO",
    "FFMDI": "DI",
    "FFMDI_STD": "DI",
    "FFMDO": "DO",
    "FFMDO_STD": "DO",
    "SHDI": "DI",             # smart HART discrete input
    "SHDO": "DO",             # smart HART discrete output
    # ── Analog control (regulatory core) ──
    "PID": "PID",
    "RTO": "RATIO",
    "BG": "BIAS",
    "SCLR": "SCALER",
    # ── Analog control (selectors / calc / split) ──
    "CALC": "EXPRESSION",
    "CTLSL": "CTLSL",
    "ISEL": "ISEL",
    "FFISELX": "ISEL",
    "SGSL": "SGSL",
    "SPLTR": "SPLITTER",
    "MANLD": "MANLD",
    # ── Analog control (dynamics / signal shaping) ──
    "DEADTIME": "DEADTIME",
    "FLTR": "FILTER",
    "LL": "LEAD_LAG",
    "LIM": "LIMITER",
    "RAMP": "RAMP",
    "RTLM": "RATE_LIMITER",
    "SGCR": "SIGNAL_CHAR",
    "SGGN": "SGGN",
    "AT": "AT",
    # ── Math ──
    "ABS": "ABS",
    "ADD": "SUMMER",
    "ARITH": "ARITH",
    "CMP": "COMPARATOR",
    "DIV": "DIVIDER",
    "INT": "INTEGRATOR",
    "MLTY": "MULTIPLIER",
    "SUB": "SUB",
    # ── Timer / counter ──
    "CTR": "COUNTER",
    "DTE": "DTE",
    "OFFD": "TIMER_OFF",
    "OND": "TIMER_ON",
    "RET": "RET",
    "TP": "PULSE",
    # ── Energy metering ──
    "AGA_SI": "AGA_SI",
    "AGA_US": "AGA_US",
    "ISE": "ISE",
    "SST": "SST",
    "TSS": "TSS",
    "SDR": "SDR",
    "STM": "STM",
    "WTH": "WTH",
    "WTS": "WTS",
    # ── Logical ──
    "AND": "AND",
    "OR": "OR",
    "NOT": "NOT",
    "BDE": "BDE",
    "PDE": "POS_EDGE",
    "NDE": "NEG_EDGE",
    "SR": "SR_LATCH",
    "RS": "RS_LATCH",
    "XFR": "TRANSFER",
    "MLTX": "MUX",
    "BFI": "BFI",
    "BFO": "BFO",
    "CND": "CND",
    "ACT": "ACT",
    "DCC": "DCC",
    # ── Device control ──
    "DC": "DEVCTL",
    "EDC": "EDC",
    # ── Advanced control ──
    "MPC": "DMC_CONTROLLER",
    "MPCPro": "APC_CONTROL",
    "MPCPlus": "APC_CONTROL",
    "LE": "LE",
    # ── Advanced (voters / sequencing) ──
    "AVTR": "AVTR",
    "DVTR": "DVTR",
    "CEM": "CEM",
    "SEQ": "SEQ",
    "STD": "STD",
}

# The full 97-entry inventory is model-owned and Qt-free.  Keep this public
# name for existing palette/test callers while making the primary catalog the one
# source of truth.  The old literal above remains a readable migration record
# until downstream documentation no longer refers to its line-by-line layout.
AZEO_MNEMONICS: dict[str, str] = dict(_PRIMARY_MNEMONICS)


def _build_block_mnemonics() -> dict[str, tuple[str, ...]]:
    """Reverse AZEO_MNEMONICS → block_type → mnemonics (exact match first)."""
    rev: dict[str, list[str]] = {}
    for mnemonic, block_type in AZEO_MNEMONICS.items():
        rev.setdefault(block_type, []).append(mnemonic)
    return {
        bt: tuple(sorted(mns, key=lambda m: (m != bt, m)))
        for bt, mns in rev.items()
    }


#: block_type → Azeo mnemonics (primary mnemonic first)
BLOCK_MNEMONICS: dict[str, tuple[str, ...]] = _build_block_mnemonics()


def mnemonic_for(block_type: str) -> str:
    """Primary Azeo mnemonic for a block type ("" if none)."""
    mns = BLOCK_MNEMONICS.get(block_type)
    return mns[0] if mns else ""


# ─────────────────────────────────────────────────────────────────────
#  Azeo Control Designer palettes  (presentation grouping only)
#
#  BlockCategory on the block classes is model state that other code
#  reads — it is NOT touched here.  This is purely how the palette tree
#  is laid out, so a Azeo-trained engineer finds blocks where the
#  manual's palettes put them (energy metering together, timers in
#  Timer/Counter, device control in Special Items, …).
# ─────────────────────────────────────────────────────────────────────
PALETTE_IO       = "I/O"
PALETTE_ANALOG   = "Analog Control"
PALETTE_MATH     = "Math"
PALETTE_TIMER    = "Timer/Counter"
PALETTE_ENERGY   = "Energy Metering"
PALETTE_LOGICAL  = "Logical"
PALETTE_ADVCTRL  = "Advanced Control"
PALETTE_ADVANCED = "Advanced"
PALETTE_SPECIAL  = "Special Items"
PALETTE_AZEO     = "Azeo Utilities"

#: Azeo palette → block_types assigned to it
PALETTE_BLOCKS: dict[str, tuple[str, ...]] = {
    PALETTE_IO: (
        "ALARM_DET", "AI", "AO", "DI", "DO", "PIN",
        "TAGAI", "TAGAO", "TAGDI", "TAGDO", "TAGIO",
        "INPORT", "OUTPORT", "SETPOINT", "MSG", "DATALOG",
    ),
    PALETTE_ANALOG: (
        # regulatory core
        "PID", "RATIO", "BIAS", "SCALER", "ONOFF", "GAIN_SCHED", "RAMP_SOAK",
        # selectors / split / manual
        "CTLSL", "ECTLSL", "ISEL", "SGSL", "SPLITTER", "MANLD",
        "SWITCH", "MODE_SWITCH",
        "MIN_SELECT", "MAX_SELECT", "MID_SELECT", "AVG_SELECT",
        # dynamics / signal shaping
        "DEADTIME", "FILTER", "LEAD_LAG", "LIMITER", "RAMP", "ERAMP",
        "RATE_LIMITER",
        "SIGNAL_CHAR", "SGGN", "AT", "DEADBAND", "MOVING_AVG", "DERIVATIVE",
    ),
    PALETTE_MATH: (
        "ABS", "SUMMER", "SUB", "MULTIPLIER", "DIVIDER", "ARITH", "COMPARATOR",
        "INTEGRATOR", "EXPRESSION", "LOG_EXP", "LOOKUP", "POLY",
        "POWER", "SQRT", "STATISTICS", "TOTALIZER", "TRIG", "FLOW_COMP",
        "BTU_CALC",
    ),
    PALETTE_TIMER: (
        "TIMER_ON", "TIMER_OFF", "RET", "PULSE", "COUNTER", "DTE",
        "SEQ_TIMER", "SCHEDULE",
    ),
    PALETTE_ENERGY: (
        "AGA_SI", "AGA_US", "ISE", "SST", "TSS", "SDR", "STM", "WTH", "WTS",
    ),
    PALETTE_LOGICAL: (
        "AND", "OR", "NOT", "NAND", "NOR", "XOR",
        "BDE", "POS_EDGE", "NEG_EDGE", "SR_LATCH", "RS_LATCH",
        "TRANSFER", "MUX", "DEMUX", "BFI", "BFO", "CND", "ACT", "DCC",
        "TRUTH_TABLE", "BIT_PACK", "BIT_UNPACK", "MEM_BOOL",
    ),
    PALETTE_ADVCTRL: (
        "DMC_CONTROLLER", "APC_CONTROL", "EXT_DMC_BRIDGE",
        "HEAT_BAL_PREDICTOR", "MV_CLAMP", "SP_HANDOFF", "SHED_LOGIC",
        "WATCHDOG", "LE",
    ),
    PALETTE_ADVANCED: (
        # voters / sequencing / matrix
        "AVTR", "DVTR", "CEM", "SEQ", "STD", "SIS_VOTER",
        # safety / BMS
        "INTERLOCK", "MOTOR_INTERLOCK", "TRIP_RELAY", "FOL_LOGIC",
        "BURNER_SEQ", "FLAME_DET", "FUEL_VALVE", "BLOWER", "PURGE_TIMER",
    ),
    PALETTE_SPECIAL: (
        # Azeo module-boundary and local-parameter special items
        "INPUT_PARAMETER", "OUTPUT_PARAMETER",
        "INTERNAL_READ_PARAMETER", "INTERNAL_WRITE_PARAMETER",
        # device control + alarms + stores/buffers
        "DEVCTL", "EDC", "VLVCTL", "ALARM",
        "MEM_FLOAT", "MEM_INT", "MEM_STRING", "FIFO", "LIFO",
    ),
    PALETTE_AZEO: (
        # A convenient literal source, but not a Azeo special palette item.
        "CONSTANT",
    ),
}

#: block_type → Azeo palette name (built once from PALETTE_BLOCKS)
_PALETTE_OF: dict[str, str] = {
    bt: palette for palette, bts in PALETTE_BLOCKS.items() for bt in bts
}

# Palette display order.  Unmapped blocks fall back to their BlockCategory
# value (e.g. SFC elements, Composite) and are appended after these.
_PALETTE_ORDER = [
    PALETTE_IO, PALETTE_ANALOG, PALETTE_MATH, PALETTE_TIMER, PALETTE_ENERGY,
    PALETTE_LOGICAL, PALETTE_ADVCTRL, PALETTE_ADVANCED, PALETTE_SPECIAL,
    PALETTE_AZEO,
]


def palette_group(block_cls) -> str:
    """Azeo palette a block belongs to.

    Falls back to the block's :class:`BlockCategory` value so blocks added
    in the future are still shown (never silently dropped).
    """
    return _PALETTE_OF.get(
        block_cls.block_type,
        block_cls.category.value if block_cls.category else "Other",
    )


_GROUP_COLORS = {
    PALETTE_IO:       "#3A8A4A",   # muted green — field I/O
    PALETTE_ANALOG:   "#3574C4",   # ISA-101 blue — analog control
    PALETTE_MATH:     "#5A6577",   # slate gray — math
    PALETTE_TIMER:    "#8A6D3B",   # bronze — timers/counters
    PALETTE_ENERGY:   "#00796B",   # teal — energy metering
    PALETTE_LOGICAL:  "#B8962A",   # muted gold — logic
    PALETTE_ADVCTRL:  "#7B1FA2",   # purple — supervisory/MPC
    PALETTE_ADVANCED: "#C82A3A",   # muted crimson — voters/safety
    PALETTE_SPECIAL:  "#4A7AA4",   # steel blue — special items
    PALETTE_AZEO:     "#6A5A8A",   # muted violet — product extensions
    # BlockCategory fallbacks
    BlockCategory.SFC.value:       "#E65100",   # deep orange — sequential
    BlockCategory.COMPOSITE.value: "#7B1FA2",   # purple — composite/UDFB
    BlockCategory.SIGNAL.value:    "#4A7AA4",
    BlockCategory.CONTROL.value:   "#3574C4",
    BlockCategory.SAFETY.value:    "#C82A3A",
    BlockCategory.APC.value:       "#7B1FA2",
    BlockCategory.LOGIC.value:     "#B8962A",
    BlockCategory.MATH.value:      "#5A6577",
    BlockCategory.IO.value:        "#3A8A4A",
}

# Unicode markers for expand/collapse state in category labels
_EXPANDED  = "\u25BC"   # ▼
_COLLAPSED = "\u25B6"   # ▶

MIME_TYPE = "application/x-strategy-block-type"
TEMPLATE_MIME_TYPE = "application/x-strategy-template-path"
_ROLE_GROUP = Qt.UserRole + 10
_ROLE_SEARCH = Qt.UserRole + 11
_ROLE_TEMPLATE = Qt.UserRole + 12
_ROLE_CUSTOM_INDEX = Qt.UserRole + 13
_CUSTOM_PREFIX = "CUSTOM:"

_TREE_STYLE = f"""
QTreeWidget {{
    background: {UI.pane};
    color: {UI.text};
    border: 1px solid {UI.border};
    font-size: 9pt;
    outline: none;
}}
QTreeWidget::item {{
    padding: 3px 4px;
    border: none;
}}
QTreeWidget::item:hover {{
    background: {UI.hover};
}}
QTreeWidget::item:selected {{
    background: {UI.blue};
    color: #FFFFFF;
}}
QTreeWidget::branch {{
    background: {UI.pane};
}}
QTreeWidget::branch:has-children:closed {{
    border-image: none;
}}
QTreeWidget::branch:has-children:open {{
    border-image: none;
}}
"""

_HDR_STYLE = (
    f"font-weight: bold; font-size: 9pt; color: {UI.blue}; "
    "padding: 4px 6px 2px 6px; background: transparent; border: none;"
)

_BTN_STYLE = (
    f"QPushButton {{ background: {UI.chrome}; color: {UI.blue}; "
    f"border: 1px solid {UI.border}; border-radius: 2px; "
    "font-size: 9pt; font-weight: bold; padding: 2px 6px; }"
    f"QPushButton:hover {{ background: {UI.selection}; }}"
)


class _PaletteTree(QTreeWidget):
    """QTreeWidget subclass that provides proper MIME data for drag operations."""

    def mimeTypes(self):
        return [MIME_TYPE, TEMPLATE_MIME_TYPE]

    def mimeData(self, items):
        data = QMimeData()
        for item in items:
            template_path = item.data(0, _ROLE_TEMPLATE)
            if template_path:
                data.setData(
                    TEMPLATE_MIME_TYPE, QByteArray(str(template_path).encode()))
                break
            block_type = item.data(0, Qt.UserRole)
            if block_type:
                data.setData(MIME_TYPE, QByteArray(block_type.encode()))
                break
        return data


class BlockPalette(QWidget):
    """Categorized palette of function block types with drag support.

    Enhanced with favorites, recently used, and rich preview tooltips.
    """

    MIME_TYPE = MIME_TYPE
    TEMPLATE_MIME_TYPE = TEMPLATE_MIME_TYPE
    templatePlacementRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cat_items: dict[str, QTreeWidgetItem] = {}
        self._favorites: set[str] = set()  # block_type strings
        self._recent: list[str] = []        # block_type strings, most recent first
        self._custom_palettes: dict[str, list[dict[str, str]]] = {}
        self._fav_item: QTreeWidgetItem | None = None
        self._recent_item: QTreeWidgetItem | None = None
        self._load_settings()
        self._build_ui()

    # ── Persistent settings ──

    def _load_settings(self):
        """Load favorites and recently used from disk."""
        if _PALETTE_SETTINGS.exists():
            try:
                with open(_PALETTE_SETTINGS, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._favorites = set(data.get("favorites", []))
                self._recent = data.get("recent", [])[:_MAX_RECENT]
                palettes = data.get("custom_palettes", {})
                if isinstance(palettes, dict):
                    self._custom_palettes = {
                        str(name): [dict(entry) for entry in entries
                                    if isinstance(entry, dict)]
                        for name, entries in palettes.items()
                        if isinstance(entries, list)
                    }
            except (json.JSONDecodeError, OSError):
                pass

    def _save_settings(self):
        """Save favorites and recently used to disk."""
        try:
            _PALETTE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            with open(_PALETTE_SETTINGS, "w", encoding="utf-8") as f:
                json.dump({
                    "favorites": sorted(self._favorites),
                    "recent": self._recent[:_MAX_RECENT],
                    "custom_palettes": self._custom_palettes,
                }, f, indent=2)
        except OSError:
            pass

    def record_block_use(self, block_type: str):
        """Record that a block type was placed on the canvas."""
        if block_type in self._recent:
            self._recent.remove(block_type)
        self._recent.insert(0, block_type)
        self._recent = self._recent[:_MAX_RECENT]
        self._save_settings()
        self._refresh_special_sections()

    # ── Favorites management ──

    def _toggle_favorite(self, block_type: str):
        """Toggle a block type as favorite."""
        if block_type in self._favorites:
            self._favorites.discard(block_type)
        else:
            self._favorites.add(block_type)
        self._save_settings()
        self._refresh_special_sections()

    def _is_favorite(self, block_type: str) -> bool:
        return block_type in self._favorites

    # ── Named custom palettes ──

    def custom_palettes(self) -> dict[str, list[dict[str, str]]]:
        return {name: [dict(entry) for entry in entries]
                for name, entries in self._custom_palettes.items()}

    def create_custom_palette(self, name: str) -> bool:
        name = str(name).strip()
        if not name or name in self._custom_palettes:
            return False
        self._custom_palettes[name] = []
        self._save_settings()
        self._populate()
        return True

    def rename_custom_palette(self, old: str, new: str) -> bool:
        new = str(new).strip()
        if old not in self._custom_palettes or not new \
                or (new != old and new in self._custom_palettes):
            return False
        entries = self._custom_palettes.pop(old)
        self._custom_palettes[new] = entries
        self._save_settings()
        self._populate()
        return True

    def remove_custom_palette(self, name: str) -> bool:
        if name not in self._custom_palettes:
            return False
        del self._custom_palettes[name]
        self._save_settings()
        self._populate()
        return True

    def add_block_to_palette(self, name: str, block_type: str) -> bool:
        if name not in self._custom_palettes or registry.get(block_type) is None:
            return False
        entry = {"kind": "block", "value": str(block_type)}
        if entry in self._custom_palettes[name]:
            return False
        self._custom_palettes[name].append(entry)
        self._save_settings()
        self._populate()
        return True

    def add_template_to_palette(self, name: str, template_path: str,
                                label: str = "") -> bool:
        path = Path(template_path)
        if name not in self._custom_palettes or not path.exists():
            return False
        entry = {"kind": "template", "value": str(path),
                 "label": str(label).strip() or path.stem}
        if any(item.get("kind") == "template" and
               Path(item.get("value", "")) == path
               for item in self._custom_palettes[name]):
            return False
        self._custom_palettes[name].append(entry)
        self._save_settings()
        self._populate()
        return True

    def remove_palette_entry(self, name: str, index: int) -> bool:
        entries = self._custom_palettes.get(name)
        if entries is None or not 0 <= index < len(entries):
            return False
        entries.pop(index)
        self._save_settings()
        self._populate()
        return True

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header row with expand/collapse buttons
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(4, 4, 4, 2)
        hdr_row.setSpacing(4)

        hdr = QLabel("BLOCK LIBRARY")
        hdr.setStyleSheet(_HDR_STYLE)
        hdr_row.addWidget(hdr)

        hdr_row.addStretch()

        btn_palettes = QPushButton("P+")
        btn_palettes.setFixedSize(24, 18)
        btn_palettes.setStyleSheet(_BTN_STYLE)
        btn_palettes.setToolTip("Create or manage a named custom palette")
        btn_palettes.clicked.connect(self._custom_palette_menu)
        hdr_row.addWidget(btn_palettes)

        btn_expand = QPushButton("+")
        btn_expand.setFixedSize(20, 18)
        btn_expand.setStyleSheet(_BTN_STYLE)
        btn_expand.setToolTip("Expand all categories")
        btn_expand.clicked.connect(self._expand_all)
        hdr_row.addWidget(btn_expand)

        btn_collapse = QPushButton("\u2212")  # minus
        btn_collapse.setFixedSize(20, 18)
        btn_collapse.setStyleSheet(_BTN_STYLE)
        btn_collapse.setToolTip("Collapse all categories")
        btn_collapse.clicked.connect(self._collapse_all)
        hdr_row.addWidget(btn_collapse)

        layout.addLayout(hdr_row)

        # Search filter
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter blocks...")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(
            f"QLineEdit {{ background: #FFFFFF; color: {UI.text}; "
            f"border: 1px solid {UI.border}; border-radius: 3px; "
            "padding: 4px 6px; margin: 2px 4px; font-size: 9pt; }"
            f"QLineEdit:focus {{ border-color: {UI.blue}; }}"
        )
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        # Tree (custom subclass for MIME)
        self._tree = _PaletteTree()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(False)
        self._tree.setDragEnabled(True)
        self._tree.setDragDropMode(QTreeWidget.DragOnly)
        self._tree.setDefaultDropAction(Qt.CopyAction)
        self._tree.setAnimated(True)
        self._tree.setIndentation(16)
        self._tree.setStyleSheet(_TREE_STYLE)
        # A category header toggles on a single click. Qt's default needs a
        # double-click on the label (or a hit on the branch arrow, which is
        # hidden here by setRootIsDecorated(False)), so the group triangles
        # looked clickable but did nothing on one click.
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.itemCollapsed.connect(self._on_item_collapsed)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree, 1)

        # Block count label
        self._lbl_count = QLabel("")
        self._lbl_count.setStyleSheet(
            f"font-size: 9pt; color: {UI.text_muted}; padding: 2px 6px;"
        )
        layout.addWidget(self._lbl_count)

        self._populate()

    def _on_context_menu(self, pos):
        """Context commands for installed blocks and named palettes."""
        item = self._tree.itemAt(pos)
        if item is None:
            return
        block_type = item.data(0, Qt.UserRole)
        group = item.data(0, _ROLE_GROUP)
        parent_group = item.parent().data(0, _ROLE_GROUP) \
            if item.parent() is not None else None
        custom_group = group if str(group).startswith(_CUSTOM_PREFIX) \
            else parent_group if str(parent_group).startswith(_CUSTOM_PREFIX) \
            else None

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {UI.pane}; color: {UI.text}; font-size: 9pt; "
            f"border: 1px solid {UI.border}; }}"
            "QMenu::item { padding: 4px 20px; }"
            f"QMenu::item:selected {{ background: {UI.blue}; color: white; }}")

        if block_type:
            is_fav = self._is_favorite(block_type)
            fav_text = "Remove from Favorites" if is_fav else "Add to Favorites"
            fav_action = menu.addAction(fav_text)
            fav_action.triggered.connect(
                lambda: self._toggle_favorite(block_type))
            add_to = menu.addMenu("Add to Custom Palette")
            for name in self._custom_palettes:
                action = add_to.addAction(name)
                action.triggered.connect(
                    lambda checked=False, n=name, bt=block_type:
                    self.add_block_to_palette(n, bt))
            if self._custom_palettes:
                add_to.addSeparator()
            add_to.addAction("New Palette…", self._prompt_new_palette)
            menu.addSeparator()
            info_action = menu.addAction("Block Info...")
            info_action.triggered.connect(
                lambda: self._show_block_info(block_type))

        if custom_group:
            name = str(custom_group)[len(_CUSTOM_PREFIX):]
            if item.parent() is not None:
                index = item.data(0, _ROLE_CUSTOM_INDEX)
                menu.addSeparator()
                menu.addAction(
                    "Remove from Palette",
                    lambda checked=False, n=name, i=int(index):
                    self.remove_palette_entry(n, i))
            else:
                menu.addAction("Add Template…",
                               lambda checked=False, n=name:
                               self._prompt_add_template(n))
                menu.addAction("Rename Palette…",
                               lambda checked=False, n=name:
                               self._prompt_rename_palette(n))
                menu.addSeparator()
                menu.addAction("Delete Palette",
                               lambda checked=False, n=name:
                               self.remove_custom_palette(n))

        if not menu.actions():
            return

        try:
            menu.exec(self._tree.viewport().mapToGlobal(pos))
        finally:
            menu.deleteLater()

    def _custom_palette_menu(self):
        menu = QMenu(self)
        menu.addAction("New Custom Palette…", self._prompt_new_palette)
        if self._custom_palettes:
            menu.addSeparator()
            for name in self._custom_palettes:
                submenu = menu.addMenu(name)
                submenu.addAction(
                    "Add Template…",
                    lambda checked=False, n=name: self._prompt_add_template(n))
                submenu.addAction(
                    "Rename…",
                    lambda checked=False, n=name: self._prompt_rename_palette(n))
                submenu.addAction(
                    "Delete",
                    lambda checked=False, n=name:
                    self.remove_custom_palette(n))
        button = self.sender()
        try:
            menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        finally:
            menu.deleteLater()

    def _prompt_new_palette(self):
        name, accepted = QInputDialog.getText(
            self, "New Custom Palette", "Palette name:")
        if accepted:
            self.create_custom_palette(name)

    def _prompt_rename_palette(self, old: str):
        name, accepted = QInputDialog.getText(
            self, "Rename Custom Palette", "Palette name:", text=old)
        if accepted:
            self.rename_custom_palette(old, name)

    def _prompt_add_template(self, palette: str):
        from azeo_control_trainer.core.strategy.serialization import strategy_io
        start = str(strategy_io.TEMPLATE_DIR)
        path, _ = QFileDialog.getOpenFileName(
            self, "Add Block Template", start, "Strategy templates (*.json)")
        if path:
            self.add_template_to_palette(palette, path)

    def _show_block_info(self, block_type: str):
        """Show a detailed block info popup."""
        from PySide6.QtWidgets import QDialog, QTextBrowser
        block_cls = registry.get(block_type)
        if not block_cls:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Block Info: {block_cls.display_name}")
        dlg.resize(450, 400)
        dlg.setStyleSheet(f"QDialog {{ background: {UI.pane}; }}")
        lay = QVBoxLayout(dlg)

        browser = QTextBrowser()
        browser.setStyleSheet(
            "QTextBrowser { background: #FFF; font-family: Segoe UI, sans-serif; "
            f"font-size: 9pt; border: 1px solid {UI.border}; }}")
        browser.setHtml(self._build_block_info_html(block_cls))
        lay.addWidget(browser, 1)

        btn = QPushButton("Close")
        btn.setStyleSheet(_BTN_STYLE)
        btn.clicked.connect(dlg.close)
        lay.addWidget(btn, alignment=Qt.AlignRight)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()

    @staticmethod
    def _build_block_info_html(block_cls) -> str:
        """Build rich HTML info for a block class."""
        html = f"<h2>{block_cls.display_name}</h2>"
        html += f"<p><b>Type:</b> {block_cls.block_type}</p>"
        html += f"<p><b>Category:</b> {block_cls.category.value}</p>"
        html += f"<p><b>Description:</b> {block_cls.description}</p>"

        # Create a temp instance to inspect terminals
        try:
            temp = block_cls("_info")
        except Exception:
            return html

        # Inputs
        if temp.inputs:
            html += "<h3>Inputs</h3><table border='1' cellpadding='3' cellspacing='0' width='100%'>"
            html += "<tr><th>Name</th><th>Type</th><th>Default</th><th>Description</th></tr>"
            for name, term in temp.inputs.items():
                bkcal = " (BKCAL)" if term.is_bkcal else ""
                html += (f"<tr><td><b>{name}</b>{bkcal}</td>"
                         f"<td>{term.data_type.value}</td>"
                         f"<td>{term.default_value}</td>"
                         f"<td>{term.description}</td></tr>")
            html += "</table>"

        # Outputs
        if temp.outputs:
            html += "<h3>Outputs</h3><table border='1' cellpadding='3' cellspacing='0' width='100%'>"
            html += "<tr><th>Name</th><th>Type</th><th>Default</th><th>Description</th></tr>"
            for name, term in temp.outputs.items():
                bkcal = " (BKCAL)" if term.is_bkcal else ""
                html += (f"<tr><td><b>{name}</b>{bkcal}</td>"
                         f"<td>{term.data_type.value}</td>"
                         f"<td>{term.default_value}</td>"
                         f"<td>{term.description}</td></tr>")
            html += "</table>"

        # Config schema
        schema = temp.get_config_schema()
        if schema:
            html += "<h3>Configuration</h3><table border='1' cellpadding='3' cellspacing='0' width='100%'>"
            html += "<tr><th>Parameter</th><th>Type</th><th>Default</th><th>Description</th></tr>"
            for name, (ptype, default, desc) in schema.items():
                tname = ptype.__name__ if hasattr(ptype, '__name__') else str(ptype)
                html += (f"<tr><td><b>{name}</b></td>"
                         f"<td>{tname}</td>"
                         f"<td>{default}</td>"
                         f"<td>{desc}</td></tr>")
            html += "</table>"

        return html

    @staticmethod
    def _group_blocks() -> dict[str, list]:
        """Group every registered block class by Azeo palette.

        Unmapped block types fall back to their BlockCategory value, so a
        newly registered block always lands somewhere.
        """
        groups: dict[str, list] = {}
        for block_cls in registry.all_types().values():
            groups.setdefault(palette_group(block_cls), []).append(block_cls)
        return groups

    @staticmethod
    def _group_order(groups: dict[str, list]) -> list[str]:
        """Azeo palette order first, then any fallback groups (A→Z)."""
        ordered = [g for g in _PALETTE_ORDER if groups.get(g)]
        extra = sorted(g for g in groups if g not in _PALETTE_ORDER)
        return ordered + extra

    def _populate(self):
        """Fill the tree with registered block types."""
        self._tree.clear()
        self._cat_items.clear()
        groups = self._group_blocks()
        total = 0

        # ── Favorites section ──
        if self._favorites:
            label = f"{_EXPANDED} Favorites  ({len(self._favorites)})"
            self._fav_item = QTreeWidgetItem([label])
            self._fav_item.setFlags(self._fav_item.flags() & ~Qt.ItemIsDragEnabled)
            self._fav_item.setData(0, Qt.UserRole + 10, "FAV")
            font = QFont("Segoe UI", 8, QFont.Bold)
            self._fav_item.setFont(0, font)
            self._fav_item.setForeground(0, QColor("#D4A017"))  # gold
            self._add_block_items_to(self._fav_item, self._favorites)
            self._tree.addTopLevelItem(self._fav_item)
            self._fav_item.setExpanded(True)

        # ── Recently Used section ──
        if self._recent:
            label = f"{_EXPANDED} Recently Used  ({len(self._recent)})"
            self._recent_item = QTreeWidgetItem([label])
            self._recent_item.setFlags(
                self._recent_item.flags() & ~Qt.ItemIsDragEnabled)
            self._recent_item.setData(0, Qt.UserRole + 10, "RECENT")
            font = QFont("Segoe UI", 8, QFont.Bold)
            self._recent_item.setFont(0, font)
            self._recent_item.setForeground(0, QColor("#4A7AA4"))  # steel blue
            self._add_block_items_to(self._recent_item, self._recent)
            self._tree.addTopLevelItem(self._recent_item)
            self._recent_item.setExpanded(True)

        # ── Named user palettes: blocks and reusable composite templates ──
        for name, entries in self._custom_palettes.items():
            group_key = f"{_CUSTOM_PREFIX}{name}"
            label = f"{_EXPANDED} {name}  ({len(entries)})"
            custom = QTreeWidgetItem([label])
            custom.setFlags(custom.flags() & ~Qt.ItemIsDragEnabled)
            custom.setData(0, _ROLE_GROUP, group_key)
            custom.setFont(0, QFont("Segoe UI", 8, QFont.Bold))
            custom.setForeground(0, QColor("#6A3FA0"))
            for index, entry in enumerate(entries):
                kind = entry.get("kind")
                value = entry.get("value", "")
                if kind == "block":
                    block_cls = registry.get(value)
                    if block_cls is None:
                        continue
                    child = self._make_block_item(block_cls)
                elif kind == "template":
                    path = Path(value)
                    child = QTreeWidgetItem([
                        f" Template · {entry.get('label') or path.stem}"])
                    child.setData(0, _ROLE_TEMPLATE, str(path))
                    child.setData(
                        0, _ROLE_SEARCH,
                        f"template {entry.get('label', '')} {path.stem}".lower())
                    child.setToolTip(
                        0, f"Reusable block/composite template\n{path}")
                else:
                    continue
                child.setData(0, _ROLE_CUSTOM_INDEX, index)
                custom.addChild(child)
            self._tree.addTopLevelItem(custom)
            custom.setExpanded(True)

        # ── Azeo palettes (+ BlockCategory fallback groups) ──
        for group in self._group_order(groups):
            blocks = groups.get(group, [])
            if not blocks:
                continue

            n = len(blocks)
            total += n
            label = f"{_EXPANDED} {group}  ({n})"
            cat_item = QTreeWidgetItem([label])
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsDragEnabled)
            cat_item.setData(0, Qt.UserRole + 10, group)

            font = QFont("Segoe UI", 8, QFont.Bold)
            cat_item.setFont(0, font)
            color = UI.blue
            cat_item.setForeground(0, QColor(color))

            for block_cls in sorted(blocks, key=lambda c: c.display_name):
                block_item = self._make_block_item(block_cls)
                cat_item.addChild(block_item)

            self._tree.addTopLevelItem(cat_item)
            # Collapsed by default, as Azeo's palette bars are: the
            # category names are the index, and 140 blocks unfolded is a
            # wall. Search still auto-expands its matches, and Favorites /
            # Recent stay open — they are the engineer's own shortlist.
            cat_item.setExpanded(False)
            self._cat_items[group] = cat_item

        self._lbl_count.setText(
            f"{total} block types in {len(self._cat_items)} palettes")

    @staticmethod
    def _search_haystack(block_cls) -> str:
        """Lowercase text the search box matches against.

        Display name + block_type id + every Azeo mnemonic alias, so a
        Azeo engineer's muscle memory (SCLR, OND, RTO, TP, XFR, MLTY\u2026)
        hits the right block even though our ids differ.
        """
        parts = [block_cls.display_name, block_cls.block_type]
        parts.extend(BLOCK_MNEMONICS.get(block_cls.block_type, ()))
        return " ".join(parts).lower()

    def _make_block_item(self, block_cls) -> QTreeWidgetItem:
        """Create a tree item for a block class with rich tooltip + icon."""
        fav_star = " \u2605" if block_cls.block_type in self._favorites else ""
        # Show the Azeo mnemonic unless the display name already carries it
        mnemonic = mnemonic_for(block_cls.block_type)
        suffix = ""
        if mnemonic and mnemonic.lower() not in block_cls.display_name.lower():
            suffix = f"  \u00b7 {mnemonic}"
        block_item = QTreeWidgetItem(
            [f" {block_cls.display_name}{suffix}{fav_star}"])
        block_item.setData(0, Qt.UserRole, block_cls.block_type)
        block_item.setData(0, Qt.UserRole + 11, self._search_haystack(block_cls))
        block_item.setToolTip(0, self._build_rich_tooltip(block_cls))
        # Procedural glyph icon \u2014 one per block_type
        from azeo_control_trainer.core.presentation.function_block_icons import make_block_qicon
        block_item.setIcon(0, make_block_qicon(
            block_cls.block_type, category=block_cls.category, size=18))
        return block_item

    @staticmethod
    def _build_rich_tooltip(block_cls) -> str:
        """Build a detailed tooltip with terminal diagram."""
        mnemonics = BLOCK_MNEMONICS.get(block_cls.block_type, ())
        lines = [
            f"{block_cls.display_name}  ({block_cls.block_type})",
            f"Palette: {palette_group(block_cls)}"
            f"   |   Category: {block_cls.category.value}",
        ]
        if mnemonics:
            lines.append(f"Azeo: {', '.join(mnemonics)}")
        lines += [f"{block_cls.description}", ""]
        try:
            temp = block_cls("_tip")
            if temp.inputs:
                lines.append("INPUTS:")
                for name, term in temp.inputs.items():
                    bkcal = " [BKCAL]" if term.is_bkcal else ""
                    lines.append(f"  {name} ({term.data_type.value}){bkcal}")
            if temp.outputs:
                lines.append("OUTPUTS:")
                for name, term in temp.outputs.items():
                    bkcal = " [BKCAL]" if term.is_bkcal else ""
                    lines.append(f"  {name} ({term.data_type.value}){bkcal}")
            schema = temp.get_config_schema()
            if schema:
                lines.append(f"\nCONFIG: {len(schema)} parameters")
        except Exception:
            pass
        return "\n".join(lines)

    def _add_block_items_to(self, parent_item: QTreeWidgetItem,
                            block_types) -> None:
        """Add block items to a parent tree item."""
        for bt in block_types:
            block_cls = registry.get(bt)
            if block_cls:
                item = self._make_block_item(block_cls)
                parent_item.addChild(item)

    def _refresh_special_sections(self):
        """Rebuild favorites and recently used sections without full repopulate."""
        self._populate()

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int = 0):
        """Toggle a category group on a single click.

        Only category headers (top-level rows) toggle; clicking a block row
        must leave selection and drag-and-drop untouched.
        """
        if item is None or item.parent() is not None:
            return
        item.setExpanded(not item.isExpanded())

    def _on_item_double_clicked(self, item: QTreeWidgetItem,
                                _column: int = 0) -> None:
        template_path = item.data(0, _ROLE_TEMPLATE)
        if template_path:
            self.templatePlacementRequested.emit(str(template_path))

    def _on_item_expanded(self, item: QTreeWidgetItem):
        """Update the arrow indicator when a category is expanded."""
        cat = item.data(0, Qt.UserRole + 10)
        if cat is not None:
            n = item.childCount()
            label = cat.value if hasattr(cat, 'value') else str(cat)
            # Use friendly name for special sections
            if cat == "FAV":
                label = "Favorites"
            elif cat == "RECENT":
                label = "Recently Used"
            elif str(cat).startswith(_CUSTOM_PREFIX):
                label = str(cat)[len(_CUSTOM_PREFIX):]
            item.setText(0, f"{_EXPANDED} {label}  ({n})")

    def _on_item_collapsed(self, item: QTreeWidgetItem):
        """Update the arrow indicator when a category is collapsed."""
        cat = item.data(0, Qt.UserRole + 10)
        if cat is not None:
            n = item.childCount()
            label = cat.value if hasattr(cat, 'value') else str(cat)
            if cat == "FAV":
                label = "Favorites"
            elif cat == "RECENT":
                label = "Recently Used"
            elif str(cat).startswith(_CUSTOM_PREFIX):
                label = str(cat)[len(_CUSTOM_PREFIX):]
            item.setText(0, f"{_COLLAPSED} {label}  ({n})")

    def _expand_all(self):
        self._tree.expandAll()

    def _collapse_all(self):
        self._tree.collapseAll()

    @staticmethod
    def _item_matches(item: QTreeWidgetItem, text: str) -> bool:
        """Does a block row match the (already lowercased) search text?

        Matches the display row, the block_type id and the Azeo
        mnemonic aliases — so "SCLR" finds Scaler, "OND" the on-delay
        timer, "RTO" Ratio.
        """
        if not text:
            return True
        haystack = item.data(0, Qt.UserRole + 11)
        if haystack and text in haystack:
            return True
        block_type = item.data(0, Qt.UserRole)
        if block_type and text in str(block_type).lower():
            return True
        return text in item.text(0).lower()

    def _filter(self, text: str):
        """Filter blocks by search text. Matching palettes auto-expand."""
        text = text.strip().lower()
        for i in range(self._tree.topLevelItemCount()):
            cat_item = self._tree.topLevelItem(i)
            any_visible = False
            for j in range(cat_item.childCount()):
                child = cat_item.child(j)
                visible = self._item_matches(child, text)
                child.setHidden(not visible)
                if visible:
                    any_visible = True
            cat_item.setHidden(not any_visible)
            # Auto-expand palettes with matches when searching
            if text and any_visible:
                cat_item.setExpanded(True)

    # ── Introspection helpers (used by the palette smoke test) ──

    def search(self, text: str) -> None:
        """Apply a search filter programmatically."""
        self._search.setText(text)

    def visible_block_types(self, include_special: bool = False) -> list[str]:
        """block_types currently visible in the tree.

        Favorites / Recently Used rows are excluded unless
        ``include_special`` is set, so callers see each block once.
        """
        out: list[str] = []
        for i in range(self._tree.topLevelItemCount()):
            cat_item = self._tree.topLevelItem(i)
            if cat_item.isHidden():
                continue
            group = cat_item.data(0, Qt.UserRole + 10)
            if not include_special and (group in ("FAV", "RECENT")
                                        or str(group).startswith(_CUSTOM_PREFIX)):
                continue
            for j in range(cat_item.childCount()):
                child = cat_item.child(j)
                if not child.isHidden():
                    out.append(child.data(0, Qt.UserRole))
        return out

    def group_contents(self) -> dict[str, list[str]]:
        """Palette group name → block_types in it (Favorites/Recent excluded)."""
        out: dict[str, list[str]] = {}
        for i in range(self._tree.topLevelItemCount()):
            cat_item = self._tree.topLevelItem(i)
            group = cat_item.data(0, Qt.UserRole + 10)
            if group in ("FAV", "RECENT") \
                    or str(group).startswith(_CUSTOM_PREFIX):
                continue
            out[str(group)] = [
                cat_item.child(j).data(0, Qt.UserRole)
                for j in range(cat_item.childCount())
            ]
        return out
