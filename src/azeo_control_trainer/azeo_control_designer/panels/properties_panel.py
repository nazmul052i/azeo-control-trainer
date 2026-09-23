"""Properties panel — shows and edits configuration of the selected block.

Dynamically builds a form based on the block's get_config_schema(), in the
style of Azeo Control Designer's parameter view:

  * enumerated ("named set") parameters render as a drop-down — the values
    come from the block's ``config_choices`` (see FunctionBlock.choices_for),
    so a typo can no longer silently select the default;
  * a Unit column (``FunctionBlock.unit_for``) sits beside each editor;
  * a filter box narrows the visible parameter rows by name — EDC / AGA_SI
    and friends carry 40–200 parameters;
  * **every** block gets collapsible parameter groups (Quick Configuration /
    Connectors / Tuning / Alarms / …), derived by ``derive_param_groups``
    from an explicit per-block override, the option bitstrings, repeated
    parameter families, and finally a name heuristic — a DCC with 216
    parameters is no longer one ungrouped list;
  * each row carries its **data type** and, for parameters a block declares
    read-only (``config_readonly``) or the schema description marks as
    computed / OOS-only, a disabled editor instead of one that silently does
    nothing;
  * option bits (``OPT_*``) are gathered into a group named after the Azeo
    bitstring they belong to (DEVICE_OPTS, BYPASS_OPTS, STATUS_OPTS, …) and
    labelled with the manual's option wording;
  * while the module is on scan, every row shows the block's **live** value
    beside the configured one (``refresh()``, called from the designer tick).

Panel can be docked on left or right side via context menu.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.property_group import PropertyGroup as _CollapsibleGroup

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget, QCheckBox, QToolButton,
)

from azeo_control_trainer.core.strategy.model.block_base import FunctionBlock
from .tag_browser import TagPickerWidget


# PID parameter grouping for organized display
_PID_GROUPS = {
    "Tuning": [
        "GAIN", "RESET", "RATE", "alpha", "beta", "gamma", "bias", "ideadband",
    ],
    "Filtering": [
        "pv_ftime", "sp_ftime", "sp_rate_up", "sp_rate_dn",
    ],
    "Action & Structure": [
        "action", "form", "structure",
    ],
    "Output Limits": [
        "out_lo", "out_hi", "arw_lo", "arw_hi",
    ],
    "SP Limits": [
        "sp_init", "sp_lo", "sp_hi", "pv_scale_lo", "pv_scale_hi",
    ],
    "Alarms": [
        "hi_hi_lim", "hi_lim", "lo_lim", "lo_lo_lim",
        "dv_hi_lim", "dv_lo_lim", "alarm_hys",
    ],
    "Nonlinear Gain": [
        "nl_enable", "nl_gap", "nl_hyst", "nl_tband", "nl_minmod",
    ],
    "Feedforward": [
        "ff_enable", "ff_gain",
    ],
    # Named after the Azeo bitstrings these bits belong to (D5) — an
    # engineer looking for FRSIPID_OPTS / CONTROL_OPTS finds them by name.
    "FRSIPID_OPTS": [
        "use_pidplus", "dynamic_reset_limit", "use_delayed_out_bad_pv",
        "recovery_fltr",
    ],
    "CONTROL_OPTS": [
        "sp_pv_track_man", "sp_pv_track_lo_iman", "sp_pv_track_rout",
        "use_pv_for_bkcal_out", "no_out_limits_in_man", "obey_sp_lim_cas_rcas",
        "track_enable", "track_in_manual", "bypass_enable",
    ],
    "Simulation": [
        "simulate_enabled",
    ],
    "Mode": [
        "mode",
    ],
}

# Blocks whose grouping is hand-authored; everything else is derived.
_EXPLICIT_GROUPS: dict[str, dict[str, list[str]]] = {
    "PID": _PID_GROUPS,
}

# Groups that open expanded (per block type). Everything else starts
# collapsed, so a 216-parameter DCC is a short list of headers.
_PID_EXPANDED = ("Quick Configuration", "Tuning", "Output Limits",
                 "SP Limits", "Mode")

# All PID modes
_PID_MODES = ["OOS", "IMAN", "LO", "MAN", "AUTO", "CAS", "RCAS", "ROUT"]

# Mode display names
_MODE_LABELS = {
    "OOS": "OOS — Out of Service",
    "IMAN": "IMAN — Init Manual",
    "LO": "LO — Local Override",
    "MAN": "MAN — Manual",
    "AUTO": "AUTO — Automatic",
    "CAS": "CAS — Cascade",
    "RCAS": "RCAS — Remote Cascade",
    "ROUT": "ROUT — Remote Output",
}

# Named sets the panel has always known about, kept here so the three legacy
# hardcoded combos (action / form / structure) now flow through exactly the
# same lookup as a block-declared ``config_choices`` entry. A block that
# declares its own values for these names overrides the table.
_FALLBACK_CHOICES: dict[str, tuple[str, ...]] = {
    "action": ("reverse", "direct"),
    "form": ("standard", "series"),
    "structure": (
        "two_dof", "pid_on_error", "pi_error_d_pv", "i_error_pd_pv",
        "pd_on_error", "p_error_d_pv", "id_on_error", "i_error_d_pv",
    ),
}

# A schema description often spells out its own named set, e.g.
#   "Target mode: OOS | MAN | AUTO | CAS"     "Function: LN, LOG10, EXP, POW10"
# For ``MODE`` params (which every Azeo block has, but which mean a DCS
# mode on some blocks and a function selector on others) those tokens are the
# only machine-readable clue until the block declares config_choices.
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,15}$")


def _tokens_from_description(desc: str) -> tuple[str, ...]:
    """Best-effort named set parsed out of a schema description."""
    if not desc or ":" not in desc:
        return ()
    tail = desc.split(":", 1)[1]
    parts = [p.strip() for p in re.split(r"[|/,]", tail)]
    tokens = [p for p in parts if _TOKEN_RE.match(p)]
    if len(tokens) < 2 or len(tokens) != len(parts):
        return ()          # prose crept in — not a clean enumeration
    return tuple(tokens)


# ─── Azeo parameter metadata: type, writability, option bitstrings ───

# Short data-type chips shown in the parameter view's Type column.
_TYPE_CHIPS = {float: ("flt", "float"), int: ("int", "integer"),
               bool: ("bool", "boolean"), str: ("str", "string")}

# A parameter the block computes: an editor for it would silently do nothing.
_RO_RE = re.compile(
    r"(read[\s-]?only|readonly|computed by|calculated by|"
    r"not settable|not configurable|indication only|status only|diagnostic only)",
    re.I)

# Azeo's "settable in OOS only" / "cannot be changed online" wording —
# writable off scan, or on scan only with the block in OOS/Man.
_OOS_RE = re.compile(
    r"(settable\s+(?:only\s+)?in\s+(?:oos|man)|oos\s+only|"
    r"cannot be changed online|change online only in oos)", re.I)

# Modes in which an "OOS only" parameter may still be written.
_OOS_WRITE_MODES = ("OOS", "MAN", "IMAN")

# A parameter label is bounded so the editor stays on screen in a 240-360 px
# panel: elided past _MAX_LABEL_CHARS (the full name is in the tooltip) and
# hard-capped at _LABEL_MAX_PX.
_MAX_LABEL_CHARS = 22
_LABEL_MAX_PX = 150

# "DEVICE_OPTS bit 3 — in Auto, FORCE_SP_D …" / "INPUT_OPTS: IN Use Uncertain"
_OPT_DESC_RE = re.compile(
    r"^\s*([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_OPTS?)\b"
    r"(?:\s*bit\s*(\d+))?\s*[:—–\-]?\s*(.*)$", re.S)

# Words that stay upper-case when an option label is built from its key.
_ACRONYMS = {
    "SP", "PV", "OUT", "IN", "IO", "CAS", "RCAS", "ROUT", "LO", "OOS", "MAN",
    "BKCAL", "CFM", "ILK", "D", "GC", "EU", "DV", "MV", "PID", "FF", "TRK",
}


def _title(text: str) -> str:
    """'SP_TRACK_ON_TRIP' → 'SP Track on Trip'."""
    small = {"on", "in", "if", "to", "or", "of", "a", "as", "the", "is"}
    words = [w for w in re.split(r"[_\s]+", text) if w]
    out = []
    for i, w in enumerate(words):
        up = w.upper()
        if up in _ACRONYMS:
            out.append(up)
        elif i and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def option_bitstring(name: str, desc: str) -> tuple[str, str] | None:
    """(bitstring, option label) for an ``OPT_*`` bit, else None.

    The new Azeo-derived blocks expose Azeo's option bitstrings as
    individual booleans (``OPT_SP_TRACK_ON_TRIP``) whose description names
    the bitstring they belong to. That is enough to put them back together
    as one named checklist per bitstring, which is how Azeo shows them.
    """
    if not name.upper().startswith("OPT_"):
        return None
    m = _OPT_DESC_RE.match(desc or "")
    if not m:
        return None
    bits, tail = m.group(1), (m.group(3) or "").strip()
    tail = tail.split("\n")[0].strip(" .")
    # Prefer the manual's own wording when the description is just the option
    # name ("IN Use Uncertain"); fall back to the key for prose descriptions.
    if tail and len(tail) <= 40 and not re.search(r"[,;]| the | if | with ", tail):
        label = tail
    else:
        label = _title(name[4:])
    return bits.upper(), label


# ─── Generic parameter grouping (Azeo groups; ours had two) ───────────

_QUICK = "Quick Configuration"
_COMMON = "Common Configuration"
_DOCS = "Documentation"

# Name heuristic, first match wins. Ordered so HI_LIM lands in Alarms
# rather than Scaling & Limits.
_SEMANTIC_RULES: tuple[tuple[str, re.Pattern], ...] = (
    # SP_HI_LIM / OUT_LO_LIM are working limits, not alarm limits.
    ("Scaling & Limits", re.compile(r"^(SP|OUT)_\w*(LIM|_HI|_LO)$")),
    ("Alarms", re.compile(
        r"ALARM|ALM|PRIORITY|HI_HI|LO_LO|(^|_)(HI|LO)_LIM|DV_(HI|LO)|HYST?$")),
    ("Tuning", re.compile(
        r"GAIN|RESET|(^|_)RATE(_|$)|^K[PID]$|^T[ID]$|ALPHA|BETA|GAMMA|"
        r"BIAS|DEADBAND|DEADBND|PROPORTIONAL|DERIV")),
    ("Filtering", re.compile(r"FTIME|FILTER|FLTR|(^|_)LAG|LEAD|SMOOTH|DAMP|TAU")),
    ("Scaling & Limits", re.compile(
        r"SCALE|LIMIT|(^|_)LIM(_|$)|RANGE|CUTOFF|CUT$|CLAMP|EU100|EU0|SPAN|"
        r"_(HI|LO|MIN|MAX)$")),
    ("Timing", re.compile(
        r"TIME|TIMER|DELAY|PERIOD|DURATION|TIMEOUT|SCAN|DEBOUNCE|^PT$|^ET$")),
    ("Mode & Action", re.compile(r"^MODE$|ACTION|^FORM$|STRUCTURE|ACTING")),
    ("Connectors", re.compile(
        r"^TAG$|CHANNEL|^IO_|_IO$|^IN(_|$)|^OUT$|SIMULATE|^SIM_|SHOW_.*PIN|"
        r"NUM_INPUTS|READBACK|^UNIT_SET$|ENG_UNITS")),
    ("Status & Options", re.compile(r"STATUS|(^|_)OPTS?(_|$)|ENABLE|INVERT")),
)

# Where each derived group sits in the panel, top to bottom.
_GROUP_ORDER = (
    _QUICK, "Connectors", _COMMON, "Tuning", "Filtering", "Scaling & Limits",
    "Timing", "Mode & Action", "Alarms", "Status & Options", _DOCS,
)

# Parameter families (shared name prefix) worth their own group only on the
# big blocks — below this many parameters the semantic rules read better.
_CLUSTER_MIN_SCHEMA = 12
_CLUSTER_MIN_MEMBERS = 4
_CLUSTER_SPLIT_ABOVE = 20      # split a huge family into index buckets
_CLUSTER_BUCKET = 8            # …of this many indices

_METADATA = {"description", "label", "desc", "comment"}

# Block attributes that are never a live parameter value.
_LIVE_SKIP_ATTRS = {"config", "status", "runtime_context", "inputs", "outputs",
                    "id", "instance_name", "x", "y"}


# Families whose prefix is an alarm/signal word are better served by the
# semantic rules (HI_LIM belongs in Alarms, not in a group called "HI").
_CLUSTER_STOP = {"HI", "LO", "OUT", "IN", "PV", "SP", "MODE", "NUM", "STATUS",
                 "ALARM", "DV", "OPT", "SIM", "IO"}

# Nicer names for a few families whose prefix is an abbreviation.
_FAMILY_NAMES = {"DESC": "Descriptions", "PERM": "Permissives",
                 "ILK": "Interlocks", "CFM": "Confirm Times"}

# First words too generic to name a family after ("Seconds before …").
_GENERIC_HEAD = {"seconds", "second", "minutes", "max", "min", "maximum",
                 "minimum", "number", "time", "true", "false", "value",
                 "initial", "enable", "optional", "set", "use", "when",
                 "target", "the", "this"}


def _family_label(prefix: str, descs: list[str]) -> str:
    """Human name for a parameter family.

    A cryptic one- or two-letter prefix is named from its members'
    descriptions instead — DCC's ``I_EXP1`` … are all described as
    "Interlock n …", which beats calling the group "I".
    """
    if len(prefix) <= 2:
        heads: dict[str, int] = {}
        for d in descs:
            w = re.split(r"[\s,:/(]", (d or "").strip(), maxsplit=1)[0]
            if len(w) >= 3 and w.isalpha() and w.lower() not in _GENERIC_HEAD:
                heads[w.capitalize()] = heads.get(w.capitalize(), 0) + 1
        if heads:
            best, n = max(heads.items(), key=lambda kv: kv[1])
            if n >= max(2, int(len(descs) * 0.6)):
                return best
    return _FAMILY_NAMES.get(prefix, _title(prefix))


def _index_of(name: str) -> int | None:
    m = re.search(r"(?:[_A-Z])(\d+)$", name.upper())
    return int(m.group(1)) if m else None


def _family_key(name: str) -> str | None:
    """The family a parameter belongs to: ``I_EXP1`` → I, ``DESC3`` → DESC."""
    if "_" in name:
        return name.split("_", 1)[0].upper()
    stem = re.sub(r"\d+$", "", name)
    return stem.upper() if stem != name and len(stem) >= 2 else None


def _cluster_families(schema: dict, names: list[str]) -> dict[str, list[str]]:
    """Group repeated parameter families (``I_*``, ``GC_*``, ``DESC1..16``)."""
    if len(schema) <= _CLUSTER_MIN_SCHEMA:
        return {}
    by_prefix: dict[str, list[str]] = {}
    for n in names:
        key = _family_key(n)
        if key and key not in _CLUSTER_STOP:
            by_prefix.setdefault(key, []).append(n)

    groups: dict[str, list[str]] = {}
    for prefix, members in by_prefix.items():
        if len(members) < _CLUSTER_MIN_MEMBERS:
            continue
        label = _family_label(prefix, [schema[m][2] for m in members])
        if label in groups:                     # two families, one name
            label = _title(prefix)
        while label in groups:
            label = f"{label} ({prefix})"
        indexed = [m for m in members if _index_of(m) is not None]
        if (len(members) <= _CLUSTER_SPLIT_ABOVE
                or len(indexed) < 0.6 * len(members)):
            groups[label] = members
            continue
        # Huge indexed family (DCC's 130 interlock parameters) → buckets.
        for m in members:
            idx = _index_of(m)
            if idx is None:
                groups.setdefault(label, []).append(m)
            else:
                lo = ((idx - 1) // _CLUSTER_BUCKET) * _CLUSTER_BUCKET + 1
                groups.setdefault(
                    f"{label} {lo}-{lo + _CLUSTER_BUCKET - 1}", []).append(m)
    return groups


def _semantic_group(name: str, ptype) -> str:
    upper = name.upper()
    if name in _METADATA or upper.endswith("_DESC") or upper.endswith("_DESCRIPTION"):
        return _DOCS
    for gname, rx in _SEMANTIC_RULES:
        if rx.search(upper):
            return gname
    if ptype is bool:
        return "Status & Options"
    return _COMMON


def derive_param_groups(block, schema: dict | None = None
                        ) -> list[tuple[str, list[str]]]:
    """Parameter groups for any block, in display order.

    Precedence: an explicit override (``_EXPLICIT_GROUPS`` or a block-declared
    ``config_groups``) → Azeo option bitstrings (``OPT_*``) → repeated
    parameter families → a name/type heuristic. Every block therefore gets a
    usable grouping instead of one flat list.
    """
    if schema is None:
        try:
            schema = block.get_config_schema()
        except Exception:
            schema = {}
    names = list(schema)
    placed: set[str] = set()
    ordered: list[tuple[str, list[str]]] = []
    extra: list[tuple[str, list[str]]] = []

    def _take(gname: str, members: list[str], into: list):
        members = [m for m in members if m in schema and m not in placed]
        if not members:
            return
        placed.update(members)
        for i, (existing, lst) in enumerate(into):
            if existing == gname:
                into[i] = (existing, lst + members)
                return
        into.append((gname, members))

    # 0. the I/O tag belongs with the block name in Quick Configuration ----
    _take(_QUICK, [n for n in names if n.lower() == "tag"], ordered)

    # 1. explicit override -------------------------------------------------
    explicit = _EXPLICIT_GROUPS.get(getattr(block, "block_type", ""))
    if explicit is None:
        declared = getattr(block, "config_groups", None)
        explicit = declared if isinstance(declared, dict) else None
    if explicit:
        for gname, members in explicit.items():
            _take(gname, list(members), ordered)

    # 2. option bitstrings -------------------------------------------------
    bitstrings: list[tuple[str, list[str]]] = []
    for n in names:
        if n in placed:
            continue
        meta = option_bitstring(n, schema[n][2])
        if meta:
            _take(meta[0], [n], bitstrings)

    # 3. repeated parameter families --------------------------------------
    remaining = [n for n in names if n not in placed]
    for gname, members in _cluster_families(schema, remaining).items():
        _take(gname, members, extra)

    # 4. name / type heuristic --------------------------------------------
    heuristic: dict[str, list[str]] = {}
    for n in names:
        if n in placed:
            continue
        heuristic.setdefault(_semantic_group(n, schema[n][0]), []).append(n)
    for gname in _GROUP_ORDER:
        if gname in heuristic:
            _take(gname, heuristic[gname], ordered)
    for gname, members in heuristic.items():        # anything unordered
        _take(gname, members, ordered)

    # Quick Configuration always exists (it carries the block name row) and
    # leads; then the ordered groups, families, bitstrings, docs last.
    def _key(item):
        gname = item[0]
        return (_GROUP_ORDER.index(gname) if gname in _GROUP_ORDER
                else len(_GROUP_ORDER))

    if not explicit:
        ordered.sort(key=_key)
    docs = [g for g in ordered if g[0] == _DOCS]
    ordered = [g for g in ordered if g[0] != _DOCS]
    return ordered + extra + bitstrings + docs


def _set_row_visible(form: QFormLayout, row: int, visible: bool) -> None:
    """Show/hide one QFormLayout row (label + field)."""
    try:
        form.setRowVisible(row, visible)          # Qt >= 6.4
        return
    except (AttributeError, TypeError):           # pragma: no cover - old Qt
        pass
    for role in (QFormLayout.LabelRole, QFormLayout.FieldRole):
        item = form.itemAt(row, role)
        if item is not None and item.widget() is not None:
            item.widget().setVisible(visible)


@dataclass
class _ParamRow:
    """One parameter row, remembered so the filter box can hide it."""

    name: str
    form: QFormLayout
    row: int
    group: object | None
    editor: QWidget
    unit: str
    label: str = ""              # displayed name (option wording for OPT_*)
    type_name: str = ""          # "float" / "boolean" / "named set" …
    access: str = "RW"           # "RW" | "RO" | "OOS"
    live: object | None = None   # row QLabel (name + type chip + live value)
    chip: str = ""               # short data-type chip ("flt", "bool", …)
    visible: bool = True         # current filter state (avoids Qt churn)
    live_text: str = ""          # live value currently rendered


class _ScriptEditorDialog(QWidget):
    """Popup script editor dialog for ACT blocks.

    Opens as a separate top-level window with mode selector, code editor,
    status bar, and help.
    """

    scriptChanged = Signal(str, int)  # (script_text, script_mode)

    def __init__(self, block_name: str = "", expression: str = "",
                 script: str = "", script_mode: int = 1, parent=None):
        super().__init__(parent, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle(f"Script Editor — {block_name}")
        self.resize(560, 420)
        self._script_mode = script_mode

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Mode selector row
        mode_row = QHBoxLayout()
        lbl = QLabel("Mode:")
        lbl.setStyleSheet(f"font-weight: bold; color: {UI.text_secondary}; font-size: 9pt;")
        mode_row.addWidget(lbl)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["IEC ST / Expression",
                                   "Azeo Python Extension"])
        self._mode_combo.setCurrentIndex(script_mode - 1)
        self._mode_combo.setStyleSheet("font-size: 9pt;")
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode_combo, 1)

        btn_help = QToolButton()
        btn_help.setText("?")
        btn_help.setFixedSize(24, 24)
        btn_help.setToolTip("Script syntax help")
        btn_help.setStyleSheet(
            f"QToolButton {{ border: 1px solid {UI.border}; border-radius: 12px; "
            f"background: {UI.hover}; color: {UI.blue}; font-weight: bold; font-size: 9pt; }}"
            f"QToolButton:hover {{ background: {UI.selection}; }}")
        btn_help.clicked.connect(self._show_help)
        mode_row.addWidget(btn_help)
        layout.addLayout(mode_row)

        # Azeo-style ribbon expression editor (Option A).
        # Provides ribbon toolbar with Operators / Function Library /
        # DCS Functions / Editing groups, Find/Replace/Go To, Parse +
        # parser output panel, tag-picker integration, recently used.
        from ..widgets.expression_editor_ribbon import RibbonExpressionEditor
        initial_text = expression if script_mode == 1 else script
        # Grab store from parent if available so the tag picker can browse
        # live tags rather than fall back to free-text entry.
        parent_w = self.parentWidget()
        store = getattr(parent_w, "_store", None) if parent_w is not None else None
        self._py_editor = RibbonExpressionEditor(
            initial_text=initial_text,
            mode="expression" if script_mode == 1 else "script",
            store=store)
        # Back-compat alias used by callers that poke ``_editor``.
        self._editor = self._py_editor.editor
        layout.addWidget(self._py_editor, 1)

        # Store both texts
        self._expression_text = expression
        self._script_text = script

        # Status bar
        self._status = QLabel("OK")
        self._status.setStyleSheet(
            "color: #4caf50; font-size: 9pt; padding: 2px 4px;")
        layout.addWidget(self._status)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_apply = QPushButton("Apply")
        btn_apply.setStyleSheet(
            f"QPushButton {{ background: {UI.blue}; color: #fff; "
            "border: none; border-radius: 3px; padding: 6px 20px; "
            "font-size: 9pt; font-weight: bold; }"
            f"QPushButton:hover {{ background: {UI.blue}; }}"
            f"QPushButton:pressed {{ background: {UI.blue}; }}")
        btn_apply.clicked.connect(self._on_apply)
        btn_row.addWidget(btn_apply)

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(
            "QPushButton { background: #757575; color: #fff; "
            "border: none; border-radius: 3px; padding: 6px 20px; "
            "font-size: 9pt; }"
            "QPushButton:hover { background: #616161; }")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

        self.setStyleSheet(
            "QWidget { background: #f5f5f5; }"
            f"QLabel {{ color: {UI.text_secondary}; }}"
            f"QComboBox {{ background: #fff; border: 1px solid {UI.border}; "
            "border-radius: 2px; padding: 2px 6px; }"
        )

    def _on_mode_changed(self, idx: int):
        if self._script_mode == 1:
            self._expression_text = self._editor.toPlainText()
        else:
            self._script_text = self._editor.toPlainText()
        self._script_mode = idx + 1
        if self._script_mode == 1:
            self._editor.setPlainText(self._expression_text)
        else:
            self._editor.setPlainText(self._script_text)
        # Tell the ribbon editor too so its parser uses the right mode and
        # the status label updates.
        if hasattr(self, "_py_editor") and hasattr(self._py_editor, "set_mode"):
            self._py_editor.set_mode("expression" if self._script_mode == 1 else "script")

    def _on_apply(self):
        text = self._editor.toPlainText()
        self.scriptChanged.emit(text, self._script_mode)

    def set_status(self, error: str):
        if error:
            self._status.setText(f"Error: {error}")
            self._status.setStyleSheet(
                "color: #f44336; font-size: 9pt; padding: 2px 4px;")
        else:
            self._status.setText("OK")
            self._status.setStyleSheet(
                "color: #4caf50; font-size: 9pt; padding: 2px 4px;")

    def set_script(self, expression: str, script: str, mode: int):
        self._expression_text = expression
        self._script_text = script
        self._script_mode = mode
        self._mode_combo.setCurrentIndex(mode - 1)
        if mode == 1:
            self._editor.setPlainText(expression)
        else:
            self._editor.setPlainText(script)

    def _show_help(self):
        # Open the proper modeless help dialog (tree + searchable rich-text)
        # so the operator can keep it open beside the editor while writing.
        from ..widgets.expression_help_dialog import open_expression_help
        open_expression_help(self)


class PropertiesPanel(QWidget):
    """Dockable panel showing block palette (collapsible categories) and
    configuration of the selected block.

    Can be repositioned to left or right side via the context menu
    or the swap button in the header.
    """

    configChanged = Signal(str)  # block_id
    dockSideRequested = Signal(str)  # "left" or "right"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._block: FunctionBlock | None = None
        self._multi_blocks: list[FunctionBlock] = []
        self._widgets: dict[str, QWidget] = {}
        self._groups: dict[str, _CollapsibleGroup] = {}
        self._rows: list[_ParamRow] = []
        self._auto_expanded: set[str] = set()   # groups the filter opened
        self._group_visible: dict[str, bool] = {}
        self._unit_labels: dict[str, QLabel] = {}
        self._live_labels: dict[str, QLabel] = {}
        self._row_labels: dict[str, QLabel] = {}
        # Live cells are only built while the module is on scan; the form is
        # rebuilt when that changes (see refresh()).
        self._live_cells = False
        self._unit_cache: dict[str, str] = {}
        self._show_units = False
        self._script_dialog: _ScriptEditorDialog | None = None
        self._scene = None  # Set externally for undo support
        # On scan? Set by the designer (set_online) or inferred from the
        # scene's live mode; drives the live-value column and the online
        # write restrictions on OOS-only parameters.
        self._online: bool | None = None

        # Parameter edits are coalesced: a spin box emits valueChanged on every
        # keystroke, so typing "12.5" would otherwise reconfigure the block
        # three times (three re-applies, three undo entries) — and on a live
        # loop each re-apply is a chance to disturb it.
        self._pending: dict[str, object] = {}
        self._pending_old: dict | None = None
        self._pending_block_id: str | None = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._flush_params)

        # The designer ticks refresh() only while a module is on scan, so the
        # panel watches for the on→off transition itself and clears the live
        # column (one bool comparison per second when nothing changes).
        self._last_online = False
        self._state_timer = QTimer(self)
        self._state_timer.setInterval(1000)
        self._state_timer.timeout.connect(self._poll_online_state)
        self._state_timer.start()

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Header with dock swap button
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)

        self._btn_dock_left = QToolButton()
        self._btn_dock_left.setText("\u25C0")
        self._btn_dock_left.setToolTip("Dock panel to left side")
        self._btn_dock_left.setFixedSize(20, 20)
        self._btn_dock_left.setStyleSheet(
            f"QToolButton {{ border: none; color: {UI.text_muted}; font-size: 9pt; }}"
            f"QToolButton:hover {{ color: {UI.blue}; }}")
        self._btn_dock_left.clicked.connect(lambda: self.dockSideRequested.emit("left"))
        hdr_row.addWidget(self._btn_dock_left)

        self._hdr = QLabel("PROPERTIES")
        self._hdr.setAlignment(Qt.AlignCenter)
        self._hdr.setStyleSheet(
            f"font-weight: bold; font-size: 9pt; color: {UI.text_secondary}; "
            f"padding: 3px; background: {UI.border_light}; "
            f"border: 1px solid {UI.border}; border-radius: 2px;"
        )
        hdr_row.addWidget(self._hdr, 1)

        self._btn_dock_right = QToolButton()
        self._btn_dock_right.setText("\u25B6")
        self._btn_dock_right.setToolTip("Dock panel to right side")
        self._btn_dock_right.setFixedSize(20, 20)
        self._btn_dock_right.setStyleSheet(
            f"QToolButton {{ border: none; color: {UI.text_muted}; font-size: 9pt; }}"
            f"QToolButton:hover {{ color: {UI.blue}; }}")
        self._btn_dock_right.clicked.connect(lambda: self.dockSideRequested.emit("right"))
        hdr_row.addWidget(self._btn_dock_right)

        layout.addLayout(hdr_row)

        # Block info
        self._info_label = QLabel("No block selected")
        self._info_label.setStyleSheet(f"color: {UI.text_secondary}; padding: 4px;")
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label)

        # Parameter filter (Azeo's parameter view has one) — EDC / AGA_SI
        # carry 40-200 params, unusable without a way to narrow them down.
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter parameters…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setToolTip("Show only parameters whose name contains this text")
        self._filter_edit.textChanged.connect(lambda _t: self._apply_filter())
        layout.addWidget(self._filter_edit)

        # Scroll area for config form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        self._form_container = QWidget()
        self._form_container_layout = QVBoxLayout(self._form_container)
        self._form_container_layout.setContentsMargins(2, 2, 2, 2)
        self._form_container_layout.setSpacing(2)

        # Flat form (for non-PID blocks)
        self._flat_form_widget = QWidget()
        self._flat_form_layout = QFormLayout(self._flat_form_widget)
        self._flat_form_layout.setContentsMargins(4, 4, 4, 4)
        self._flat_form_layout.setSpacing(4)
        self._flat_form_layout.setLabelAlignment(Qt.AlignRight)
        self._form_container_layout.addWidget(self._flat_form_widget)

        self._form_container_layout.addStretch()
        scroll.setWidget(self._form_container)
        layout.addWidget(scroll, 1)

        # Apply styling
        self.setStyleSheet(f"""
            QLabel {{ color: {UI.text_secondary}; font-size: 9pt; }}
            QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
                background: #ffffff; color: #333;
                border: 1px solid {UI.border}; border-radius: 2px;
                padding: 2px 4px; font-size: 9pt;
            }}
            QComboBox::drop-down {{
                border: none; width: 16px;
            }}
            QComboBox QAbstractItemView {{
                background: #ffffff; color: #333;
                border: 1px solid {UI.border};
                selection-background-color: {UI.blue};
                selection-color: #ffffff;
            }}
            QCheckBox {{ color: {UI.text_secondary}; font-size: 9pt; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; }}
            QLabel[dvRole="unit"] {{ color: {UI.text_muted}; font-size: 9pt; }}
            QLabel[dvRole="type"] {{ color: {UI.text_muted}; font-size: 9pt; }}
            QLabel[dvRole="live"] {{
                color: {UI.blue}; font-size: 9pt; font-weight: bold;
            }}
            QLabel[dvRole="livediff"] {{
                color: #e65100; font-size: 9pt; font-weight: bold;
            }}
        """)

    def set_block(self, block: FunctionBlock | None):
        """Set the block to display in the properties panel."""
        # Commit any half-typed edit before the form is torn down, otherwise
        # clicking away from a field silently discards it.
        self.flush_pending()
        # Clear form BEFORE clearing dicts (so _clear_form can find old widgets)
        self._clear_form()

        self._block = block
        self._multi_blocks = []
        self._widgets.clear()
        self._groups.clear()
        self._rows.clear()
        self._auto_expanded.clear()
        self._group_visible.clear()
        self._unit_labels.clear()
        self._live_labels.clear()
        self._row_labels.clear()
        self._unit_cache.clear()
        self._show_units = False

        if block is None:
            self._info_label.setText("No block selected")
            return

        # Block info
        self._update_info_label()

        # Config schema
        self._live_cells = self.is_online()
        schema = block.get_config_schema()

        # Only reserve the Unit column when this block actually declares
        # units — otherwise it is dead space in a 240 px panel.
        self._show_units = any(self._unit_for(p) for p in schema)

        if block.block_type == "ACT":
            self._build_act_form(schema, block)
        elif block.block_type == "CND":
            self._build_cnd_form(schema, block)
        elif block.block_type == "SFC_CHART":
            self._build_sfc_chart_form(schema, block)
        else:
            self._build_grouped_form(schema, block)

        # A filter typed for the previous block still applies to this one.
        self._apply_filter()

        # Selecting a block while the module is on scan shows live values at
        # once rather than on the next designer tick.
        if self.is_online():
            self._update_live_values()

    def set_blocks(self, blocks: list[FunctionBlock]):
        """Edit common parameters across a multi-selection in one undo step."""
        blocks = list(dict.fromkeys(blocks))
        if len(blocks) <= 1:
            self.set_block(blocks[0] if blocks else None)
            return
        self.flush_pending()
        self._clear_form()
        self._block = None
        self._multi_blocks = blocks
        self._widgets.clear()
        self._groups.clear()
        self._rows.clear()
        self._info_label.setText(
            f"{len(blocks)} BLOCKS SELECTED\n"
            "Edit a common value once to apply it to the whole selection."
        )

        schemas = [dict(block.get_config_schema() or {}) for block in blocks]
        common = set(schemas[0])
        for schema in schemas[1:]:
            common.intersection_update(schema)
        compatible = []
        for name in sorted(common, key=str.lower):
            definitions = [schema[name] for schema in schemas]
            if not all(definition[0] is definitions[0][0]
                       for definition in definitions[1:]):
                continue
            compatible.append((name, definitions[0]))

        if not compatible:
            self._flat_form_layout.addRow(QLabel(
                "The selected block types have no common editable parameters."))
            return

        online = self.is_online()
        for name, definition in compatible:
            ptype, default, description = definition
            values = [block.config.params.get(name, default) for block in blocks]
            mixed = any(value != values[0] for value in values[1:])
            editor = QLineEdit("" if mixed else str(values[0]))
            editor.setPlaceholderText("— mixed —" if mixed else "")
            editor.setToolTip(
                f"{description}\nApplies to {len(blocks)} selected blocks"
            )
            editor.setEnabled(not online)
            editor.editingFinished.connect(
                lambda n=name, t=ptype, w=editor: self._apply_multi_value(
                    n, t, w.text()))
            label = QLabel(name)
            label.setToolTip(description)
            self._flat_form_layout.addRow(label, editor)
            self._widgets[name] = editor
        if online:
            self._flat_form_layout.addRow(QLabel(
                "Batch edits are available after the module goes offline."))

    def _apply_multi_value(self, name: str, value_type: type, text: str):
        if not self._multi_blocks or self.is_online() or not text.strip():
            return
        try:
            if value_type is bool:
                normalized = text.strip().lower()
                if normalized not in {"true", "false", "1", "0", "yes", "no"}:
                    raise ValueError("expected true or false")
                value = normalized in {"true", "1", "yes"}
            else:
                value = value_type(text) if value_type in (int, float, str) else text
        except (TypeError, ValueError):
            editor = self._widgets.get(name)
            if editor is not None:
                editor.setStyleSheet("border: 1px solid #C62828;")
                editor.setToolTip(f"Invalid {value_type.__name__} value: {text}")
            return

        snapshots = []
        for block in self._multi_blocks:
            before = dict(block.config.params)
            after = dict(before)
            after[name] = value
            snapshots.append((block.id, before, after))
        if self._scene is not None:
            from ..undo import BatchChangeConfigCommand

            self._scene.undo_stack.push(BatchChangeConfigCommand(
                self._scene,
                snapshots,
                f"Set {name} on {len(snapshots)} Blocks",
            ))
        for block in self._multi_blocks:
            self.configChanged.emit(block.id)
        current = list(self._multi_blocks)
        self.set_blocks(current)

    def _clear_form(self):
        """Clear all form content."""
        while self._flat_form_layout.rowCount() > 0:
            self._flat_form_layout.removeRow(0)

        # Remove and hide grouped widgets immediately. Keep their native
        # parent until DeferredDelete runs: detaching first transfers C++
        # ownership to a short-lived Python wrapper and can double-delete the
        # widget on the next event-loop pass.
        for grp in self._groups.values():
            self._form_container_layout.removeWidget(grp)
            grp.hide()
            grp.deleteLater()

        # Close script editor dialog if open
        if self._script_dialog is not None:
            self._script_dialog.close()
            self._script_dialog.deleteLater()
            self._script_dialog = None

    # ─── Named sets / units — Azeo parameter-view metadata ────────
    def _choices_for(self, name: str) -> tuple[str, ...]:
        """Enumerated values for a config param, or () if it is free text.

        Block-declared ``config_choices`` win (case-insensitively, via
        ``FunctionBlock.choices_for``); the legacy hardcoded action / form /
        structure sets are the fallback so they keep working on blocks that
        have not declared them yet.
        """
        block = self._block
        if block is not None:
            getter = getattr(block, "choices_for", None)
            if callable(getter):
                try:
                    vals = tuple(getter(name) or ())
                except Exception:
                    vals = ()
                if vals:
                    return vals
        low = name.lower()
        for key, vals in _FALLBACK_CHOICES.items():
            if key.lower() == low:
                return vals
        return ()

    def _unit_for(self, name: str) -> str:
        """Engineering unit for a config param ("" when it has none)."""
        block = self._block
        if block is None:
            return ""
        hit = self._unit_cache.get(name)
        if hit is not None:
            return hit
        getter = getattr(block, "unit_for", None)
        unit = ""
        if callable(getter):
            try:
                unit = str(getter(name) or "")
            except Exception:
                unit = ""
        self._unit_cache[name] = unit
        return unit

    def _type_name(self, name: str, ptype) -> tuple[str, str]:
        """(chip, full name) for the parameter's data type."""
        if self._choices_for(name) or (ptype is str and name.lower() == "mode"):
            return "enum", "named set"
        return _TYPE_CHIPS.get(ptype, ("str", "string"))

    def param_type_name(self, name: str) -> str:
        """Full data-type name shown for a parameter ('float', 'named set')."""
        row = next((r for r in self._rows if r.name == name), None)
        return row.type_name if row else ""

    def _access_for(self, name: str, desc: str) -> str:
        """'RW' | 'RO' (computed) | 'OOS' (writable off scan / in OOS-Man)."""
        block = self._block
        declared = getattr(block, "config_readonly", ()) if block else ()
        try:
            if any(str(k).lower() == name.lower() for k in declared):
                return "RO"
        except TypeError:                       # pragma: no cover - odd decl
            pass
        if desc and _RO_RE.search(desc):
            return "RO"
        if desc and _OOS_RE.search(desc):
            return "OOS"
        return "RW"

    def is_read_only(self, name: str) -> bool:
        """True when the panel refuses to edit this parameter right now."""
        row = next((r for r in self._rows if r.name == name), None)
        if row is None:
            return False
        if row.access == "RO":
            return True
        return row.access == "OOS" and self.is_online() and \
            self._block_mode() not in _OOS_WRITE_MODES

    def _wrap_with_unit(self, name: str, editor: QWidget) -> QWidget:
        """Editor + optional live-value and Unit cells (Azeo value column).

        Off scan and without units this is just the editor: a 216-parameter
        DCC would otherwise pay for hundreds of extra widgets every time it
        is selected. The live cell is built when the module is on scan (the
        form is rebuilt on the on/off-scan transition).
        """
        if not self._show_units and not self._live_cells:
            return editor
        cont = QWidget()
        row = QHBoxLayout(cont)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        row.addWidget(editor, 1)
        if self._live_cells:
            live = QLabel("")
            live.setFixedWidth(44)
            live.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            live.setProperty("dvRole", "live")
            live.setToolTip("Live value while the module is on scan")
            self._live_labels[name] = live
            row.addWidget(live, 0)
        if self._show_units:
            unit = self._unit_for(name)
            lbl = QLabel(unit)
            lbl.setFixedWidth(28)
            lbl.setToolTip(unit)
            lbl.setProperty("dvRole", "unit")
            self._unit_labels[name] = lbl
            row.addWidget(lbl, 0)
        return cont

    def _row_label_html(self, text: str, chip: str, access: str) -> str:
        """Row label: parameter name + its data-type / access chip.

        The type cell rides inside the label rather than being its own
        widget — one QLabel per row instead of two. The name is elided to
        ``_LABEL_MAX_PX`` so a long option wording ("Bypass Timeout
        Indication Only") cannot push the editor out of a 240 px panel.
        """
        suffix = chip + ("" if access == "RW" else " " + access.lower())
        if len(text) > _MAX_LABEL_CHARS:
            text = text[:_MAX_LABEL_CHARS - 1] + "…"
        return (f"{text}: <span style='color:#b0bec5; font-size:9pt;'>"
                f"{suffix}</span>")

    def _add_param_row(self, form: QFormLayout, name: str, ptype, current,
                       desc: str, group=None, widget: QWidget | None = None):
        """Add one label / editor / unit row and register it for filtering."""
        if widget is None:
            widget = self._create_widget(name, ptype, current, desc)
        self._widgets[name] = widget

        # An option bit shows the manual's option wording, not OPT_FOO_BAR.
        # Everything else displays Azeo-proper — uppercase, as every
        # Azeo parameter list reads. Display only: the config key is
        # never renamed (hard-won item 4), and the tooltip keeps the raw
        # key as the scripting truth.
        opt = option_bitstring(name, desc)
        label_text = opt[1] if opt else name.upper()
        access = self._access_for(name, desc)
        chip, type_full = self._type_name(name, ptype)
        unit = self._unit_for(name)

        label = QLabel(self._row_label_html(label_text, chip, access))
        label.setMaximumWidth(_LABEL_MAX_PX)
        self._row_labels[name] = label
        tip = (f"<b>{name}</b><br/>Type: {type_full}"
               + (f"<br/>Unit: {unit}" if unit else "")
               + "<br/>Access: " + {
                   "RW": "read / write",
                   "RO": "read-only — computed by the block",
                   "OOS": "settable off scan, or on scan in OOS / Man",
               }[access]
               + f"<br/>{desc}")
        label.setToolTip(tip)
        widget.setToolTip(tip)
        widget.setProperty("dvTip", tip)      # restored when it unlocks
        form.addRow(label, self._wrap_with_unit(name, widget))
        self._rows.append(_ParamRow(
            name=name, form=form, row=form.rowCount() - 1, group=group,
            editor=widget, unit=unit, label=label_text,
            type_name=type_full, access=access, chip=chip,
            live=self._live_labels.get(name),
        ))
        self._apply_access(self._rows[-1])
        return widget

    def _apply_access(self, row: _ParamRow, ctx: tuple[bool, str] | None = None):
        """Disable the editor of a parameter that cannot be written now."""
        online, mode = ctx or (self.is_online(), self._block_mode())
        if row.access == "RO":
            blocked, why = True, "Read-only — computed by the block"
        elif row.access == "OOS" and online and mode not in _OOS_WRITE_MODES:
            blocked = True
            why = ("Settable in OOS / Man only — the module is on scan and "
                   f"the block is in {mode}")
        else:
            blocked, why = False, ""
        if row.editor.isEnabled() == (not blocked):
            return
        row.editor.setEnabled(not blocked)
        row.editor.setToolTip(why if blocked
                              else str(row.editor.property("dvTip") or ""))

    def unit_label_for(self, name: str) -> QLabel | None:
        """The Unit-column label for a parameter (None when not shown)."""
        return self._unit_labels.get(name)

    def type_chip_for(self, name: str) -> str:
        """Short data-type chip shown on the row ('flt', 'bool', 'enum')."""
        row = next((r for r in self._rows if r.name == name), None)
        return row.chip if row else ""

    def row_label_for(self, name: str) -> QLabel | None:
        """The row's name label (carries the data-type chip)."""
        return self._row_labels.get(name)

    def live_label_for(self, name: str) -> QLabel | None:
        """The live-value cell for a parameter (None while off scan)."""
        return self._live_labels.get(name)

    def live_text_for(self, name: str) -> str:
        """The live value currently displayed ('' while off scan)."""
        row = next((r for r in self._rows if r.name == name), None)
        return row.live_text if row else ""

    def group_names(self) -> list[str]:
        """Parameter group titles currently displayed, in panel order."""
        return list(self._groups)

    def _apply_filter(self):
        """Hide rows whose parameter name — or displayed option wording —
        doesn't contain the filter text."""
        needle = self._filter_edit.text().strip().lower()
        matched: dict[int, bool] = {}
        for r in self._rows:
            vis = (not needle) or needle in r.name.lower() \
                or needle in r.label.lower()
            if vis != r.visible:
                # Only touch Qt when the state actually changes — a 216-row
                # DCC re-lays out on every setRowVisible().
                _set_row_visible(r.form, r.row, vis)
                r.visible = vis
            if r.group is not None:
                matched[id(r.group)] = matched.get(id(r.group), False) or vis

        for gname, grp in self._groups.items():
            hit = matched.get(id(grp), True)
            # Hide groups with nothing left to show; expand the ones that do,
            # otherwise a match inside a collapsed group stays invisible.
            want = bool(hit if needle else True)
            if self._group_visible.get(gname) != want:
                # Showing a group with 64 rows re-lays it out — only do it
                # when the state actually changed.
                grp.setVisible(want)
                self._group_visible[gname] = want
            if needle and hit and grp.is_collapsed():
                grp.set_collapsed(False)
                self._auto_expanded.add(gname)
            elif not needle and gname in self._auto_expanded:
                # Only re-collapse what the filter itself opened.
                grp.set_collapsed(True)
                self._auto_expanded.discard(gname)

    # ─── Grouped parameter view (every block, not just PID) ─────────
    def _new_group(self, name: str, *, collapsed: bool) -> _CollapsibleGroup:
        """Create, register and insert one collapsible group."""
        grp = _CollapsibleGroup(name)
        if collapsed:
            grp.set_collapsed(True)
        insert_idx = self._form_container_layout.count() - 1
        self._form_container_layout.insertWidget(insert_idx, grp)
        self._groups[name] = grp
        self._group_visible[name] = True       # newly inserted groups show
        return grp

    def _quick_group(self, block: FunctionBlock) -> _CollapsibleGroup:
        """Quick Configuration — block name first, Azeo-style."""
        grp = self._groups.get(_QUICK) or self._new_group(_QUICK, collapsed=False)
        if "Name" not in self._widgets:
            name_edit = QLineEdit(block.instance_name)
            name_edit.setToolTip("Block name (tag) — unique within the module")
            name_edit.editingFinished.connect(
                lambda: self._on_name_changed(name_edit.text()))
            self._widgets["Name"] = name_edit
            grp.form().addRow("Name:", name_edit)
        return grp

    def _build_grouped_form(self, schema: dict, block: FunctionBlock,
                            skip: tuple[str, ...] = ()):
        """Build the collapsible grouped form for any block type."""
        self._quick_group(block)
        groups = derive_param_groups(block, schema)
        expanded = (_PID_EXPANDED if block.block_type in _EXPLICIT_GROUPS
                    else None)
        shown = 0
        for gname, members in groups:
            members = [m for m in members if m not in skip]
            if not members:
                continue
            if expanded is not None:
                collapsed = gname not in expanded
            else:
                # Open the first two groups; the rest (options, families,
                # documentation) start closed so a 216-parameter block is a
                # short list of headers.
                collapsed = shown >= 2
            grp = (self._groups.get(gname)
                   or self._new_group(gname, collapsed=collapsed))
            if gname == _QUICK:
                grp.set_collapsed(False)
            for pname in members:
                ptype, default, desc = schema[pname]
                current = block.config.params.get(pname, default)
                # ``mode`` (any case) becomes a combo inside _create_widget.
                self._add_param_row(grp.form(), pname, ptype, current, desc,
                                    group=grp)
            shown += 1

    def _build_sfc_chart_form(self, schema: dict, block: FunctionBlock):
        """SFC_CHART: the chart is edited in its own editor, never as a
        JSON text row."""
        self._build_grouped_form(schema, block, skip=("CHART",))
        grp = self._new_group("Chart", collapsed=False)
        form = grp.form()
        chart = block.chart()
        summary = QLabel(f"{len(chart.steps)} step(s), "
                         f"{len(chart.transitions)} transition(s)")
        summary.setStyleSheet(f"font-size: 9pt; color: {UI.blue};")
        form.addRow(summary)
        self._sfc_summary_lbl = summary
        btn = QPushButton("Edit Chart...")
        btn.setStyleSheet(
            f"QPushButton {{ background: {UI.blue}; color: #fff; "
            "border: none; border-radius: 3px; padding: 6px 16px; "
            "font-size: 9pt; font-weight: bold; }"
            f"QPushButton:hover {{ background: {UI.blue}; }}")
        btn.clicked.connect(self._open_sfc_chart_editor)
        form.addRow(btn)

    def _open_sfc_chart_editor(self):
        from ..dialogs.sfc_chart_editor import SfcChartEditorDialog

        block = self._block
        if block is None:
            return
        graph = getattr(self._scene, "graph", None)
        store = getattr(self._scene, "_store", None) or getattr(
            self, "_store", None)
        dialog = SfcChartEditorDialog(block, graph, store, parent=self)

        def applied(chart) -> None:
            block.set_chart(chart)
            block._apply_config()
            self.configChanged.emit(block.id)
            if hasattr(self, "_sfc_summary_lbl"):
                self._sfc_summary_lbl.setText(
                    f"{len(chart.steps)} step(s), "
                    f"{len(chart.transitions)} transition(s)")

        dialog.applied.connect(applied)
        self._sfc_chart_dialog = dialog
        from azeo_control_trainer.core.presentation.headless import is_headless

        if not is_headless():
            dialog.exec()

    def _build_cnd_form(self, schema: dict, block: FunctionBlock):
        """CND: the usual form, with the Azeo expression editor.

        The EXPRESSION string is edited in its own dialog — operator
        palette, parameter browsing, Parse — never as a bare text row: a
        typo in a text row is a Bad verdict on scan 1, and the dialog's
        parser catches it at edit time.
        """
        self._build_grouped_form(schema, block, skip=("EXPRESSION",))

        grp = self._new_group("Expression", collapsed=False)
        form = grp.form()
        preview = str(block.config.params.get("EXPRESSION", "")) or "(empty)"
        self._cnd_expr_lbl = QLabel(
            preview if len(preview) <= 48 else preview[:45] + "…")
        self._cnd_expr_lbl.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 9pt; "
            f"color: {UI.blue}; padding: 2px 0;")
        self._cnd_expr_lbl.setToolTip(preview)
        form.addRow(self._cnd_expr_lbl)
        btn_edit = QPushButton("Edit Expression...")
        btn_edit.setStyleSheet(
            f"QPushButton {{ background: {UI.blue}; color: #fff; "
            "border: none; border-radius: 3px; padding: 6px 16px; "
            "font-size: 9pt; font-weight: bold; }"
            f"QPushButton:hover {{ background: {UI.blue}; }}")
        btn_edit.clicked.connect(self._open_expression_editor)
        form.addRow(btn_edit)

    def _open_expression_editor(self):
        """The Azeo-style expression dialog for the current CND block."""
        from ..dialogs.expression_editor import ExpressionEditorDialog

        block = self._block
        if block is None:
            return
        graph = getattr(self._scene, "graph", None)
        store = getattr(self._scene, "_store", None) or getattr(
            self, "_store", None)
        dialog = ExpressionEditorDialog(block, graph, store, parent=self)

        def applied(text: str) -> None:
            block.config.params["EXPRESSION"] = text
            block._apply_config()
            self.configChanged.emit(block.id)
            if hasattr(self, "_cnd_expr_lbl"):
                shown = text or "(empty)"
                self._cnd_expr_lbl.setText(
                    shown if len(shown) <= 48 else shown[:45] + "…")
                self._cnd_expr_lbl.setToolTip(text)

        dialog.applied.connect(applied)
        self._expression_dialog = dialog
        from azeo_control_trainer.core.presentation.headless import is_headless

        if not is_headless():
            dialog.exec()

    def _build_act_form(self, schema: dict, block: FunctionBlock):
        """ACT: the usual grouped form plus a Script group with the editor."""
        # EXPRESSION / SCRIPT / SCRIPT_MODE are edited in the script dialog.
        self._build_grouped_form(
            schema, block, skip=("EXPRESSION", "SCRIPT", "SCRIPT_MODE"))

        grp = self._new_group("Script", collapsed=False)
        form = grp.form()

        # Mode label
        mode = int(block.config.params.get("SCRIPT_MODE", 1))
        mode_text = ("IEC ST / Expression" if mode == 1
                     else "Azeo Python Extension")
        self._act_mode_lbl = QLabel(f"Mode: {mode_text}")
        self._act_mode_lbl.setStyleSheet(
            f"color: {UI.text_secondary}; font-size: 9pt; padding: 2px 0;")
        form.addRow(self._act_mode_lbl)

        # Status label
        self._act_status_lbl = QLabel("OK")
        self._act_status_lbl.setStyleSheet(
            "color: #4caf50; font-size: 9pt; padding: 1px 4px;")
        err = ""
        if hasattr(block, "get_script_error"):
            err = block.get_script_error()
        if err:
            self._act_status_lbl.setText(f"Error: {err}")
            self._act_status_lbl.setStyleSheet(
                "color: #f44336; font-size: 9pt; padding: 1px 4px;")
        form.addRow("Status:", self._act_status_lbl)

        # Open script editor button
        btn_edit = QPushButton("Edit Script...")
        btn_edit.setStyleSheet(
            f"QPushButton {{ background: {UI.blue}; color: #fff; "
            "border: none; border-radius: 3px; padding: 6px 16px; "
            "font-size: 9pt; font-weight: bold; }"
            f"QPushButton:hover {{ background: {UI.blue}; }}"
            f"QPushButton:pressed {{ background: {UI.blue}; }}")
        btn_edit.clicked.connect(self._open_script_editor)
        form.addRow(btn_edit)

    def _open_script_editor(self):
        """Open the script editor popup dialog for the current ACT block."""
        if not self._block or self._block.block_type != "ACT":
            return

        # If dialog already open for this block, just raise it
        if (self._script_dialog is not None
                and self._script_dialog.isVisible()):
            self._script_dialog.raise_()
            self._script_dialog.activateWindow()
            return

        expression = self._block.config.params.get("EXPRESSION", "OUT1 = IN1")
        script = self._block.config.params.get("SCRIPT", "")
        mode = int(self._block.config.params.get("SCRIPT_MODE", 1))

        dlg = _ScriptEditorDialog(
            block_name=self._block.instance_name,
            expression=expression, script=script,
            script_mode=mode, parent=self,
        )
        dlg.scriptChanged.connect(self._on_script_changed)

        err = ""
        if hasattr(self._block, "get_script_error"):
            err = self._block.get_script_error()
        dlg.set_status(err)

        self._script_dialog = dlg
        dlg.show()

    def _on_script_changed(self, text: str, mode: int):
        """Handle script apply from ACT block editor."""
        if self._block and self._block.block_type == "ACT":
            self._block.config.params["SCRIPT_MODE"] = mode
            if mode == 1:
                self._block.config.params["EXPRESSION"] = text
            else:
                self._block.config.params["SCRIPT"] = text
            self._block._apply_config()
            self.configChanged.emit(self._block.id)
            # Update status in dialog and panel
            err = ""
            if hasattr(self._block, "get_script_error"):
                err = self._block.get_script_error()
            if self._script_dialog:
                self._script_dialog.set_status(err)
            # Update panel labels
            if hasattr(self, '_act_mode_lbl'):
                mode_text = ("IEC ST / Expression" if mode == 1
                             else "Azeo Python Extension")
                self._act_mode_lbl.setText(f"Mode: {mode_text}")
            if hasattr(self, '_act_status_lbl'):
                if err:
                    self._act_status_lbl.setText(f"Error: {err}")
                    self._act_status_lbl.setStyleSheet(
                        "color: #f44336; font-size: 9pt; padding: 1px 4px;")
                else:
                    self._act_status_lbl.setText("OK")
                    self._act_status_lbl.setStyleSheet(
                        "color: #4caf50; font-size: 9pt; padding: 1px 4px;")

    def _create_mode_combo(self, current_value, name: str = "mode",
                           *, editable: bool = False,
                           desc: str = "Block mode") -> QComboBox:
        """Create a combo box for DCS mode selection with all 8 modes."""
        return self._create_choice_combo(
            name, str, current_value, desc, _PID_MODES,
            labels=_MODE_LABELS, editable=editable,
            default="AUTO", upper=True)

    @staticmethod
    def _coerce(ptype, value):
        """Cast a combo's string selection back to the schema's type."""
        if ptype in (int, float) and isinstance(value, str):
            try:
                return ptype(value)
            except (TypeError, ValueError):
                return value
        return value

    def _create_choice_combo(self, name: str, ptype, value, desc: str,
                             choices, *, labels: dict | None = None,
                             editable: bool = False, default: str = "",
                             upper: bool = False) -> QComboBox:
        """Drop-down over an enumerated ("named set") parameter."""
        combo = QComboBox()
        combo.setEditable(editable)
        for c in choices:
            combo.addItem((labels or {}).get(c, str(c)), str(c))

        current = str(value) if value not in (None, "") else default
        if upper:
            current = current.upper()
        idx = combo.findData(current)
        if idx < 0 and current:
            low = current.lower()
            idx = next((i for i in range(combo.count())
                        if str(combo.itemData(i)).lower() == low), -1)
        if idx < 0 and current:
            # A value outside the declared set: keep it rather than silently
            # rewriting the block when an odd/legacy file is loaded.
            combo.insertItem(0, current, current)
            combo.setItemData(0, "Value is outside the declared named set",
                              Qt.ToolTipRole)
            idx = 0
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.setToolTip(desc)

        def _emit(_i=None, n=name, c=combo, t=ptype):
            data = c.currentData()
            if data is None:
                data = c.currentText()
            self._on_param_changed(n, self._coerce(t, data))

        combo.currentIndexChanged.connect(_emit)
        if editable:
            combo.editTextChanged.connect(
                lambda text, n=name, t=ptype: self._on_param_changed(
                    n, self._coerce(t, text)))
        return combo

    # Block types that use tag configuration (IO blocks)
    _TAG_BLOCK_TYPES = {
        "AI", "AO", "DI", "DO",
        "TAGIO", "TAGAI", "TAGAO", "TAGDI", "TAGDO",
    }

    def _create_widget(self, name: str, ptype, value, desc: str) -> QWidget:
        """Create appropriate widget for a config parameter."""
        # Use tag picker for "tag" params on IO blocks
        if (name == "tag" and ptype is str
                and self._block and self._block.block_type in self._TAG_BLOCK_TYPES):
            w = TagPickerWidget(str(value) if value else "")
            w.setToolTip(desc)
            w.tagChanged.connect(lambda v, n=name: self._on_param_changed(n, v))
            return w

        # Enumerated ("named set") params → drop-down. Covers every param the
        # block declares in config_choices plus the legacy action / form /
        # structure sets, so a typo can no longer pick the default silently.
        choices = self._choices_for(name)
        if choices:
            return self._create_choice_combo(
                name, ptype, value, desc, choices,
                default=str(choices[0]))

        # ``mode`` / ``MODE`` (any case) — every Azeo block has one.
        if ptype is str and name.lower() == "mode":
            tokens = _tokens_from_description(desc)
            if tokens and not all(t.upper() in _PID_MODES for t in tokens):
                # Not a DCS mode at all (e.g. math MODE: LN, LOG10, EXP).
                return self._create_choice_combo(
                    name, ptype, value, desc, tokens, default=str(tokens[0]))
            # A DCS mode. When the description named no modes we cannot be
            # sure the standard 8 are the whole set, so allow free text too.
            return self._create_mode_combo(value, name, editable=not tokens,
                                           desc=desc)

        if ptype is float:
            w = QDoubleSpinBox()
            w.setRange(-1e9, 1e9)
            w.setDecimals(4)
            w.setValue(float(value) if value else 0.0)
            w.setToolTip(desc)
            w.valueChanged.connect(lambda v, n=name: self._on_param_changed(n, v))
            return w
        elif ptype is int:
            w = QSpinBox()
            w.setRange(-1000000, 1000000)
            w.setValue(int(value) if value else 0)
            w.setToolTip(desc)
            w.valueChanged.connect(lambda v, n=name: self._on_param_changed(n, v))
            return w
        elif ptype is bool:
            w = QCheckBox()
            w.setChecked(bool(value))
            w.setToolTip(desc)
            w.toggled.connect(lambda v, n=name: self._on_param_changed(n, v))
            return w
        else:  # str
            w = QLineEdit(str(value) if value else "")
            w.setToolTip(desc)
            w.editingFinished.connect(lambda n=name, ww=w: self._on_param_changed(n, ww.text()))
            if name == "memory_tag":
                from azeo_control_trainer.core.presentation.memory_tag_dialog import attach_memory_picker
                attach_memory_picker(w, lambda value: self._on_param_changed("memory_tag", value))
            return w

    def _on_name_changed(self, name: str):
        if self._block and name:
            old_name = self._block.instance_name
            if old_name == name:
                return
            self._block.instance_name = name
            if self._scene and hasattr(self._scene, '_undo_stack'):
                from ..undo import RenameBlockCommand
                self._scene._undo_stack.push(RenameBlockCommand(
                    self._scene, self._block.id, old_name, name))
            self.configChanged.emit(self._block.id)

    def _on_param_changed(self, name: str, value):
        """Queue a parameter edit; applied once the user stops typing."""
        if not self._block:
            return
        if self._pending_block_id != self._block.id:
            self.flush_pending()          # different block — commit the old one
            self._pending_old = dict(self._block.config.params)
            self._pending_block_id = self._block.id
        self._pending[name] = value
        self._debounce.start()

    def flush_pending(self):
        """Apply any queued parameter edits immediately."""
        if self._debounce.isActive():
            self._debounce.stop()
        self._flush_params()

    def _flush_params(self):
        pending, old_config = self._pending, self._pending_old
        block_id = self._pending_block_id
        self._pending, self._pending_old, self._pending_block_id = {}, None, None
        if not pending or block_id is None:
            return
        block = self._block
        if block is None or block.id != block_id:
            # Selection moved on — find the block we were editing.
            block = None
            if self._scene is not None:
                block = getattr(self._scene, "graph", None) and \
                    self._scene.graph.blocks.get(block_id)
            if block is None:
                return

        new_config = dict(block.config.params)
        new_config.update(pending)
        if (block.block_type == "TAGIO"
                and "TAG_DEFINITION" in pending
                and str(pending["TAG_DEFINITION"]).strip().upper()
                != "UNASSIGNED"):
            assign = getattr(self._scene, "assign_tag_io", None)
            if not callable(assign):
                return
            try:
                replacement = assign(
                    block_id,
                    str(pending["TAG_DEFINITION"]),
                    config=new_config,
                )
            except (TypeError, ValueError) as exc:
                notice = getattr(self._scene, "_notice", None)
                if callable(notice):
                    notice(f"TAGIO assignment was refused: {exc}")
                replacement = None
            if replacement is None:
                # Online structural lock or invalid definition: return the
                # editor to the real retained placeholder configuration.
                self.set_block(block)
                return
            self.set_block(replacement)
            self.configChanged.emit(block_id)
            return
        block.config.params = new_config
        block._apply_config()
        if self._scene and hasattr(self._scene, '_undo_stack'):
            from ..undo import ChangeConfigCommand
            self._scene._undo_stack.push(ChangeConfigCommand(
                self._scene, block_id, old_config or {},
                dict(block.config.params)))
        self.configChanged.emit(block_id)

    # ─── On-scan state and live parameter values (D6) ───────────────
    def set_online(self, online: bool | None):
        """Tell the panel whether the module is on scan.

        Optional: when the designer does not call this, ``is_online()``
        falls back to the scene's live mode. Passing None restores the
        fallback.
        """
        self._online = None if online is None else bool(online)
        self.refresh()

    def is_online(self) -> bool:
        """True when the selected block's module is on scan."""
        if self._online is not None:
            return self._online
        scene = self._scene
        if scene is None:
            return False
        for probe in ("is_live_mode", "structure_locked"):
            attr = getattr(scene, probe, None)
            if attr is None:
                continue
            try:
                return bool(attr() if callable(attr) else attr)
            except Exception:
                continue
        return False

    def _poll_online_state(self):
        """Repaint the panel when the module goes on or off scan."""
        online = self.is_online()
        if online != self._last_online:
            self._last_online = online
            self.refresh()

    def _block_mode(self) -> str:
        """The block's actual mode (falls back to the configured one)."""
        block = self._block
        if block is None:
            return ""
        try:
            val = block.get_output("MODE")
        except Exception:
            val = None
        if not val:
            params = block.config.params
            val = next((params[k] for k in ("mode", "MODE") if k in params), "")
        return str(val or "").upper()

    @staticmethod
    def _fmt_live(value) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return f"{value:.4g}"
        text = str(value)
        return text if len(text) <= 8 else text[:7] + "…"

    def _live_hosts(self) -> list:
        """The block plus the runtime objects its config is applied to.

        A PID keeps its working tuning on a nested ``PIDBlock`` algorithm
        object, not on the function block, so the live GAIN is only found by
        looking one level in.
        """
        block = self._block
        if block is None:
            return []
        hosts = [block]
        for aname, val in list(vars(block).items()):
            if aname in _LIVE_SKIP_ATTRS or isinstance(
                    val, (str, int, float, bool, dict, list, tuple, set)):
                continue
            if hasattr(val, "__dict__"):
                hosts.append(val)
        return hosts

    def _live_value(self, name: str, hosts: list | None = None):
        """Current value of a parameter on the running block, or None.

        Looks for a terminal of the same name first (MODE, OUT, SP …), then
        an attribute the block's ``_apply_config`` wrote (``gain`` for GAIN),
        on the block itself or on its algorithm object.
        """
        block = self._block
        if block is None or name in _METADATA:
            return None                      # documentation, not a value
        for pool in (block.outputs, block.inputs):
            for tname, term in pool.items():
                if tname.lower() == name.lower():
                    return term.value
        for host in (self._live_hosts() if hosts is None else hosts):
            for attr in (name, name.lower(), name.upper(), "_" + name.lower()):
                val = getattr(host, attr, None)
                if isinstance(val, (int, float, bool, str)):
                    return val
        return None

    def _update_live_values(self):
        """Show each parameter's current value while the module is on scan."""
        ctx = (self.is_online(), self._block_mode())
        params = self._block.config.params if self._block else {}
        # Rebuilt every tick: a block's algorithm object (PID's _pid_core) is
        # created lazily, so a cached host list would miss it.
        hosts = self._live_hosts() if ctx[0] else []
        for r in self._rows:
            lbl = r.live
            self._apply_access(r, ctx)
            if lbl is None:
                continue
            val = self._live_value(r.name, hosts) if ctx[0] else None
            text = "" if val is None else self._fmt_live(val)
            if text == r.live_text:
                continue                      # nothing moved — no Qt work
            r.live_text = text
            lbl.setText(text)
            lbl.setToolTip(f"{r.name} live value: {val}")
            # Orange when the running value has moved away from the
            # configured one — what commissioning is looking for.
            configured = params.get(r.name)
            diff = configured is not None and str(configured) != str(val)
            state = "livediff" if diff else "live"
            if lbl.property("dvRole") != state:
                lbl.setProperty("dvRole", state)
                lbl.style().unpolish(lbl)
                lbl.style().polish(lbl)

    def _update_info_label(self):
        """Header: TYPE — name (+ actual mode / status while on scan)."""
        block = self._block
        if block is None:
            self._info_label.setText("No block selected")
            return
        extra = ""
        if self.is_online():
            mode = self._block_mode()
            status = getattr(block, "status", None)
            status_txt = getattr(status, "value", status) or ""
            extra = ("<br/><small><b>ON SCAN</b>"
                     + (f" &nbsp; mode {mode}" if mode else "")
                     + (f" &nbsp; {status_txt}" if status_txt else "")
                     + "</small>")
        self._info_label.setText(
            f"<b>{block.block_type}</b> — {block.instance_name}<br/>"
            f"<small>{block.description}</small>{extra}"
        )

    def refresh(self):
        """Refresh values from the current block (for live updates)."""
        if not self._block:
            return
        if self.is_online() != self._live_cells:
            # Going on or off scan adds/removes the live column.
            self.set_block(self._block)
            self._update_info_label()
            return
        self._update_info_label()
        self._update_live_values()
        # Update mode combo if PID
        mode_key = next((k for k in ("mode", "MODE") if k in self._widgets), None)
        if self._block.block_type == "PID" and mode_key:
            mode_widget = self._widgets[mode_key]
            if isinstance(mode_widget, QComboBox):
                current = self._block.get_output("MODE")
                idx = mode_widget.findData(str(current).upper() if current else "AUTO")
                if idx >= 0:
                    mode_widget.blockSignals(True)
                    mode_widget.setCurrentIndex(idx)
                    mode_widget.blockSignals(False)
        # Update ACT block status
        if self._block.block_type == "ACT":
            err = ""
            if hasattr(self._block, "get_script_error"):
                err = self._block.get_script_error()
            if self._script_dialog and self._script_dialog.isVisible():
                self._script_dialog.set_status(err)
            if hasattr(self, '_act_status_lbl'):
                if err:
                    self._act_status_lbl.setText(f"Error: {err}")
                    self._act_status_lbl.setStyleSheet(
                        "color: #f44336; font-size: 9pt; padding: 1px 4px;")
                else:
                    self._act_status_lbl.setText("OK")
                    self._act_status_lbl.setStyleSheet(
                        "color: #4caf50; font-size: 9pt; padding: 1px 4px;")
