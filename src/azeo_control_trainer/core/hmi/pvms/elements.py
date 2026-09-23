"""The element catalogue beyond shapes — datalinks and user entries.

Shapes draw the plant; these are the parts an engineer *configures* and
an operator *uses*. The manual splits them into Data elements (live,
read-only) and User Entry elements (writable), and that split is the
one worth keeping: a datalink shows a number, a slew changes one, and
confusing the two is how an operator discovers a control is live by
moving it.

**Datalink types are not decoration.** The manual's table is precise
about what each returns, and the failure modes are specific: a numeric
datalink pointed at `.STR` returns NaN, a scaled numeric returns
unresolved, and mode / named-set / scaling links must NOT carry a
field suffix at all. Encoding that here means a mis-typed datalink is
caught at configuration rather than showing `????????` online.

**The two error renderings are Azeo's own** and they say different
things: `?????????` is Bad status — the value exists and cannot be
trusted — while `@@@@@@@@` is not communicating at all. Collapsing
them into one blank would throw away the only clue an operator has
about which half of the system to go and look at.

Qt-free. `studio/items.py` paints these; the rules live here so they
can be tested without a screen.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..binding.result import UNRESOLVED, BindingResult
from azeo_control_trainer.core.strategy.model.terminal import Quality

# ------------------------------------------------------------ datalinks
STRING, NUMERIC, SCALED_NUMERIC = "string", "numeric", "scaled_numeric"
MODE, NAMED_SET, SCALING = "mode", "named_set", "scaling"

DATALINK_TYPES = (STRING, NUMERIC, SCALED_NUMERIC, MODE, NAMED_SET,
                  SCALING)

DATALINK_TITLES = {STRING: "String", NUMERIC: "Numeric",
                   SCALED_NUMERIC: "Scaled numeric", MODE: "Mode",
                   NAMED_SET: "Named set", SCALING: "Scaling"}

#: Types that return an OBJECT, not a scalar. Their path must end at
#: the parameter — a field suffix makes them unresolvable.
OBJECT_TYPES = (MODE, NAMED_SET, SCALING)

#: Bad status: the point exists, its value cannot be trusted.
BAD_TEXT = "?????????"
#: Not communicating: nothing is answering for this point at all.
STALE_TEXT = "@@@@@@@@"

#: Stable semantic process ports used by newly placed equipment symbols.
#: Persisted positions are normalized so a pipe remains attached when the
#: engineer resizes the equipment. A tuple of tuples keeps this Qt-free model
#: constant immutable; ``default_symbol_ports`` returns document dictionaries.
STANDARD_SYMBOL_PORTS = (
    ("inlet", 0.0, 0.5),
    ("outlet", 1.0, 0.5),
    ("top", 0.5, 0.0),
    ("bottom", 0.5, 1.0),
)

# -------------------------------------------------- process continuations
# Off-page streams are engineering objects rather than decorated text.  Their
# single process port says which side belongs to the on-page pipe; the label
# says where the process comes from or continues to.  Keeping this contract
# Qt-free means a saved display, verifier, and operator renderer agree without
# depending on the Studio palette that created it.
STREAM_CONNECTOR = "stream_connector"
STREAM_INCOMING = "incoming"
STREAM_OUTGOING = "outgoing"
STREAM_DIRECTIONS = (STREAM_INCOMING, STREAM_OUTGOING)


def stream_connector_properties(direction: str) -> dict:
    """Return the persistent properties for one off-page process stream.

    Both variants point in the conventional left-to-right process direction.
    An incoming stream attaches at its arrow tip and is the source of the
    on-page pipe; an outgoing stream attaches at its tail and is the
    destination.  Rotation and mirroring cover the other page edges without
    multiplying the document vocabulary.
    """
    direction = str(direction or "").strip().lower()
    if direction not in STREAM_DIRECTIONS:
        raise ValueError(f"unknown stream connector direction {direction!r}")
    incoming = direction == STREAM_INCOMING
    return {
        "stream_direction": direction,
        "ports": [{
            "name": "process",
            "x": 1.0 if incoming else 0.0,
            "y": 0.5,
            "normal": "e" if incoming else "w",
        }],
        "font_family": "Segoe UI",
        "font_size": 8.0,
        "font_bold": True,
        "text_halign": "center",
        "text_valign": "middle",
    }


def default_symbol_ports(symbol: str = "") -> list[dict]:
    """A fresh serializable semantic-port contract for equipment.

    Library SVGs declare the nozzles that touch their real artwork.  Persist
    those values on placement so an exported display remains self-describing;
    unknown and imported legacy symbols retain the cardinal fallback.
    """
    declared = {}
    if symbol:
        from .symbols import connection_ports

        declared = connection_ports(symbol)
    cardinal = {"inlet": "w", "outlet": "e",
                "top": "n", "bottom": "s"}
    return [
        {"name": name,
         "x": declared.get(cardinal[name], (x, y))[0],
         "y": declared.get(cardinal[name], (x, y))[1],
         "normal": cardinal[name],
         # A catalog-managed port follows corrected SVG nozzle metadata in a
         # later product release. Opening the structured port editor removes
         # this field, which intentionally turns the row into a user override.
         "source": "catalog"}
        for name, x, y in STANDARD_SYMBOL_PORTS
    ]

#: Field suffixes a path may carry.
_FIELDS = (".CV", ".ST", ".TARGET", ".ACTUAL", ".STR", ".F_CV")


def path_field(path: str) -> str:
    """The `.FIELD` suffix on a parameter path, or ''."""
    upper = str(path or "").upper()
    for field in _FIELDS:
        if upper.endswith(field):
            return field
    return ""


def validate_datalink(dtype: str, path: str) -> str:
    """'' when the pairing is legal, else why it is not.

    Checked at configuration time on purpose. Every one of these
    produces a runtime symbol rather than an error, so an unchecked
    mis-typing looks like a plant problem.
    """
    if dtype not in DATALINK_TYPES:
        return f"unknown datalink type {dtype!r}"
    if not path:
        return "no data source"
    field = path_field(path)
    if dtype in OBJECT_TYPES and field:
        return (f"a {DATALINK_TITLES[dtype]} datalink returns an object; "
                f"its path must end at the parameter, not at {field}")
    if dtype == NUMERIC and field == ".STR":
        return "a numeric datalink on a .STR path returns NaN — use a " \
               "string datalink"
    if dtype == SCALED_NUMERIC and field == ".STR":
        return "a scaled numeric datalink on a .STR path is " \
               "unresolvable — use a string datalink"
    return ""


@dataclass(frozen=True)
class DatalinkText:
    """What a datalink actually shows, and why."""

    text: str
    quality: Quality = Quality.GOOD
    writable: bool = False

    @property
    def is_error(self) -> bool:
        return self.text in (BAD_TEXT, STALE_TEXT)


def datalink_text(dtype: str, result: BindingResult, *,
                  decimals: int = 1, units: bool = False,
                  writable: bool = False) -> DatalinkText:
    """Render one datalink's value the way Azeo renders it."""
    if result is None or result is UNRESOLVED:
        return DatalinkText(STALE_TEXT, Quality.BAD, False)
    if result.quality is Quality.BAD:
        return DatalinkText(BAD_TEXT, Quality.BAD, False)
    value = result.value
    if value is None:
        return DatalinkText(STALE_TEXT, Quality.BAD, False)
    if dtype == MODE:
        text = str(result.mode_actual or value)
    elif dtype in (NAMED_SET, SCALING):
        text = str(value)
    elif dtype == STRING:
        text = str(value)
    else:
        try:
            text = f"{float(value):.{max(0, int(decimals))}f}"
        except (TypeError, ValueError):
            text = str(value)
    if units and result.units:
        text = f"{text} {result.units}"
    return DatalinkText(text, result.quality, writable)


