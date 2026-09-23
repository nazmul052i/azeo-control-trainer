"""The uniform property model — Azeo Operator Station's diamond context menu.

In Graphics Designer every property carrying a diamond can be one of a
fixed set of things: a static value, a simple animation, a blink
animation, a library animation (a function reference), a standard
reference, a variable reference, a PVM configuration property
reference, or — inside a linked PVM — an override. That uniformity is
the whole authoring model: an engineer learns *one* gesture and it
works on fill, line, rotation, visibility, text, width, angle.

Before this, animation here was a special case bolted onto four
properties (`fill`, `line`, `fill_pct`, `rot`) and references were two
magic string prefixes. This module makes the general case general.

**Storage.** A descriptor lives in `data["props"][name]`; the plain
value stays in `data[name]` and is what a resolver falls back to. That
keeps every existing display loading unchanged — a property with no
descriptor is a static value, which is exactly what it was — and
satisfies the never-rename rule: the legacy `anim` map and the legacy
`Pvm.` / `Standard.` string prefixes are read as descriptors here
rather than being migrated on disk.

**Quality is carried, never dropped.** Resolution returns a value *and*
a quality, and a compound resolution takes the worst of its inputs —
the manual's own rule ("the data quality for the final computed value
reflects the worst data quality"). A property that cannot be resolved
is Bad, and Bad is not zero: the caller removes its override rather
than painting an invented value.
"""
from __future__ import annotations

from ..compatibility import (
    LEGACY_SCOPE_PREFIX, PVM_SCOPE_PREFIX, PVM_SCOPE_PREFIXES, normalize_pvm_descriptor,
)

import re
from dataclasses import dataclass, replace

from ..binding.result import UNRESOLVED, BindingResult
from azeo_control_trainer.core.strategy.model.terminal import Quality

# ------------------------------------------------------------- kinds
#: A literal. The absence of a descriptor means this.
STATIC = "static"
#: `Standard.<name>` — a library standard, themed.
STANDARD = "standard"
#: `Pvm.<Property>[.<Column>]` — a PVM configuration property.
PVM_PROPERTY = "pvm"
#: `Dsp.<name>` / `Pvm.<name>` / `Grp.<name>` — a graphics variable.
VARIABLE = "variable"
#: A live parameter, optionally through a conversion function.
ANIMATION = "animation"
#: Two colours alternating while a condition holds.
BLINK = "blink"
#: Arithmetic over live parameters.
EXPRESSION = "expression"

KINDS = (STATIC, STANDARD, PVM_PROPERTY, VARIABLE, ANIMATION, BLINK,
         EXPRESSION)

#: What the diamond menu offers, in the manual's own order.
MENU_ORDER = (ANIMATION, BLINK, EXPRESSION, STANDARD, VARIABLE,
              PVM_PROPERTY)

MENU_TITLES = {
    STATIC: "Set to Static Value",
    ANIMATION: "Animation…",
    BLINK: "Blink Animation…",
    EXPRESSION: "Expression…",
    STANDARD: "Browse for Standard…",
    VARIABLE: "Variables…",
    PVM_PROPERTY: "PVM Configuration Properties…",
}

#: Context prefixes, as the manual defines them. `Lyt.` and `Grp.` are
#: accepted so a display authored against the full grammar still loads;
#: `resolve` answers what it can and reports Bad for the rest rather
#: than pretending a layout variable resolved.
PREFIXES = {"Dsp.": VARIABLE, "Lyt.": VARIABLE, "Grp.": VARIABLE,
            PVM_SCOPE_PREFIX: PVM_PROPERTY, LEGACY_SCOPE_PREFIX: PVM_PROPERTY,
            "Standard.": STANDARD, "GL.": STANDARD}

#: Standard blink 500 ms, alternate 125 ms — the layout's Styling tab
#: values, defaulted here so a blink works with nothing configured.
BLINK_STANDARD_MS = 500
BLINK_ALTERNATE_MS = 125


