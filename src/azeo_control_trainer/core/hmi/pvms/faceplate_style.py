"""Versioned Azeo Operator Station faceplate profiles.

The runtime uses a small set of fixed shells: a 204 px analog/loop shell,
a 250 px selector/device shell, and wider table shells. Keeping the
accepted dimensions here prevents a Qt layout's size hint from becoming
an accidental faceplate specification.

Sizes are the faceplate *content* in 96-DPI logical pixels.  Native window
frames and Qt's high-DPI scaling sit outside this contract.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FaceplateProfile:
    family: str
    width: int
    height: int
    actions: tuple[str, ...]
    integrated_writes: bool = False


# A profile declares the controls the faceplate owns; its host then
# intersects this catalogue with the services it can actually perform.  This
# preserves AI_fp/TAGAO_fp's documented five-icon row in a capable operator
# station without leaving inert circles in a reduced Studio preview.
_MODULE_ACTIONS = ("detail", "primary", "studio", "history", "ack")
_FALLBACK_ACTIONS = ("detail", "history")
_FB_ACTIONS = ("faceplate",)
_LOOP_ACTIONS = ("detail", "dcc", "primary", "studio", "history", "ack")


# Class names are intentional: the registry is the source of the block-type
# mapping, while these measurements describe the rendered class.  In
# particular PID's cascade-pair variant is a different shell despite sharing
# the PID block type.
PROFILES: dict[str, FaceplateProfile] = {
    # AI_fp / TAGAO_fp / Loop_fp figures.
    "AnalogFaceplate": FaceplateProfile(
        "analog", 204, 468, _MODULE_ACTIONS),
    "PulseInputFaceplate": FaceplateProfile(
        "pulse", 308, 385, _FB_ACTIONS),
    "AnalogOutputFaceplate": FaceplateProfile(
        "analog_output", 204, 468, _MODULE_ACTIONS,
        integrated_writes=True),
    "PIDFaceplate": FaceplateProfile(
        "loop", 203, 537, _LOOP_ACTIONS, integrated_writes=True),
    "FLCFaceplate": FaceplateProfile(
        "loop", 203, 537, _LOOP_ACTIONS, integrated_writes=True),
    "CascadePairFaceplate": FaceplateProfile(
        "cascade", 280, 250, _FB_ACTIONS),
    "PassBalanceFaceplate": FaceplateProfile(
        "balance", 308, 300, _FB_ACTIONS),

    # DC_fp and DCC_fp figures.
    "DeviceFaceplate": FaceplateProfile(
        "device", 250, 560, ("detail", "faceplate")),
    "MotorInterlockFaceplate": FaceplateProfile(
        "conditions", 624, 505, _FB_ACTIONS),

    # State/sequence figures.
    "SfcChartFaceplate": FaceplateProfile(
        "sequence", 367, 468, _FB_ACTIONS),
    "SEQFaceplate": FaceplateProfile("sequence", 367, 468, _FB_ACTIONS),
    "STDFaceplate": FaceplateProfile(
        "state_transition", 620, 400, _FB_ACTIONS),

    # Xmtr_fp / ECTLSL_fp figures.
    "ISELFaceplate": FaceplateProfile("selector", 250, 412, _FB_ACTIONS),
    "LowSelectFaceplate": FaceplateProfile(
        "selector", 250, 412, _FB_ACTIONS),
    "HighSelectFaceplate": FaceplateProfile(
        "selector", 250, 412, _FB_ACTIONS),
    "CTLSLFaceplate": FaceplateProfile("control_selector", 250, 412,
                                        _FB_ACTIONS),

    # AVTR_fp / DVTR_fp and AT_fp figures.
    "AVTRFaceplate": FaceplateProfile("voter", 440, 372, _FB_ACTIONS),
    "DVTRFaceplate": FaceplateProfile("voter", 440, 372, _FB_ACTIONS),
    "ATFaceplate": FaceplateProfile("tracking", 578, 449, _FB_ACTIONS),

    # Trainer-specific blocks use the nearest Azeo function-block shell.
    "RatioFaceplate": FaceplateProfile(
        "calculation", 250, 300, _FB_ACTIONS),
    "TotalizerFaceplate": FaceplateProfile(
        "calculation", 250, 330, _FB_ACTIONS),
    "RampSoakFaceplate": FaceplateProfile("sequence_control", 244, 385,
                                           _FB_ACTIONS),

    # Module detail figures: Loop_dt / DC_dt use the shared two-column
    # configuration-and-diagnostics shell. Interlock and SFC details retain
    # the width needed by their condition/chart bodies.
    "AnalogDetail": FaceplateProfile("detail_analog", 560, 550, ()),
    "PulseInputDetail": FaceplateProfile("detail_pulse", 560, 500, ()),
    "AnalogOutputDetail": FaceplateProfile(
        "detail_analog_output", 560, 500, ()),
    "PIDDetail": FaceplateProfile("detail_pid", 592, 656, ()),
    "DeviceDetail": FaceplateProfile("detail_device", 560, 500, ()),
    "MotorInterlockDetail": FaceplateProfile(
        "detail_conditions", 624, 505, ()),
    "SfcChartDetail": FaceplateProfile("detail_sequence", 620, 400, ()),
}


FALLBACK = FaceplateProfile("module", 250, 360, _FALLBACK_ACTIONS)

for _machine in ("VFDSpeedFaceplate", "TurbineSpeedFaceplate", "CompressorSpeedFaceplate"):
    PROFILES[_machine] = FaceplateProfile(
        "machine", 481, 610, ("detail", "primary", "studio", "history", "ack"),
        integrated_writes=True)


def profile_for(pvm) -> FaceplateProfile:
    """Return a measured shell for every registered faceplate class."""
    return PROFILES.get(type(pvm).__name__, FALLBACK)