# --------------------------------------------------------- user entries
BUTTON, CHECK_BOX, COMBO_BOX = "button", "check_box", "combo_box"
RADIO_BUTTON, SLEW, SLIDER = "radio_button", "slew", "slider"
TEXT_ENTRY = "text_entry"

USER_ENTRY_TYPES = (BUTTON, CHECK_BOX, COMBO_BOX, RADIO_BUTTON, SLEW,
                    SLIDER, TEXT_ENTRY)

USER_ENTRY_TITLES = {BUTTON: "Button", CHECK_BOX: "Check box",
                     COMBO_BOX: "Combo box",
                     RADIO_BUTTON: "Radio button", SLEW: "Slew",
                     SLIDER: "Slider", TEXT_ENTRY: "Text entry"}

#: A slew's change is proportional to how long it is held — the
#: manual's own description. These are the ramp's shape.
SLEW_INITIAL_STEP = 0.1
SLEW_ACCELERATION = 1.6
SLEW_MAX_STEP = 10.0


def slew_step(held_ms: float, span: float = 100.0) -> float:
    """How far a slew moves after being held for `held_ms`.

    Proportional to hold time, capped. A slew that moved a fixed step
    per tick is a button pressed repeatedly; a slew that accelerated
    without a cap overshoots the moment an operator looks away.
    """
    seconds = max(0.0, held_ms) / 1000.0
    step = SLEW_INITIAL_STEP * (SLEW_ACCELERATION ** seconds)
    return min(step, SLEW_MAX_STEP) * (span / 100.0)