@dataclass(frozen=True)
class Resolved:
    """One property, answered."""

    value: object = None
    quality: Quality = Quality.GOOD
    #: Which kind produced it, for the property pane and for tests.
    kind: str = STATIC

    @property
    def good(self) -> bool:
        return self.quality is Quality.GOOD

    @property
    def usable(self) -> bool:
        """A value the caller may actually paint."""
        return self.value is not None and self.quality is not Quality.BAD


#: What an unresolvable property answers. Bad, valueless — never zero.
UNSET = Resolved(None, Quality.BAD, STATIC)


def descriptor_for(data: dict, name: str) -> dict | None:
    """The descriptor governing `name`, from any of the three storages.

    New form first (`props`), then the legacy `anim` map, then a legacy
    prefixed string sitting in the value itself. Reading legacy forms
    here rather than migrating them on disk is what keeps shipped
    displays byte-identical.
    """
    props = data.get("props")
    if isinstance(props, dict) and isinstance(props.get(name), dict):
        return normalize_pvm_descriptor(dict(props[name]))
    anim = data.get("anim")
    if isinstance(anim, dict) and isinstance(anim.get(name), dict):
        legacy = dict(anim[name])
        legacy.setdefault("kind", ANIMATION)
        return legacy
    value = data.get(name)
    if isinstance(value, str):
        for prefix, kind in PREFIXES.items():
            if value.startswith(prefix):
                return {"kind": kind, "ref": value}
    return None


def set_descriptor(data: dict, name: str, descriptor: dict | None) -> None:
    """Attach or clear one property's descriptor.

    Clearing removes the key entirely rather than storing a null, so a
    display that has never used the feature stays byte-identical to one
    that used it and stopped.
    """
    props = data.get("props")
    if descriptor is None:
        if isinstance(props, dict):
            props.pop(name, None)
            if not props:
                data.pop("props", None)
        anim = data.get("anim")
        if isinstance(anim, dict):
            anim.pop(name, None)
            if not anim:
                data.pop("anim", None)
        return
    if not isinstance(props, dict):
        props = {}
        data["props"] = props
    props[name] = dict(descriptor)


def described_properties(data: dict) -> dict:
    """Every property on `data` that carries a descriptor."""
    names = set()
    for key in ("props", "anim"):
        holder = data.get(key)
        if isinstance(holder, dict):
            names.update(holder)
    for key, value in data.items():
        if isinstance(value, str) and any(value.startswith(p)
                                          for p in PREFIXES):
            names.add(key)
    return {name: descriptor_for(data, name) for name in sorted(names)}


#: `DLSYS["MODULE/BLOCK/PARAM"]`, or the same path in bare quotes. The
#: bracket form is Azeo's; the quoted form is the shorthand an author
#: reaches for, and accepting both costs one alternation.
_PATH_IN_EXPRESSION = re.compile(
    r"""(?:DLSYS\s*\[\s*)?(['"])(?P<path>[^'"]+)\1\s*\]?""")


def _substitute_paths(text: str) -> tuple[str, dict]:
    """Replace quoted control paths with tokens the grammar accepts.

    Returns the rewritten expression and {token: real path}. A token is
    `P0`, `P1`, … which the tag pattern reads as an ordinary tag.
    """
    mapping: dict = {}

    def swap(match):
        token = f"P{len(mapping)}"
        mapping[token] = match.group("path")
        return token

    return _PATH_IN_EXPRESSION.sub(swap, text or ""), mapping


class _LiveSnapshot:
    """A BindingResult wearing the live model's Quality.

    The two enums share member names and nothing else — `GOOD` is `0`
    in one and `"GOOD"` in the other — so an untranslated result ranks
    as unknown and the whole expression answers Bad.
    """

    __slots__ = ("value", "quality", "shows_value")

    def __init__(self, result: BindingResult):
        from ..model.quality import Quality as LiveQuality
        self.value = result.value
        self.quality = getattr(LiveQuality, result.quality.name,
                               LiveQuality.BAD)
        self.shows_value = result.value is not None \
            and result.quality is not Quality.BAD


