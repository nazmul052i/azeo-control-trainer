"""Derived values: arithmetic over tags, with quality that cannot lie.

The reason DynaLive resisted expressions is worth restating before allowing
them: **a display that computes is a display that can lie about the plant.**
Azeo Operator Station's expression language is general enough to hide logic inside a
graphic, where no module listing will ever show it. This is deliberately not
that. What is allowed:

- tag names, numeric literals, ``+ - * /`` and parentheses — arithmetic,
  nothing else. No comparisons, no conditionals, no functions, no strings.
  The moment an expression can *decide* something, it is control logic
  living in a picture, and control logic belongs in a module where the
  compiler, the tag database and the version history can see it.
- **quality is the worst of the inputs.** A difference computed from one
  good flow and one bad one is bad, full stop. Propagating anything less
  would put a plausible number on the console with nothing marking that
  half of it was invented — invariant I6, applied to arithmetic.
- an error — unknown tag, division by zero, a typo — evaluates to no value
  at Bad quality, never to zero. Zero is a reading; a broken expression is
  not.

Parsing is a hand-rolled recursive descent over a dozen lines rather than
`eval` with a filter, because a filtered `eval` is a sandbox and sandboxes
leak. This parser cannot execute anything it cannot name.
"""
from __future__ import annotations

import re

from .quality import Quality

#: What a tag looks like inside an expression. Deliberately the same shape
#: the converter recognises: letters, digits and dashes starting with a
#: letter — `FT-101`, `LIC-2104`.
_TOKEN = re.compile(r"\s*(?:"
                    r"(?P<number>\d+(?:\.\d+)?)|"
                    r"(?P<tag>[A-Za-z][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)*)|"
                    r"(?P<op>[-+*/()])"
                    r")")

_WORSE = {Quality.GOOD: 0, Quality.SIM: 1, Quality.UNCERTAIN: 2,
          Quality.OOS: 3, Quality.BAD: 4}


def tags_in(expression: str) -> tuple[str, ...]:
    """Every tag the expression reads — for validation and subscriptions."""
    found = []
    for match in _TOKEN.finditer(expression or ""):
        if match.group("tag"):
            found.append(match.group("tag"))
    return tuple(dict.fromkeys(found))


def _tokens(expression: str):
    position = 0
    while position < len(expression):
        match = _TOKEN.match(expression, position)
        if match is None or match.end() == position:
            remainder = expression[position:].strip()
            if remainder:
                raise ValueError(f"cannot read {remainder[:12]!r}")
            return
        position = match.end()
        if match.group("number"):
            yield ("number", float(match.group("number")))
        elif match.group("tag"):
            yield ("tag", match.group("tag"))
        else:
            yield ("op", match.group("op"))


class _Parser:
    """value := term (('+'|'-') term)* ; term := factor (('*'|'/') factor)*
    ; factor := number | tag | '-' factor | '(' value ')'"""

    def __init__(self, expression: str, lookup):
        self.tokens = list(_tokens(expression))
        self.at = 0
        self.lookup = lookup
        self.quality = Quality.GOOD

    def _peek(self):
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def _take(self):
        token = self._peek()
        self.at += 1
        return token

    def value(self) -> float:
        left = self.term()
        while self._peek() in (("op", "+"), ("op", "-")):
            _, op = self._take()
            right = self.term()
            left = left + right if op == "+" else left - right
        return left

    def term(self) -> float:
        left = self.factor()
        while self._peek() in (("op", "*"), ("op", "/")):
            _, op = self._take()
            right = self.factor()
            if op == "/":
                if right == 0:
                    raise ZeroDivisionError
                left = left / right
            else:
                left = left * right
        return left

    def factor(self) -> float:
        token = self._take()
        if token is None:
            raise ValueError("expression ended early")
        kind, payload = token
        if kind == "number":
            return payload
        if kind == "tag":
            value, quality = self.lookup(payload)
            if _WORSE[quality] > _WORSE[self.quality]:
                self.quality = quality
            if value is None:
                raise LookupError(payload)
            return float(value)
        if token == ("op", "-"):
            return -self.factor()
        if token == ("op", "("):
            inner = self.value()
            if self._take() != ("op", ")"):
                raise ValueError("missing ')'")
            return inner
        raise ValueError(f"unexpected {payload!r}")


def evaluate(expression: str, snapshots) -> tuple[float | None, Quality]:
    """`(value, quality)` — the worst input quality, or Bad on any error."""

    def lookup(tag: str):
        snapshot = snapshots.get(tag)
        if snapshot is None:
            return None, Quality.BAD
        value = getattr(snapshot, "value", None)
        quality = getattr(snapshot, "quality", Quality.BAD)
        if not getattr(snapshot, "shows_value", value is not None):
            return None, Quality.BAD
        return value, quality

    try:
        parser = _Parser(expression, lookup)
        value = parser.value()
        if parser._peek() is not None:
            raise ValueError("trailing input")
        return value, parser.quality
    except (ValueError, LookupError, ZeroDivisionError, KeyError):
        return None, Quality.BAD


def derived_snapshot(expression: str, snapshots, *, label: str = ""):
    """A `TagSnapshot` for a derived value, honest about what it is not.

    The definition is synthesised — a derived value has no instrument, so it
    has no engineering range, no alarm limits and no faceplate worth
    opening. Range 0..100 exists only so a painter has *something*; a
    display author who wants a meaningful bar should bind a real tag. The
    description carries the expression itself, so hovering the object shows
    where the number comes from rather than presenting it as a measurement.
    """
    from .tags import TagDef, TagSnapshot

    value, quality = evaluate(expression, snapshots)
    stamps = [getattr(snapshots.get(tag), "timestamp", 0.0)
              for tag in tags_in(expression) if tag in snapshots]
    definition = TagDef(tag=f"={expression}", description=f"= {expression}",
                        eu="", range_lo=0.0, range_hi=100.0, decimals=1)
    return TagSnapshot(tag=f"={expression}", value=value, quality=quality,
                       timestamp=min(stamps) if stamps else 0.0,
                       definition=definition)