@dataclass
class UserEntry:
    """One writable control's configuration."""

    kind: str = BUTTON
    #: What it writes to. Empty means it only runs actions.
    path: str = ""
    label: str = ""
    #: Button value. Other controls derive the value from their state.
    value: object = True
    #: Combo and radio: [(value, label), …].
    options: tuple = ()
    #: Slew and slider.
    lo: float = 0.0
    hi: float = 100.0
    #: A control an operator may not use is disabled WITH a reason —
    #: an unexplained greyed-out button is why operators phone the
    #: engineer (the same rule the faceplates already follow).
    disabled_reason: str = ""

    @property
    def writable(self) -> bool:
        return bool(self.path) and not self.disabled_reason

    def clamp(self, value: float) -> float:
        try:
            return max(self.lo, min(self.hi, float(value)))
        except (TypeError, ValueError):
            return self.lo

    def validate(self) -> str:
        """'' when configured usably, else what is wrong."""
        if self.kind not in USER_ENTRY_TYPES:
            return f"unknown user entry type {self.kind!r}"
        if self.kind in (COMBO_BOX, RADIO_BUTTON) and not self.options:
            return f"a {USER_ENTRY_TITLES[self.kind]} needs options"
        if self.kind in (SLEW, SLIDER) and self.hi <= self.lo:
            return (f"a {USER_ENTRY_TITLES[self.kind]} needs hi above "
                    f"lo (got {self.lo}..{self.hi})")
        if self.kind != BUTTON and not self.path:
            return f"a {USER_ENTRY_TITLES[self.kind]} needs a data source"
        return ""

    def to_dict(self) -> dict:
        out = {"kind": self.kind}
        for name in ("path", "label", "disabled_reason"):
            if getattr(self, name):
                out[name] = getattr(self, name)
        if self.options:
            out["options"] = [list(o) for o in self.options]
        if self.kind == BUTTON and self.value is not True:
            out["value"] = self.value
        if self.kind in (SLEW, SLIDER):
            out["lo"], out["hi"] = self.lo, self.hi
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "UserEntry":
        def number(name: str, fallback: float) -> float:
            try:
                return float(data.get(name, fallback))
            except (TypeError, ValueError):
                # A PVM class layout may hold ``Pvm.EU0`` until instance
                # configuration resolves it.  Authoring must remain
                # paintable; validation still reports other invalid values.
                return fallback

        return cls(kind=str(data.get("kind", BUTTON)),
                   path=str(data.get("path", "")),
                   label=str(data.get("label", "")),
                   value=data.get("value", True),
                   options=tuple(tuple(o)
                                 for o in data.get("options", ())),
                   lo=number("lo", 0.0),
                   hi=number("hi", 100.0),
                   disabled_reason=str(data.get("disabled_reason", "")))


