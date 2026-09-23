"""The PVM class system's base — §10 plus the §6 display-state contract.

Vocabulary (PVM_CLASS_CATALOG.md): a **PVM class** is written once by an
Author and registered against a `block_type` and a role; a **PVM** is a
placement of that class on a display carrying one parameter — the block
path — plus geometry and optional fill/line appearance. Units, ranges and
mode lists still resolve at runtime through the binding engine.

Two invariants live here and nowhere else:

- **I6 — state rendering is single-sourced.** `resolve_state()` is the
  §6 display-state contract, implemented once. A subclass decides
  layout; it never decides what Bad quality looks like.
- **I7 — no per-instance process-data overrides.** A `Pvm` is a frozen
  record. Fill and line are explicit visual properties; units, ranges,
  modes and binding behavior remain class/type-owned.

The single-parameter rule (§10.2) is enforced at class-definition time:
`PARAMS` longer than `("path",)` is refused unless the class declares an
`EXCEPTION` string, and every exception is logged at registration — the
rule stays enforceable while staying honest about the cascade pair.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from azeo_control_trainer.core.strategy.model.terminal import LimitStatus, Quality
from ..binding.result import BindingResult

log = logging.getLogger("azeo.hmi.pvms")

ROLES = ("dynamo_compact", "dynamo_inline", "faceplate", "detail")

#: Alarm priority -> theme token, Azeo numbering.
_PRIORITY_TOKENS = {15: "signal.critical", 11: "signal.warning",
                    7: "signal.advisory"}

#: What a value reads as when quality is Bad. Never a stale number.
BAD_VALUE_TEXT = "– – –"


# ────────────────────────────────────────────────── the state contract
@dataclass(frozen=True)
class DisplayState:
    """One §6 resolution: first matching priority wins, forced additive."""

    name: str                     # bad | alarm | warning | mode_mismatch
    #                             # | uncertain | normal
    marker: str                   # hatched_circle | square | triangle
    #                             # | hollow | ""
    token: str                    # theme token for the marker; "" = none
    value_text: str               # what the number field shows
    forced: bool                  # ALWAYS carried, in addition to state
    show_last_good: bool          # display state 1: history, labelled
    primary_action: str           # "ACKNOWLEDGE" | "DETAIL"  (§10.4)
    mode_text: str = ""           # "TARGET · ACTUAL" when mismatched


def _format_value(value, units: str = "") -> str:
    if isinstance(value, bool):
        text = "ON" if value else "OFF"
    elif isinstance(value, float):
        text = f"{value:.4g}"
    elif value is None:
        text = BAD_VALUE_TEXT
    else:
        text = str(value)
    return f"{text} {units}".strip() if units else text


def resolve_state(result: BindingResult) -> DisplayState:
    """The §6 table, in priority order — implemented once (I6)."""
    forced = bool(result.forced)
    action = "ACKNOWLEDGE" if (result.alarm_active
                               and not result.alarm_acked) else "DETAIL"

    if result.quality == Quality.BAD:
        return DisplayState("bad", "hatched_circle", "state.bad_quality",
                            BAD_VALUE_TEXT, forced, True, action)
    value_text = _format_value(result.value, result.units)
    if result.alarm_active and not result.alarm_acked:
        token = _PRIORITY_TOKENS.get(result.alarm_priority,
                                     "signal.warning")
        return DisplayState("alarm", "square", token, value_text,
                            forced, False, "ACKNOWLEDGE")
    if result.limit != LimitStatus.NOT_LIMITED \
            or (result.alarm_active and result.alarm_priority <= 11):
        return DisplayState("warning", "triangle", "signal.warning",
                            value_text, forced, False, action)
    if result.mode_mismatch:
        return DisplayState(
            "mode_mismatch", "", "signal.hold", value_text, forced,
            False, action,
            mode_text=f"{result.mode_target} · {result.mode_actual}")
    if result.quality == Quality.UNCERTAIN:
        return DisplayState("uncertain", "hollow", "text.dimmed",
                            value_text, forced, False, action)
    # Normal: no colour anywhere. A display where nothing needs
    # attention is entirely greyscale.
    return DisplayState("normal", "", "", value_text, forced, False,
                        action)


# ───────────────────────────────────────────────────────────── binds
@dataclass(frozen=True)
class Bind:
    """One declared binding: a target key on the PVM, a path template.

    `prop` selects a metadata field of the same path's result
    (EngineeringUnits, EURange, StatusCode, Limit, Forced) rather than a
    second read. `expr` makes it a derived value over other binds' keys
    (dots become underscores for the expression language). `writable`
    marks operator-write targets; `persists` marks config writes — the
    two write semantics of §8.3, never one code path.
    """

    key: str
    path: str = ""
    prop: str = ""
    writable: bool = False
    persists: bool = False
    expr: str = ""


# ───────────────────────────────────────────────────────── registry
class PvmRegistry:
    def __init__(self):
        self._classes: dict[tuple, type] = {}

    def register(self, pvm_cls: type) -> type:
        key = (pvm_cls.block_type, pvm_cls.role,
               getattr(pvm_cls, "variant", ""))
        if key in self._classes:
            raise ValueError(
                f"a PVM class for {key} is already registered "
                f"({self._classes[key].__name__}) — a second look for the "
                "same type+role is a variant, name it")
        self._classes[key] = pvm_cls
        exception = getattr(pvm_cls, "EXCEPTION", "")
        if exception:
            log.info("PVM exception registered: %s takes %s — %s",
                     pvm_cls.__name__, pvm_cls.PARAMS, exception)
        return pvm_cls

    def get(self, block_type: str, role: str,
            variant: str = "") -> type | None:
        return self._classes.get((block_type, role, variant))

    def for_block_type(self, block_type: str) -> dict:
        return {role: cls for (bt, role, var), cls
                in self._classes.items()
                if bt == block_type and not var}

    def all_classes(self) -> dict:
        return dict(self._classes)

    def exceptions(self) -> list[type]:
        return [c for c in self._classes.values()
                if getattr(c, "EXCEPTION", "")]

    def registration_report(self) -> list[str]:
        """The startup log sheet 7.6 specifies — classes, the counted
        exceptions, and the data-override line that must read zero (I7;
        appearance may vary without changing process semantics)."""
        classes = list(self._classes.values())
        block_types = {c.block_type for c in classes}
        ok = [c for c in classes if not getattr(c, "EXCEPTION", "")]
        lines = [f"registered {len(classes)} PVM classes across "
                 f"{len(block_types)} block types",
                 f"ok         {len(ok)} classes · PARAMS = (\"path\",)"]
        for cls in self.exceptions():
            lines.append(f"exception  {cls.__name__} · PARAMS = "
                         f"{cls.PARAMS}")
        lines.append("data overrides  0 (invariant I7 holds)")
        return lines

    def log_registration(self) -> None:
        for line in self.registration_report():
            log.info("PVM registry: %s", line)


registry = PvmRegistry()


def register_pvm(pvm_cls: type) -> type:
    return registry.register(pvm_cls)


# ───────────────────────────────────────────────────────── the class
class PvmClass:
    """Author-side base. Subclasses declare, they do not configure."""

    block_type: str = ""
    role: str = ""
    variant: str = ""
    display_name: str = ""
    #: Exactly one parameter — the block path (§10.2).
    PARAMS: tuple = ("path",)
    #: Set ONLY for a documented multi-parameter exception (catalog
    #: Part 3). Growing this habit past two or three classes means the
    #: rule is wrong and should be revisited deliberately.
    EXCEPTION: str = ""
    #: Preferred placement size (w, h); None = the role default.
    DEFAULT_SIZE: tuple | None = None
    #: Optional class-owned normalized connection points. Most authored
    #: classes persist these in PvmConfiguration; code classes may declare
    #: the same shape directly.
    CONNECTION_POINTS: tuple = ()
    #: Conditions allowed to raise the HP status box. Empty means all;
    #: a class may suppress conditions that are irrelevant to its role.
    STATUS_BOX_CONDITIONS: tuple = ()
    bindings: tuple = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        abstract = not cls.block_type and not cls.role
        if abstract:
            return
        if cls.role not in ROLES:
            raise TypeError(
                f"{cls.__name__}: role must be one of {ROLES}, "
                f"not {cls.role!r}")
        if len(cls.PARAMS) != 1 and not cls.EXCEPTION:
            raise TypeError(
                f"{cls.__name__} declares parameters {cls.PARAMS} — a PVM "
                "class takes exactly one parameter, the block path "
                "(§10.2). Extra parameters quietly reintroduce "
                "per-instance configuration. A genuine composite view "
                "must set EXCEPTION with its justification.")

    # -------------------------------------------------------- placement
    def place(self, pvm_id: str, standard: str = "", x: float = 0,
              y: float = 0, w: float = 0, h: float = 0,
              fill: str = "", line: str = "",
              **params) -> "Pvm":
        """Create a PVM of this class. Params must match PARAMS exactly —
        an extra keyword here is a process-data override trying to get
        in (I7), and it is refused by name. Fill and line are the two
        explicitly supported placement-level appearance properties."""
        expected = set(self.PARAMS)
        given = set(params)
        if given != expected:
            extra = sorted(given - expected)
            missing = sorted(expected - given)
            detail = []
            if extra:
                detail.append(f"unexpected {extra} — a PVM carries no "
                              "per-instance process configuration (I7)")
            if missing:
                detail.append(f"missing {missing}")
            raise TypeError(f"{type(self).__name__}.place(): "
                            + "; ".join(detail))
        return Pvm(id=pvm_id, pvm_class=type(self).__name__,
                   block_type=self.block_type, role=self.role,
                   params=dict(params), variant=self.variant,
                   standard=standard, fill=fill, line=line,
                   x=x, y=y, w=w, h=h)

    # ---------------------------------------------------------- binding
    def bind_all(self, engine, params: dict, config=None,
                 choices: dict | None = None) -> dict:
        """Resolve the declared bindings against the engine. Returns
        {key: Binding-or-(base_key, prop)} — prop binds read a field of
        the base path's result instead of a second subscription.

        With a `config` (the class's PvmConfiguration) and an
        instance's `choices`, templates may reference configuration
        properties and a spec gated by Presence / Present Online is
        never bound — never subscribed."""
        from ..binding.engine import format_template
        gated: set = set()
        if config is not None:
            params = config.binding_params(choices or {},
                                           base=params)
            _active, gated_specs = config.plan_bindings(
                self.bindings, choices or {})
            gated = {spec.key for spec in gated_specs}
        resolved: dict = {}
        expression_binds = []
        for spec in self.bindings:
            if spec.key in gated:
                continue
            if spec.expr:
                expression_binds.append(spec)
                continue
            if spec.prop:
                resolved[spec.key] = ("prop",
                                      format_template(spec.path,
                                                      params),
                                      spec.prop)
                continue
            resolved[spec.key] = engine.bind(
                spec.path, params, name=self._safe(spec.key, params))
        for spec in expression_binds:
            refs = {}
            expression_text = spec.expr
            for other in self.bindings:
                if other.expr or other.prop:
                    continue
                safe = other.key.replace(".", "_")
                if other.key in spec.expr or safe in spec.expr:
                    expression_text = expression_text.replace(
                        other.key, safe)
                    refs[safe] = self._safe(other.key, params)
            resolved[spec.key] = engine.bind_expression(
                expression_text, refs,
                name=self._safe(spec.key, params))
        return resolved

    def _safe(self, key: str, params: dict) -> str:
        tag = "/".join(str(v) for v in params.values())
        return f"{type(self).__name__}:{tag}:{key}".replace(".", "_")

    # ------------------------------------------------------------ state
    #: Subclasses receive resolved DisplayStates, never raw quality bits.
    resolve_state = staticmethod(resolve_state)

    def build(self, ctx) -> None:                   # pragma: no cover
        """Layout only — implemented by the rendering layer's subclass."""
        raise NotImplementedError


# ─────────────────────────────────────────────────────────── the PVM
def primary_path(params: dict) -> str:
    """An explicit primary tag wins over serialization order in a composite."""
    return str(params.get("path", "") or "") if "path" in params else str(next(iter(params.values()), ""))


@dataclass(frozen=True)
class Pvm:
    """A placement: class, path parameter(s), appearance and rectangle.

    Frozen and field-complete: fill and line may vary visually while ranges,
    units, modes and behavior remain owned by the PVM class and live type.
    """

    id: str
    pvm_class: str
    block_type: str
    role: str
    params: dict
    variant: str = ""
    #: Friendly name (Azeo Operator Station's per-PVM label) — identity, not
    #: styling, so it lives beside the path rather than breaking I7.
    label: str = ""
    standard: str = ""
    #: Optional literal appearance. Blank keeps the active theme roles;
    #: operational alarm/status colours are never overridden by these fields.
    fill: str = ""
    line: str = ""
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    #: Quarter-turn rotation (0/90/180/270) — geometry, like x/y/w/h,
    #: so a placement may own it without breaking I7. Bars swap to
    #: their horizontal/vertical counterpart layout (text upright);
    #: equipment silhouettes rotate in place.
    rot: float = 0.0
    #: Authoring stack order, persisted rather than left only on a Qt item.
    z: float = 0.0
    #: Flat authoring group identity. It changes selection/transform scope,
    #: never the PVM's class-owned rendering or bindings.
    group: str = ""
    layer: str = "pvms"
    #: Authoring state. These are not style overrides: visibility changes
    #: whether the placement participates in the published display, while a
    #: lock is Studio-only protection against accidental movement.
    visible: bool = True
    locked: bool = False
    #: Selection choices from the class's configuration document —
    #: DECLARED, enumerated variation (the paper's Selection +
    #: Presence model), not an override: each entry names an option
    #: the Author put in the class, and values still resolve through
    #: the class's own configuration at bind time. Absent when
    #: default, so existing displays stay byte-identical. I7 stands:
    #: there is still no field a free-form value could live in.
    choices: dict = field(default_factory=dict)
    class_revision: str = ""
    #: Optional artwork override: the equipment silhouette this
    #: placement draws instead of its class's built-in one, named in the
    #: shared symbol catalog (vendored or project-imported). Appearance
    #: only, exactly like `fill`/`line`: bindings, mode, alarm box,
    #: status icons and faceplate are untouched, so I7 stands — no
    #: process meaning lives here. Appended last so every positional
    #: construction keeps its order, and absent when blank so existing
    #: displays stay byte-identical.
    symbol: str = ""

    def to_dict(self) -> dict:
        d = {"id": self.id, "class": f"{self.block_type}/{self.role}",
             "params": dict(self.params),
             "x": self.x, "y": self.y, "w": self.w, "h": self.h}
        if self.variant:
            d["variant"] = self.variant
        if self.label:
            d["label"] = self.label
        if self.standard:
            d["standard"] = self.standard
        if self.fill:
            d["fill"] = self.fill
        if self.line:
            d["line"] = self.line
        if self.rot:
            d["rot"] = self.rot
        if self.z:
            d["z"] = self.z
        if self.group:
            d["group"] = self.group
        if self.layer != "pvms":
            d["layer"] = self.layer
        if not self.visible:
            d["visible"] = False
        if self.locked:
            d["locked"] = True
        if self.choices:
            d["choices"] = dict(self.choices)
        if self.class_revision:
            d["class_revision"] = self.class_revision
        if self.symbol:
            d["symbol"] = self.symbol
        return d
