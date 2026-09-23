"""Display complexity measures and index — the manual's own arithmetic.

Azeo Operator Station predicts a display's call-up time from three counts and
publishes the formula, which makes it one of the few HMI performance
claims that can be checked rather than argued about. That is exactly
the discipline this repo already applies to scan cost and to repaint
cost, so it is worth having rather than approximating.

The three measures (`Ensuring optimal online display performance`):

- **Total Present Online Event Handlers** — events carrying a script
  that will be present online.
- **Total Present Online Control Tags** — distinct control tags the
  configuration references.
- **Total Present Online Control Parameters** — control parameters
  that *may* be present online, counted per display and **per PVM
  instance**, so two PVMs on the same module count twice. The manual
  is explicit that this over-counts what Azeo Operator Station actually
  requests, and equally explicit that it is still the better predictor
  of call-up time. Keep the over-count.

Only elements that are *present online* count. An element or a PVM
custom property with `Present Online` false is not deployed, so it
cannot cost anything — which is why `Presence` gating in
`configurator/model.py` is a performance feature and not just a
visibility one.

**The index** is the formula in the manual, transcribed:

    handlers / 42  +  tags / 1.2  +  params / 33  =  complexity index

It reproduces the manual's worked example exactly (100 handlers, 50
tags, 1000 parameters -> 74), which is the test. Note the divisors are
not the "relative weights" table (1 / 37 / 1.5); those weights are
described as approximate and are for reasoning about which measure to
attack first. The divisors are the arithmetic.

The number is a **benchmark, not a grade**. The manual says so twice
and it matters here more than there: a training display that scores
120 is not broken, it is slower to call up than one that scores 40 on
the same machine.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The manual's Complexity Index Formula divisors.
HANDLER_DIVISOR = 42.0
TAG_DIVISOR = 1.2
PARAMETER_DIVISOR = 33.0

#: The relative weights the manual tabulates, for prioritising which
#: measure to reduce. Deliberately NOT used to compute the index.
RELATIVE_WEIGHTS = {"handlers": 1.0, "tags": 37.0, "parameters": 1.5}


@dataclass(frozen=True)
class Complexity:
    """One display's measures and its index."""

    handlers: int = 0
    tags: int = 0
    parameters: int = 0

    @property
    def index(self) -> float:
        return (self.handlers / HANDLER_DIVISOR
                + self.tags / TAG_DIVISOR
                + self.parameters / PARAMETER_DIVISOR)

    @property
    def dominant(self) -> str:
        """Which measure to attack first, by weighted contribution."""
        scored = {"handlers": self.handlers * RELATIVE_WEIGHTS["handlers"],
                  "tags": self.tags * RELATIVE_WEIGHTS["tags"],
                  "parameters": (self.parameters
                                 * RELATIVE_WEIGHTS["parameters"])}
        return max(scored, key=lambda k: (scored[k], k))

    def to_dict(self) -> dict:
        return {"handlers": self.handlers, "tags": self.tags,
                "parameters": self.parameters,
                "index": round(self.index, 1),
                "dominant": self.dominant}

    def __str__(self) -> str:
        return (f"index {self.index:.0f}  "
                f"({self.handlers} handlers, {self.tags} tags, "
                f"{self.parameters} parameters; reduce {self.dominant})")


def _tag_of(path: str) -> str:
    """MODULE from `MODULE/BLOCK/PARAM` — a control TAG is the module."""
    return str(path).split("/", 1)[0] if path else ""


def _present_online(data: dict) -> bool:
    """Whether this item is deployed at all.

    `visible: False` is NOT the same question — a hidden element is
    still online and still costs its subscriptions. Only `present`
    false removes it, matching the manual's Present Online property.
    """
    return bool(data.get("present", True))