# ------------------------------------------------------------- actions
CLICK, DOUBLE_CLICK, RIGHT_CLICK, DRAG = ("click", "double_click",
                                          "right_click", "drag")
MOUSE_EVENTS = (CLICK, DOUBLE_CLICK, RIGHT_CLICK, DRAG)

OPEN_FACEPLATE = "open_faceplate"
OPEN_USER_FACEPLATE = "open_user_faceplate"
OPEN_USER_DETAIL = "open_user_detail"
OPEN_DETAIL = "open_detail"
OPEN_DISPLAY = "open_display"
WRITE_VALUE = "write_value"
SHOW_TOOLTIP = "show_tooltip"
ADD_TO_WATCH = "add_to_watch"
SCRIPT = "script"
PROCEDURE_COMMAND = "procedure_command"

ACTION_KINDS = (OPEN_FACEPLATE, OPEN_USER_FACEPLATE, OPEN_DETAIL,
                OPEN_DISPLAY, WRITE_VALUE, SHOW_TOOLTIP, ADD_TO_WATCH,
                SCRIPT, PROCEDURE_COMMAND, OPEN_USER_DETAIL)

# ---------------------------------------------------------- data elements
CHART = "chart"
ALARM_LIST = "alarm_list"
MULTI_POINT = "multi_point"
RADAR_PLOT = "radar_plot"
TAB = "tab"
DATE_TIME = "date_time"
TABLE = "table"

#: A hosted shipped faceplate section — a condition table or a
#: sequencer body. Deliberately NOT in `DATA_ELEMENT_KINDS`: those are
#: validated and configured by `validate_data_element`, while this one
#: carries a `section` name and a `paths` map instead of pens and rows.
FACEPLATE_SECTION = "faceplate_section"

DATA_ELEMENT_KINDS = (CHART, ALARM_LIST, MULTI_POINT, RADAR_PLOT, TAB,
                      DATE_TIME, TABLE)
MAX_CHART_PENS = 10
MIN_MULTI_PARAMETERS, MAX_MULTI_PARAMETERS = 3, 12
MIN_TABS, MAX_TABS = 1, 32


def element_paths(data: dict) -> tuple[str, ...]:
    """All live paths a compound data element monitors."""
    if data.get("kind") == CHART:
        rows = data.get("pens", ())
    elif data.get("kind") in (MULTI_POINT, RADAR_PLOT):
        rows = data.get("parameters", ())
    elif data.get("kind") == TABLE:
        rows = []
        for row in data.get("rows", ()):
            if not isinstance(row, dict):
                continue
            rows.extend(value for value in row.values()
                        if isinstance(value, dict) and value.get("path"))
    else:
        rows = ()
    if data.get("rows_path"):
        rows = [*rows, {"path": data["rows_path"]}]
    if data.get("series_path"):
        rows = [*rows, {"path": data["series_path"]}]
    return tuple(str(row.get("path", "")).strip() for row in rows
                 if isinstance(row, dict) and str(row.get("path", "")).strip())


