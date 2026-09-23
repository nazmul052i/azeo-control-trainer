"""Vision test — proving the style guide's redundancy rules actually hold.

The conformance checklist asks that *every alarm colour has a paired shape and
passes the monochrome vision test*. A shape next to a colour is easy to add and
easy to get wrong: the point is not that a glyph exists, it is that priority
survives when colour carries no information at all.

So this simulates what a viewer with a colour-vision deficiency sees, and what
a greyscale print shows, and then asks the question that matters: **can these
two priorities still be told apart?**

Three checks, and the third is the one worth having:

1. *Contrast* — does each alarm colour still separate from the display ground?
2. *Separation* — do two priorities remain distinguishable from each other?
3. *Redundancy* — if colour is removed entirely, is there still a difference?
   A pass here is what lets you claim the shape coding does real work.

Deficiency simulation uses the Brettel/Viénot-style projections in linear RGB;
they are approximations, and are used here to catch designs that obviously
fail rather than to certify one that passes.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import hphmi as style

#: The deficiencies worth testing. Together these cover the large majority of
#: colour-vision deficiency; monochromacy is the limiting case and the one the
#: checklist names.
VISION_MODES = ("normal", "deuteranopia", "protanopia", "tritanopia",
                "monochrome")

#: Below this, two colours are close enough that an operator should not be
#: asked to tell them apart at a glance across a console.
MIN_SEPARATION = 24.0

#: Minimum contrast between an alarm colour and the display ground.
MIN_GROUND_CONTRAST = 30.0


def _to_linear(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(value: float) -> int:
    value = max(0.0, min(1.0, value))
    out = 12.92 * value if value <= 0.0031308 else \
        1.055 * (value ** (1 / 2.4)) - 0.055
    return int(round(max(0.0, min(1.0, out)) * 255))


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def rgb_to_hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(c) for c in rgb))


def luminance(value: str) -> float:
    """Relative luminance, 0–255, as a greyscale print would render it."""
    r, g, b = (_to_linear(c) for c in hex_to_rgb(value))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) * 255.0


def simulate(value: str, mode: str = "normal") -> str:
    """What ``value`` looks like to a viewer with ``mode``."""
    if mode == "normal" or mode not in VISION_MODES:
        return value.upper()
    r, g, b = (_to_linear(c) for c in hex_to_rgb(value))

    if mode == "monochrome":
        grey = 0.2126 * r + 0.7152 * g + 0.0722 * b
        r = g = b = grey
    elif mode == "deuteranopia":
        # Green cone missing: red and green collapse onto one axis.
        r, g, b = (0.625 * r + 0.375 * g, 0.700 * r + 0.300 * g, b)
    elif mode == "protanopia":
        r, g, b = (0.567 * r + 0.433 * g, 0.558 * r + 0.442 * g, b)
    elif mode == "tritanopia":
        r, g, b = (0.950 * r + 0.050 * g, g, 0.433 * g + 0.567 * b)
    return rgb_to_hex((_to_srgb(r), _to_srgb(g), _to_srgb(b)))


def distance(first: str, second: str) -> float:
    """Perceptual-ish distance between two colours, weighted for the eye."""
    r1, g1, b1 = hex_to_rgb(first)
    r2, g2, b2 = hex_to_rgb(second)
    dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    # Weighted Euclidean: green dominates perceived brightness, blue least.
    return (2 * dr * dr + 4 * dg * dg + 3 * db * db) ** 0.5


@dataclass
class VisionFinding:
    """One pair of priorities, under one vision mode."""

    mode: str
    first: str
    second: str
    separation: float
    shapes_differ: bool

    @property
    def colour_alone_passes(self) -> bool:
        return self.separation >= MIN_SEPARATION

    @property
    def passes(self) -> bool:
        """Colour may fail, provided the shape carries the distinction.

        That is the whole argument for shape coding: the design is allowed to
        lose colour separation under a deficiency *because* something else
        still tells the two apart.
        """
        return self.colour_alone_passes or self.shapes_differ


def check_priorities(theme: str = "normal") -> list[VisionFinding]:
    """Every priority pair, under every vision mode."""
    names = ("CRITICAL", "WARNING", "ADVISORY", "LOG")
    findings: list[VisionFinding] = []
    for mode in VISION_MODES:
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                findings.append(VisionFinding(
                    mode=mode,
                    first=first,
                    second=second,
                    separation=distance(
                        simulate(style.alarm_colour(first, theme), mode),
                        simulate(style.alarm_colour(second, theme), mode)),
                    shapes_differ=(style.shape_for(first)
                                   != style.shape_for(second)),
                ))
    return findings


def check_ground_contrast(theme: str = "normal") -> list[tuple]:
    """Each alarm colour against the display ground, in every vision mode."""
    from .hphmi import _GROUND

    ground = _GROUND.get(theme, _GROUND["normal"])["bg"]
    out = []
    for mode in VISION_MODES:
        seen_ground = simulate(ground, mode)
        outline = simulate(style.ALARM_OUTLINE, mode)
        outline_gap = distance(outline, seen_ground)
        for name in ("CRITICAL", "WARNING", "ADVISORY"):
            seen = simulate(style.alarm_colour(name, theme), mode)
            # An alarm mark is a fill *and* an outline, so it separates from
            # the ground if either does. Measuring only the fill condemned
            # priority 3 on a light ground, where the outline is exactly what
            # keeps it visible.
            gap = max(distance(seen, seen_ground), outline_gap)
            out.append((mode, name, round(gap, 1), gap >= MIN_GROUND_CONTRAST))
    return out


def report(theme: str = "normal") -> dict:
    """A pass/fail summary, and the cases that only shape rescues.

    ``rescued_by_shape`` is the interesting number: it is the count of pairs
    that would be indistinguishable on colour alone. A design where that is
    zero has not been tested hard enough — it means no deficiency was
    simulated that actually collapses the palette.
    """
    findings = check_priorities(theme)
    contrast = check_ground_contrast(theme)
    rescued = [f for f in findings
               if not f.colour_alone_passes and f.shapes_differ]
    return {
        "theme": theme,
        "pairs": len(findings),
        "failed": [f for f in findings if not f.passes],
        "rescued_by_shape": rescued,
        "contrast_failures": [c for c in contrast if not c[3]],
        "passes": all(f.passes for f in findings)
        and all(c[3] for c in contrast),
    }
