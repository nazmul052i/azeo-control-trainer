"""Frozen visual metrics for Azeo High Performance PVM classes.

These dimensions and colours are the accepted product contract at 96-DPI
logical resolution. Keeping them in one Qt-free module prevents individual
painters from drifting and lets registry tests prove that every installed
class uses the same geometry and palette vocabulary.

Sizes are intentionally exact. High Performance PVMs are placed at their
native design size; responsive authoring scales the whole retained-mode item
uniformly instead of asking each painter to invent a new layout.
"""

from __future__ import annotations

#: The card frame grey shared by every installed HP class.
FRAME = "#BFBFBF"

#: Placement size (w, h) at 100 %, per Azeo HP PVM class.
#: Frozen product contract; changes require a visual-regression update.
SIZES: dict[str, tuple[float, float]] = {
    "HP_Alarms": (116.0, 44.0),
    "HP_AT_VLV_": (88.0, 46.0),
    "HP_B_Valve": (72.0, 47.0),
    "HP_C_Valve": (72.0, 47.0),
    "HP_EDC_PMP_": (88.0, 46.0),
    "HP_EDC_VLV_": (88.0, 46.0),
    "HP_HorizAnalog": (182.0, 34.0),
    "HP_M1_CH_S_": (128.0, 39.0),
    "HP_MA1_CH_": (132.0, 64.0),
    "HP_MA1_DH_": (132.0, 64.0),
    "HP_MA1_N_": (132.0, 46.0),
    "HP_MA1_VLV_": (74.0, 46.0),
    "HP_MA2_DH_": (128.0, 39.0),
    "HP_MA2_DV_M_": (55.0, 158.0),
    "HP_MA2_DV_S_": (55.0, 117.0),
    "HP_MA3_CH_": (128.0, 39.0),
    "HP_MA3_CV_M_": (55.0, 158.0),
    "HP_MA3_CV_ML_": (55.0, 158.0),
    "HP_MA3_CV_S_": (55.0, 117.0),
    "HP_MA3_CV_SL_": (55.0, 117.0),
    "HP_MA3_N_": (82.0, 38.0),
    "HP_MD1_PMP_": (88.0, 46.0),
    "HP_MD1_VLV_": (88.0, 46.0),
    "HP_MSHDI_": (128.0, 39.0),
    "HP_MSHDO_": (128.0, 39.0),
    "HP_Numeric": (115.0, 42.0),
    "HP_Pump": (72.0, 47.0),
    "HP_VertAnalog": (34.0, 184.0),
}

#: Shared HP palette. Only colours with a defined semantic role belong here.
PALETTE: dict[str, str] = {
    # Process-value bar family.
    "BAR_PV": "#7B92AD",
    "BAR_PV_LIGHT": "#9BADC6",
    "BAR_SCALE_END": "#EBECF1",
    # The card itself.
    "CARD_FRAME": "#BFBFBF",
    "CARD_FACE": "#DFD7CC",
    # Text, both supported weights.
    "TEXT": "#404040",
    "TEXT_STRONG": "#202020",
}

#: All installed classes are registered by ``hp.classes``. Importing
#: lazily keeps this metrics module declaration-cycle free while
#: those classes read ``SIZES``.
def implemented() -> dict[str, tuple[str, str, str]]:
    from .classes import HP_CLASS_KEYS
    return dict(HP_CLASS_KEYS)


class _Implemented(dict):
    """Mapping view retained for existing IMPLEMENTED callers."""

    def _data(self):
        return implemented()

    def items(self):
        return self._data().items()

    def values(self):
        return self._data().values()

    def keys(self):
        return self._data().keys()

    def __iter__(self):
        return iter(self._data())

    def __len__(self):
        return len(self._data())

    def __getitem__(self, key):
        return self._data()[key]

    def get(self, key, default=None):
        return self._data().get(key, default)

    def __contains__(self, key):
        return key in self._data()


IMPLEMENTED: dict[str, tuple[str, str, str]] = _Implemented()


def size_for(pvm_key: tuple[str, str, str]) -> tuple[float, float] | None:
    """The Azeo size a registered PVM class must render at."""
    for name, key in IMPLEMENTED.items():
        if key == pvm_key:
            return SIZES[name]
    return None
