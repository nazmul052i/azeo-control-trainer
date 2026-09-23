"""Type roles, in the same spirit as colour roles.

`docs/02-design-tokens.md` fixes four uses — process value, tag/label,
engineering unit, chrome — with families, sizes and weights. A painter asks
for a role and gets a font, for the same reason it asks for a colour role: a
value that shrinks to fit is a value an operator misreads at a metre.

The one rule worth stating twice: **process values are monospace with tabular
figures.** A column of proportional digits will not decimal-align, and a
number that shifts sideways as it changes is a number the eye has to re-find
on every scan.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

#: The typefaces ship with the package rather than being asked of the host.
#:
#: Two reasons, and the second is the real one. **It has to work headless**:
#: under the offscreen platform this build reports *zero* font families, so a
#: display bound to the system font renders every glyph as a box — which is
#: what the first run of the golden matrix produced. And **a console must
#: look the same everywhere**: `docs/console/07` requires chrome geometry to be
#: byte-identical on every display, and that cannot hold if the workstation's
#: font substitution decides where the text lands. Vendoring makes the
#: reference images portable for the same reason.
#:
#: DejaVu: Bitstream Vera licence plus public-domain changes — see
#: `assets/LICENSE_DEJAVU`.
ASSETS = Path(__file__).resolve().parent / "assets"


def ensure_font_directory() -> Path:
    """Point Qt at the vendored faces. **Call before `QApplication`.**

    PySide6 ships no fonts, and on a machine without a system font directory
    Qt prints a warning to the console on every start:

        QFontDatabase: Cannot find font directory .../PySide6/lib/fonts.
        Note that Qt no longer ships fonts.

    That is not cosmetic noise to be filtered — it is Qt telling you its font
    database is empty, which is the same condition that made every glyph in
    the first Studio screenshot render as a box (Q21). We already vendor
    DejaVu for exactly this reason; this hands Qt the directory it is asking
    for, so the warning goes away because the cause does.

    Setting the variable only matters *before* the platform plugin starts, so
    this is called from the entry points, not from `apply_application_font`.
    """
    import os

    if ASSETS.is_dir() and not os.environ.get("QT_QPA_FONTDIR"):
        os.environ["QT_QPA_FONTDIR"] = str(ASSETS)
    return ASSETS
MONO_FAMILIES = ("DejaVu Sans Mono", "Consolas", "Liberation Mono")
SANS_FAMILIES = ("DejaVu Sans", "Segoe UI", "Liberation Sans")

_FONT_FILES = (
    "DejaVuSans.ttf", "DejaVuSans-Bold.ttf",
    "DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf",
)


@lru_cache(maxsize=1)
def load_fonts() -> tuple[str, ...]:
    """Register the packaged faces. Idempotent, and safe to call often.

    Needs a `QGuiApplication` to exist, so it is called lazily from
    :func:`font_for` rather than at import: importing a theme module must not
    require an application object.
    """
    loaded = []
    for name in _FONT_FILES:
        path = ASSETS / name
        if not path.exists():                              # pragma: no cover
            continue
        if QFontDatabase.addApplicationFont(str(path)) != -1:
            loaded.append(name)
    return tuple(loaded)


class FontRole(str, Enum):
    """What a piece of text *is*, not what it should look like."""

    #: The number an operator acts on. Monospace, heavy, never below 14 px.
    VALUE = "VALUE"
    #: A secondary value: the sub-line SP, a bar endpoint.
    VALUE_SMALL = "VALUE_SMALL"
    #: The big number on a KPI tile.
    VALUE_LARGE = "VALUE_LARGE"
    #: Tag names and captions.
    LABEL = "LABEL"
    #: Engineering units. Smaller than the value they qualify, deliberately —
    #: the unit is context, the number is the message.
    UNIT = "UNIT"
    #: Chrome: identity row, status bar, banner.
    CHROME = "CHROME"
    #: Chrome emphasis: counts, the banner's leading line.
    CHROME_BOLD = "CHROME_BOLD"
    #: Chrome *data*: the identity meta line, banner lines, status cells.
    #: Monospace because those columns line up between one scan and the next
    #: — a timestamp whose digits shift the text sideways is a timestamp the
    #: eye has to find again every second.
    CHROME_MONO = "CHROME_MONO"
    CHROME_MONO_BOLD = "CHROME_MONO_BOLD"
    #: Scale endpoints, hints, the `UNBOUND` placeholder.
    TINY = "TINY"


_SPECS = {
    FontRole.VALUE: (MONO_FAMILIES, 14, QFont.DemiBold),
    FontRole.VALUE_SMALL: (MONO_FAMILIES, 10, QFont.Normal),
    FontRole.VALUE_LARGE: (MONO_FAMILIES, 20, QFont.DemiBold),
    FontRole.LABEL: (SANS_FAMILIES, 10, QFont.Normal),
    FontRole.UNIT: (SANS_FAMILIES, 9, QFont.Normal),
    FontRole.CHROME: (SANS_FAMILIES, 11, QFont.Normal),
    FontRole.CHROME_BOLD: (SANS_FAMILIES, 11, QFont.DemiBold),
    FontRole.CHROME_MONO: (MONO_FAMILIES, 10, QFont.Normal),
    FontRole.CHROME_MONO_BOLD: (MONO_FAMILIES, 10, QFont.DemiBold),
    FontRole.TINY: (SANS_FAMILIES, 8, QFont.Normal),
}


def apply_application_font() -> QFont:
    """Make the packaged face the application default.

    The painters ask for a role and get the right font; **Qt's own widgets do
    not**. A dock, a spin box and a menu take the application font, which
    under the offscreen platform resolves to nothing at all — the first
    Studio screenshot had boxes in every field. Setting it once here also
    means the chrome, the docks and the canvas agree, which is the same
    argument as vendoring the file in the first place (Q21).
    """
    from PySide6.QtWidgets import QApplication

    # Role fonts are deliberately pixel-sized: canvas geometry must not move
    # with the workstation's font substitution.  Qt-owned widgets are a
    # different boundary.  On Windows an editable QComboBox copies the
    # application font into its QLineEdit by calling setPointSize(); a
    # pixel-only QFont reports pointSize() == -1, producing one warning per
    # editor.  Give the application a point-sized COPY at the same physical
    # size while leaving the cached painter role untouched.
    font = QFont(font_for(FontRole.CHROME))
    app = QApplication.instance()
    if app is not None:
        screen = app.primaryScreen()
        dpi = screen.logicalDotsPerInch() if screen is not None else 96.0
        if dpi <= 0:
            dpi = 96.0
        pixels = max(1, font.pixelSize())
        font.setPointSizeF(max(1.0, pixels * 72.0 / dpi))
        # Opening another product must not broadcast font changes through
        # the live native editors of every existing window. Qt still emits
        # ApplicationFontChange for repeated class overrides of equal fonts.
        if app.font() != font:
            app.setFont(font)
        # QApplication keeps per-widget-class overrides separately from its
        # default. Native styles also create editors and popup surfaces lazily,
        # so one of those classes can retain the old pixel-only font after the
        # application default is repaired. Repair every Qt-owned class that
        # copies a point size internally; stylesheet sizes can still style
        # individual controls in points.
        for widget_class in (
            "QComboBox", "QFontComboBox", "QLineEdit", "QAbstractSpinBox",
            "QSpinBox", "QDoubleSpinBox", "QMenu", "QToolTip",
        ):
            if app.font(widget_class) != font:
                app.setFont(font, widget_class)
    return font


@lru_cache(maxsize=64)
def font_for(role: FontRole, size: int | None = None) -> QFont:
    """The font for a role, optionally at an explicit pixel size.

    ``size`` exists for the one prop that sets it — `text.size`, which is an
    engineering decision about a caption, not a style override. Cached because
    building a QFont per repaint is waste in the one loop that must stay cheap.
    """
    load_fonts()
    families, default_size, weight = _SPECS[role]
    font = QFont()
    font.setFamilies(list(families))
    font.setPixelSize(size or default_size)
    font.setWeight(weight)
    if families is MONO_FAMILIES:
        font.setFixedPitch(True)
        # Tabular figures: digits share an advance width, so a value that
        # changes does not shift the ones beside it.
        font.setStyleHint(QFont.Monospace)
    else:
        font.setStyleHint(QFont.SansSerif)
    return font