def validate_data_element(data: dict) -> str:
    """Validate caps the manual puts on placeable data elements."""
    kind = data.get("kind")
    if kind == CHART:
        if data.get("series_path"):
            if data.get("pens") or not str(data["series_path"]).startswith("@procedure/") or not str(data["series_path"]).endswith("/TREND"):
                return "shared procedure history requires a TREND collection and no local pens"
            return ""
        rows = data.get("pens", ())
        count = len(rows)
        if not 1 <= count <= MAX_CHART_PENS:
            return f"a chart needs 1..{MAX_CHART_PENS} pens"
        if any(not str(row.get("path", "")).strip() for row in rows):
            return "every chart pen needs a parameter"
    elif kind in (MULTI_POINT, RADAR_PLOT):
        rows = data.get("parameters", ())
        count = len(rows)
        if not MIN_MULTI_PARAMETERS <= count <= MAX_MULTI_PARAMETERS:
            return (f"a {kind.replace('_', ' ')} needs "
                    f"{MIN_MULTI_PARAMETERS}..{MAX_MULTI_PARAMETERS} parameters")
        if any(not str(row.get("path", "")).strip() for row in rows):
            return f"every {kind.replace('_', ' ')} axis needs a parameter"
    elif kind == TAB:
        count = len(data.get("tabs", ()))
        if not MIN_TABS <= count <= MAX_TABS:
            return f"a tab element needs {MIN_TABS}..{MAX_TABS} tab items"
    elif kind == DATE_TIME:
        if data.get("timezone", "local") not in ("local", "utc"):
            return "date-time timezone must be local or utc"
    elif kind == TABLE:
        columns = data.get("columns", ())
        rows = data.get("rows", ())
        if data.get("presentation", "table") not in {"table", "workflow"}:
            return "table presentation must be table or workflow"
        if data.get("presentation") == "workflow" and not str(data.get("rows_path", "")).endswith("/STEPS"):
            return "workflow presentation requires a procedure STEPS collection"
        if data.get("row_action", "") not in {"", "tune"}:
            return "unsupported table row action"
        if data.get("row_action") == "tune" and (not str(data.get("rows_path", "")).endswith("/PARAMETERS") or not data.get("command_context")):
            return "tuning rows require PARAMETERS and a procedure command context"
        if data.get("row_help") and not str(data.get("rows_path", "")).endswith("/STEPS"):
            return "procedure row Block Help requires a procedure STEPS collection"
        if not 1 <= len(columns) <= 12:
            return "a table needs 1..12 columns"
        if len(rows) > 100:
            return "a table supports at most 100 rows"
        keys = [str(column.get("key", "")).strip()
                for column in columns if isinstance(column, dict)]
        if len(keys) != len(columns) or any(not key for key in keys):
            return "every table column needs a key"
        if len(set(keys)) != len(keys):
            return "table column keys must be unique"
        if any(not isinstance(row, dict) for row in rows):
            return "every table row must be an object"
    elif kind in ("symbol", STREAM_CONNECTOR):
        ports = data.get("ports", ())
        if ports and not isinstance(ports, (list, tuple)):
            return "symbol ports must be a list"
        names = []
        for port in ports or ():
            if not isinstance(port, dict):
                return "every symbol port must be an object"
            name = str(port.get("name", "")).strip()
            if not name:
                return "every symbol port needs a name"
            names.append(name)
            try:
                x, y = float(port.get("x")), float(port.get("y"))
            except (TypeError, ValueError):
                return f"symbol port {name} needs numeric x/y"
            if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
                return f"symbol port {name} must lie within 0..1"
        if len(names) != len(set(names)):
            return "symbol port names must be unique"
        if kind == STREAM_CONNECTOR and names != ["process"]:
            return "a stream connector needs exactly one process port"

    return ""


@dataclass
class Action:
    """One thing a mouse event does.

    `active` is the manual's own property, and it hides the interaction
    region as well as disabling it — a hotspot that highlights and then
    does nothing is worse than no hotspot.
    """

    event: str = CLICK
    kind: str = OPEN_FACEPLATE
    target: str = ""
    active: bool = True
    value: object = None
    source: str = ""

    def to_dict(self) -> dict:
        out = {"event": self.event, "kind": self.kind}
        if self.target:
            out["target"] = self.target
        if self.value is not None:
            out["value"] = self.value
        if self.source:
            out["source"] = self.source
        if not self.active:
            out["active"] = False
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Action":
        return cls(event=str(data.get("event", CLICK)),
                   kind=str(data.get("kind", OPEN_FACEPLATE)),
                   target=str(data.get("target", "")),
                   active=bool(data.get("active", True)),
                   value=data.get("value"),
                   source=str(data.get("source", "")))


def actions_of(data: dict) -> list:
    """Every action configured on an item."""
    return [Action.from_dict(a) for a in data.get("actions", ())
            if isinstance(a, dict)]


def has_interaction_region(data: dict) -> bool:
    """Whether this item is a hotspot online.

    The manual's rule, including the part people forget: an invisible
    element has no interaction region, so hiding a control disables it
    rather than leaving an invisible live hotspot on the screen.
    """
    if not data.get("visible", True):
        return False
    if not data.get("enabled", True):
        return False
    return any(a.active for a in actions_of(data))
