"""The full faceplate registry must stay on explicit product contracts."""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication

import azeo_control_trainer.core.hmi.pvms  # noqa: F401,E402
from azeo_control_trainer.core.hmi.binding import (  # noqa: E402
    BindingEngine,
    LiveGraphSource,
)
from azeo_control_trainer.core.hmi.pvms.base import registry  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.faceplate_catalog import (  # noqa: E402
    CONTRACTS,
)
from azeo_control_trainer.core.hmi.pvms.faceplate_style import (  # noqa: E402
    PROFILES,
)
from azeo_control_trainer.core.hmi.pvms.render_bars import (  # noqa: E402
    CLASS_VISUALS,
)
from azeo_control_trainer.core.hmi.pvms.render import (  # noqa: E402
    PvmFaceplateWidget,
)
from azeo_control_trainer.core.hmi.theme.tokens import THEMES  # noqa: E402


def _application():
    return QApplication.instance() or QApplication([])


def _registered_faceplate_names() -> set[str]:
    return {
        pvm_class.__name__
        for (_block_type, role, _variant), pvm_class
        in registry.all_classes().items()
        if role == "faceplate"
    }


def test_every_registered_faceplate_has_one_measured_whole_surface() -> None:
    _application()
    names = _registered_faceplate_names()
    assert names == set(PROFILES) - {
        name for name in PROFILES if name.endswith("Detail")}
    assert names <= set(CLASS_VISUALS)
    for name in names:
        surface = CLASS_VISUALS[name](THEMES["azeo_live"])
        profile = PROFILES[name]
        assert surface.WHOLE_SURFACE is True
        assert (surface.sizeHint().width(), surface.sizeHint().height()) == (
            profile.width, profile.height)


def test_every_faceplate_declares_an_explicit_visual_contract() -> None:
    names = _registered_faceplate_names()
    assert names == set(CONTRACTS)
    assert {contract.fidelity for contract in CONTRACTS.values()} == {
        "native", "adapted"}
    for name, contract in CONTRACTS.items():
        assert contract.design_id, name
        if contract.fidelity == "adapted":
            assert contract.note, name


def test_narrower_trainer_contracts_are_not_labelled_exact() -> None:
    adapted = {
        "ATFaceplate",
        "CTLSLFaceplate",
        "DeviceFaceplate",
        "MotorInterlockFaceplate",
    }
    for name in adapted:
        contract = CONTRACTS[name]
        assert contract.fidelity == "adapted"
        assert "trainer" in contract.note.lower()


def test_module_analog_profiles_keep_the_documented_five_action_row() -> None:
    expected = ("detail", "primary", "studio", "history", "ack")
    assert PROFILES["AnalogFaceplate"].actions == expected
    assert PROFILES["AnalogOutputFaceplate"].actions == expected


def test_function_block_profiles_use_faceplate_not_detail_action() -> None:
    module_profiles = {
        "AnalogFaceplate", "AnalogOutputFaceplate", "PIDFaceplate",
        "FLCFaceplate", "DeviceFaceplate",
        "VFDSpeedFaceplate", "TurbineSpeedFaceplate", "CompressorSpeedFaceplate",
    }
    for name, profile in PROFILES.items():
        if name.endswith("Detail") or name in module_profiles:
            continue
        assert profile.actions == ("faceplate",), name

    assert PROFILES["DeviceFaceplate"].actions == (
        "detail", "faceplate")


def test_fixed_detail_shells_do_not_compress_their_panels() -> None:
    """AO, pulse and device details must receive their full profile width."""
    app = _application()
    engine = BindingEngine(LiveGraphSource(lambda: {}))

    for block_type in ("AO", "PIN", "DEVCTL"):
        pvm_class = registry.get(block_type, "detail")
        assert pvm_class is not None
        widget = PvmFaceplateWidget(
            pvm_class, {"path": f"M/{block_type}1"}, engine)
        try:
            widget.show()
            app.processEvents()
            margins = widget.faceplate_surface.layout().contentsMargins()
            assert (
                margins.left(), margins.top(),
                margins.right(), margins.bottom(),
            ) == (0, 0, 0, 0)
            assert widget.detail_panel.geometry().width() == (
                widget.faceplate_profile.width)
            assert widget.detail_panel.minimumSizeHint().width() <= (
                widget.detail_panel.width())
        finally:
            widget.close()
            widget.deleteLater()
            app.processEvents()
    assert "dcc" in PROFILES["PIDFaceplate"].actions