def measure_items(items, pvms=(), extra_handlers: int = 0) -> Complexity:
    """Count one display's measures.

    `items` are drawing-item dicts; `pvms` are placement records. Both
    are counted the way the manual counts them: tags distinct across
    the display, parameters **per instance** so repeats accumulate.
    """
    from .properties import ANIMATION, BLINK, EXPRESSION, \
        described_properties
    from .elements import element_paths

    tags: set = set()
    parameters = 0
    handlers = extra_handlers

    def account(path: str) -> None:
        nonlocal parameters
        if not path:
            return
        parameters += 1
        tag = _tag_of(path)
        if tag:
            tags.add(tag)

    for data in items or ():
        if not isinstance(data, dict) or not _present_online(data):
            continue
        for spec in described_properties(data).values():
            if not spec:
                continue
            kind = spec.get("kind")
            if kind == ANIMATION:
                account(str(spec.get("path", "")))
            elif kind == BLINK:
                account(str(spec.get("condition", "")))
            elif kind == EXPRESSION:
                for path in _paths_in_expression(
                        str(spec.get("expr", ""))):
                    account(path)
        if data.get("actions"):
            handlers += len(data["actions"])
        if data.get("kind") == "datalink":
            account(str(data.get("path", "")))
        elif data.get("kind") == "display_link" \
                and data.get("target"):
            handlers += 1
        elif data.get("kind") == "user_entry":
            entry = data.get("entry") or {}
            account(str(entry.get("path", "")))
            handlers += 1
        else:
            for path in element_paths(data):
                account(path)
            if data.get("kind") == "tab":
                handlers += len(data.get("tabs", ()))

    for pvm in pvms or ():
        record = pvm if isinstance(pvm, dict) else getattr(
            pvm, "__dict__", {})
        if not _present_online(record):
            continue
        params = record.get("params") or {}
        for value in params.values():
            if isinstance(value, str) and "/" in value:
                account(value)
        # Every bound row on a card is its own subscription.
        parameters += int(record.get("bound_rows", 0) or 0)

    return Complexity(handlers=handlers, tags=len(tags),
                      parameters=parameters)


def _paths_in_expression(text: str) -> list:
    """Control paths named inside an arithmetic expression."""
    import re
    return re.findall(r"[A-Za-z_][\w-]*(?:/[A-Za-z_][\w.-]*)+", text or "")


def measure_display(display, renderer=None) -> Complexity:
    """Measure a `PvmDisplay`, counting each PVM's real bound rows.

    Passing the renderer makes the parameter count honest: a card that
    binds PV, SP and OUT costs three subscriptions, not one, and only
    the renderer knows how many its class declares.
    """
    pvms = []
    for pvm in getattr(display, "pvms", ()) or ():
        record = dict(pvm) if isinstance(pvm, dict) else {
            "params": dict(getattr(pvm, "params", {})),
            "present": True}
        if renderer is not None:
            record["bound_rows"] = _bound_rows(renderer, pvm)
        pvms.append(record)
    return measure_items(getattr(display, "items", ()) or (), pvms)


def _bound_rows(renderer, pvm) -> int:
    """How many subscriptions one placement's class actually declares."""
    try:
        from .base import registry
        from .rendering.renderer import pvm_from_dict
        record = pvm_from_dict(pvm) if isinstance(pvm, dict) else pvm
        pvm_cls = (registry.get(record.block_type, record.role,
                                record.variant)
                   or registry.get(record.block_type, record.role))
        specs = renderer.class_bindings(pvm_cls)
        return sum(1 for s in specs if not s.prop and not s.expr)
    except Exception:                           # noqa: BLE001
        return 0


def report(name: str, complexity: Complexity,
           benchmark: float | None = None) -> str:
    """One display's line, with a benchmark comparison when given."""
    line = f"{name}: {complexity}"
    if benchmark:
        ratio = complexity.index / benchmark if benchmark else 0
        verdict = "at" if 0.9 <= ratio <= 1.1 else (
            "below" if ratio < 0.9 else "above")
        line += f"  [{verdict} the {benchmark:.0f} benchmark]"
    return line