def _to_strategy_quality(live_quality) -> Quality:
    """Back the other way, so a Resolved always speaks one dialect."""
    return getattr(Quality, getattr(live_quality, "name", "BAD"),
                   Quality.BAD)


def paths_in_expression(text: str) -> tuple:
    """Every control path an expression reads — for subscriptions and
    for the complexity count."""
    return tuple(_substitute_paths(text)[1].values())


def _worst(*qualities: Quality) -> Quality:
    """Worst-of, the manual's rule for a compound expression."""
    for level in (Quality.BAD, Quality.UNCERTAIN):
        if level in qualities:
            return level
    return Quality.GOOD


class PropertyResolver:
    """Answers descriptors against one display's context.

    Every source is optional: a resolver with no standards store simply
    reports Bad for a standard reference, which is the honest answer
    and keeps the class usable in a test with three lines of setup.
    """

    def __init__(self, *, standards=None, functions=None, variables=None,
                 pvm_properties=None, read=None, clock=None,
                 blink_standard_ms=BLINK_STANDARD_MS,
                 blink_alternate_ms=BLINK_ALTERNATE_MS):
        #: name -> value. `library_explorer.StandardsStore`-shaped, or a
        #: plain mapping.
        self.standards = standards
        #: `functions.FunctionStore`, or anything with `eval(name, value)`.
        self.functions = functions
        #: name -> value, from the containing display / PVM / group.
        self.variables = variables
        #: name -> value, resolved PVM configuration properties.
        self.pvm_properties = pvm_properties
        #: path -> BindingResult. The live half.
        self._read = read
        #: Milliseconds, injected so a blink is testable without waiting.
        self._clock = clock
        self.blink_standard_ms = max(1, int(blink_standard_ms))
        self.blink_alternate_ms = max(1, int(blink_alternate_ms))

    # ------------------------------------------------------------ reads
    def read(self, path: str) -> BindingResult:
        if not path or self._read is None:
            return UNRESOLVED
        try:
            result = self._read(path)
        except Exception:                       # noqa: BLE001
            return UNRESOLVED
        return result if isinstance(result, BindingResult) else UNRESOLVED

    def now_ms(self) -> int:
        if self._clock is not None:
            return int(self._clock())
        import time
        return int(time.monotonic() * 1000)

    # ------------------------------------------------------- lookups
    def _standard(self, ref: str) -> Resolved:
        # `Standard.S_Foo` and `GL.MyLibrary.S_Foo` both name S_Foo.
        name = ref.rsplit(".", 1)[-1] if "." in ref else ref
        store = self.standards
        if store is None:
            return replace(UNSET, kind=STANDARD)
        found = store.get(name) if hasattr(store, "get") else None
        # A StandardsStore answers the whole entry; a plain mapping
        # answers the value. Accept both so a test needs no store.
        value = found.get("value") if isinstance(found, dict) else found
        if value is None:
            return replace(UNSET, kind=STANDARD)
        return Resolved(value, Quality.GOOD, STANDARD)

    def _variable(self, ref: str) -> Resolved:
        name = ref.split(".", 1)[-1] if "." in ref else ref
        source = self.variables
        value = None
        if source is not None:
            value = source.get(name) if isinstance(source, dict) \
                else getattr(source, "get", lambda _n: None)(name)
        if value is None:
            return replace(UNSET, kind=VARIABLE)
        return Resolved(value, Quality.GOOD, VARIABLE)

    def _pvm_property(self, ref: str) -> Resolved:
        key = ref.split(".", 1)[-1] if ref.startswith(PVM_SCOPE_PREFIXES) else ref
        source = self.pvm_properties or {}
        value = source.get(key)
        if value is None and "." in key:
            # `Orientation.BodyRot` may be stored flat under either half.
            head, _, tail = key.partition(".")
            value = source.get(tail, source.get(head))
        if value is None:
            return replace(UNSET, kind=PVM_PROPERTY)
        return Resolved(value, Quality.GOOD, PVM_PROPERTY)

    # -------------------------------------------------------- animation
    def _animation(self, spec: dict) -> Resolved:
        result = self.read(str(spec.get("path", "")))
        if result.quality is Quality.BAD:
            return replace(UNSET, kind=ANIMATION)
        value = result.value
        animation_type = str(spec.get("type", "value"))
        if animation_type == "font":
            value = spec.get("on") if bool(value) else spec.get("off")
        elif animation_type in ("degree", "number") \
                and all(name in spec for name in (
                    "input_start", "input_end", "output_start", "output_end")):
            try:
                source = float(value)
                input_start, input_end = (float(spec["input_start"]),
                                          float(spec["input_end"]))
                output_start, output_end = (float(spec["output_start"]),
                                            float(spec["output_end"]))
                fraction = ((source - input_start)
                            / (input_end - input_start))
                behaviour = str(spec.get("fill", "hold"))
                if behaviour == "default" and not 0.0 <= fraction <= 1.0:
                    value = spec.get("default")
                else:
                    if behaviour != "extrapolate":
                        fraction = max(0.0, min(1.0, fraction))
                    value = output_start + fraction * (
                        output_end - output_start)
            except (TypeError, ValueError, ZeroDivisionError):
                return replace(UNSET, kind=ANIMATION)
        name = spec.get("fn") or spec.get("function") or ""
        if name:
            if self.functions is None:
                return replace(UNSET, kind=ANIMATION)
            function_inputs = spec.get("inputs", ())
            if function_inputs:
                values, qualities = {}, [result.quality]
                for index, one in enumerate(function_inputs):
                    if not isinstance(one, dict):
                        continue
                    path = str(one.get("path", ""))
                    answer = self.read(path)
                    qualities.append(answer.quality)
                    values[str(one.get("name", f"Input{index + 1}"))] = \
                        answer.value
                if Quality.BAD in qualities:
                    return replace(UNSET, kind=ANIMATION)
                value = self.functions.eval(name, values)
                result = replace(result, quality=_worst(*qualities))
            else:
                value = self.functions.eval(name, value)
        if value is None:
            # A function with no answer removes the override; it never
            # invents one.
            return replace(UNSET, kind=ANIMATION)
        return Resolved(value, result.quality, ANIMATION)

    def _blink(self, spec: dict) -> Resolved:
        """Two colours while a condition holds.

        The condition being false or unresolvable yields the default —
        the manual is explicit that an unreadable blink condition must
        NOT leave the element blinking, because a blink that means
        nothing is worse than no blink at all.
        """
        default = spec.get("default")
        result = self.read(str(spec.get("condition", "")))
        if result.quality is Quality.BAD or not result.value:
            if default is None:
                return replace(UNSET, kind=BLINK)
            return Resolved(default, Quality.GOOD, BLINK)
        period = self.blink_alternate_ms \
            if spec.get("style") == "alternate" \
            else self.blink_standard_ms
        phase = (self.now_ms() // max(period, 1)) % 2
        value = spec.get("on") if phase == 0 else spec.get("off")
        if value is None:
            value = default
        if value is None:
            return replace(UNSET, kind=BLINK)
        return Resolved(value, result.quality, BLINK)

    def _expression(self, spec: dict) -> Resolved:
        text = spec.get("expr") or spec.get("text") or ""
        if not text:
            return replace(UNSET, kind=EXPRESSION)
        # The unified Graphics Designer editor stores named inputs beside the
        # expression instead of hiding paths inside quoted source text.  This
        # is both easier to browse and safer to refactor.  Keep the original
        # DLSYS["M/B/P"] grammar below for every existing display.
        refs = spec.get("refs")
        if isinstance(refs, dict):
            from ..binding import expression as binding_expression

            values = {}
            qualities = []
            for alias, path in refs.items():
                result = self.read(str(path))
                if result is UNRESOLVED or result.quality is Quality.BAD \
                        or result.value is None:
                    return replace(UNSET, kind=EXPRESSION)
                values[str(alias)] = result.value
                qualities.append(result.quality)
            problem = binding_expression.check(text, set(values))
            if problem:
                return replace(UNSET, kind=EXPRESSION)
            try:
                value = binding_expression.evaluate(text, values)
            except (ArithmeticError, KeyError, TypeError, ValueError):
                return replace(UNSET, kind=EXPRESSION)
            return Resolved(value, _worst(*qualities), EXPRESSION)
        from ..model.expressions import evaluate

        # `/` is division, so a bare MODULE/BLOCK/PARAM cannot be a
        # token. Azeo has the same problem and answers it the same
        # way — `DLSYS["PID_LOOP_1/PID1/PV.CV"]` — so bracket the path
        # rather than fork the expression grammar into a second
        # evaluator that would drift from the first.
        rewritten, paths = _substitute_paths(text)

        class _Snapshots:
            """`evaluate` wants .get(tag) -> snapshot.

            A BindingResult carries the value and the quality already,
            but its Quality is the STRATEGY enum while the expression
            evaluator ranks the LIVE one — two enums with the same
            member names and different values. Translating by name here
            is what stops every expression silently answering Bad.
            """

            def __init__(self, read, mapping):
                self._read = read
                self._mapping = mapping

            def get(self, tag):
                result = self._read(self._mapping.get(tag, tag))
                if result is UNRESOLVED:
                    return None
                return _LiveSnapshot(result)

        try:
            value, quality = evaluate(rewritten,
                                      _Snapshots(self.read, paths))
        except Exception:                       # noqa: BLE001
            return replace(UNSET, kind=EXPRESSION)
        if value is None:
            return replace(UNSET, kind=EXPRESSION)
        return Resolved(value, _to_strategy_quality(quality), EXPRESSION)

    # ------------------------------------------------------------ entry
    def resolve(self, descriptor: dict | None,
                fallback: object = None) -> Resolved:
        """One descriptor, answered. `fallback` is the static value."""
        if not descriptor:
            return Resolved(fallback, Quality.GOOD, STATIC)
        kind = descriptor.get("kind") or ANIMATION
        ref = str(descriptor.get("ref", ""))
        if kind == STANDARD:
            answer = self._standard(ref)
        elif kind == VARIABLE:
            answer = self._variable(ref)
        elif kind == PVM_PROPERTY:
            answer = self._pvm_property(ref)
        elif kind == BLINK:
            answer = self._blink(descriptor)
        elif kind == EXPRESSION:
            answer = self._expression(descriptor)
        elif kind == ANIMATION:
            answer = self._animation(descriptor)
        else:
            return Resolved(fallback, Quality.GOOD, STATIC)
        if answer.usable:
            return answer
        # Unresolvable: the configured default, else the static value.
        default = descriptor.get("default")
        if default is not None:
            return Resolved(default, Quality.UNCERTAIN, answer.kind)
        if fallback is not None:
            return Resolved(fallback, Quality.UNCERTAIN, answer.kind)
        return answer

    def resolve_property(self, data: dict, name: str,
                         fallback: object = None) -> Resolved:
        """Read one property off an item, descriptor or not."""
        static = data.get(name, fallback)
        return self.resolve(descriptor_for(data, name), static)

    def apply(self, data: dict, names=None) -> dict:
        """Resolve every described property; return {name: Resolved}.

        Used by the live loop: what comes back is written onto the item
        as an override and removed again when it stops resolving, so an
        element never keeps painting an animation that has gone Bad.
        """
        described = described_properties(data)
        if names is not None:
            described = {k: v for k, v in described.items() if k in names}
        return {name: self.resolve(spec, data.get(name))
                for name, spec in described.items()}


def worst_quality(results) -> Quality:
    """Worst quality across an iterable of Resolved or BindingResult."""
    return _worst(*[r.quality for r in results]) if results else Quality.GOOD
