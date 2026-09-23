"""Class-owned typography profiles for canvas PVMs.

A placement may choose a *declared* profile (Standard, Compact, Large),
but it never stores a font, colour or arbitrary style override.  The profile
grid lives in the PVM configuration document, so an Author can change exact
families and point sizes once and every linked placement follows.

Size cells accept either an exact point size (``10`` / ``10pt``) or a
percentage of the painter's measured Azeo size (``125%``).  Percentages are
what let the built-in Standard profile preserve every class's reference
measurement while still allowing one Large profile to work across numeric,
bar and equipment PVM families.
"""
from __future__ import annotations

from dataclasses import dataclass

from .configurator.model import (
    PvmConfiguration, PvmProperty, Option, PropertyGroup,
)

TYPOGRAPHY_PROPERTY = "Typography"
TYPOGRAPHY_GROUP = "Typography"
TYPOGRAPHY_COLUMNS = (
    "TextFamily", "NumberFamily", "TagSize", "ValueSize",
    "SecondarySize", "ModeSize", "ScaleSize", "AlarmSize",
)

_DEFAULT_SPECS = {
    "tag": "100%",
    "value": "100%",
    "secondary": "100%",
    "mode": "100%",
    "scale": "100%",
    "alarm": "100%",
}


def typography_property() -> PvmProperty:
    """The editable, named profile grid installed on coded PVM classes."""
    columns = list(TYPOGRAPHY_COLUMNS)
    return PvmProperty(
        TYPOGRAPHY_PROPERTY,
        "Selection",
        title="Typography profile",
        description=(
            "Class-owned text and numeric typography. Size cells accept "
            "points (10 or 10pt) or a percentage of the measured class "
            "size (125%). A placement stores only the profile name."),
        default="Standard",
        columns=columns,
        options=[
            Option("Standard", ["", "", "100%", "100%", "100%",
                                "100%", "100%", "100%"]),
            Option("Compact", ["", "", "88%", "88%", "88%",
                               "90%", "88%", "90%"]),
            Option("Large", ["", "", "125%", "135%", "120%",
                             "120%", "120%", "125%"]),
        ],
    )


def ensure_typography(config: PvmConfiguration) -> PvmConfiguration:
    """Install the class-owned profile grid when an older document lacks it.

    The migration is in memory until an Author edits and saves the class; old
    files remain byte-identical merely by opening Graphics Designer.
    """
    if config.property(TYPOGRAPHY_PROPERTY) is not None:
        return config
    group = next((one for one in config.groups
                  if one.name == TYPOGRAPHY_GROUP), None)
    if group is None:
        # Appearance choices do not subscribe to the controller namespace.
        group = PropertyGroup(TYPOGRAPHY_GROUP, [], present_online=False)
        config.groups.append(group)
    group.properties.append(typography_property())
    return config


def typography_configuration(class_name: str) -> PvmConfiguration:
    """A synthetic default for a class with no sidecar document yet."""
    return PvmConfiguration(class_name, [
        PropertyGroup(TYPOGRAPHY_GROUP, [typography_property()],
                      present_online=False),
    ])


def _size(spec: str, base: float) -> float:
    text = str(spec or "100%").strip().lower()
    try:
        if text.endswith("%"):
            result = float(text[:-1]) * float(base) / 100.0
        else:
            result = float(text.removesuffix("pt"))
    except (TypeError, ValueError):
        result = float(base)
    # Refuse invisible text and pathological class values without making a
    # malformed profile capable of crashing QFont on the paint hot path.
    return max(4.0, min(48.0, result))


@dataclass(frozen=True)
class PvmTypography:
    """One resolved profile, independent of Qt and safe on the paint path."""

    name: str = "Standard"
    text_family: str = ""
    number_family: str = ""
    tag: str = "100%"
    value: str = "100%"
    secondary: str = "100%"
    mode: str = "100%"
    scale: str = "100%"
    alarm: str = "100%"

    def family(self, role: str, fallback: str) -> str:
        chosen = self.number_family if role in ("value", "scale") \
            else self.text_family
        return chosen.strip() or fallback

    def point_size(self, role: str, base: float) -> float:
        return _size(getattr(self, role, _DEFAULT_SPECS.get(role, "100%")),
                     base)


def resolve_typography(config: PvmConfiguration | None,
                       choices: dict | None = None) -> PvmTypography:
    """Resolve one placement's named class profile to paint-ready values."""
    if config is None or config.property(TYPOGRAPHY_PROPERTY) is None:
        return PvmTypography()
    choices = choices or {}
    name = config.value_of(TYPOGRAPHY_PROPERTY, choices) or "Standard"

    def cell(column: str, fallback: str = "") -> str:
        value = config.subvalue(
            f"{TYPOGRAPHY_PROPERTY}.{column}", choices)
        return str(value) if value != "" else fallback

    return PvmTypography(
        name=name,
        text_family=cell("TextFamily"),
        number_family=cell("NumberFamily"),
        tag=cell("TagSize", "100%"),
        value=cell("ValueSize", "100%"),
        secondary=cell("SecondarySize", "100%"),
        mode=cell("ModeSize", "100%"),
        scale=cell("ScaleSize", "100%"),
        alarm=cell("AlarmSize", "100%"),
    )


__all__ = [
    "PvmTypography", "TYPOGRAPHY_COLUMNS", "TYPOGRAPHY_PROPERTY",
    "ensure_typography", "resolve_typography", "typography_configuration",
    "typography_property",
]
