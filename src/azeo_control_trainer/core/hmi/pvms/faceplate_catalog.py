"""Auditable product contracts for every installed faceplate.

``native`` means the geometry and runtime contract are designed together for
the named Azeo block. ``adapted`` means an established shell and visual
grammar are reused for a narrower or trainer-specific block contract. An
adapted surface must describe the difference instead of fabricating data.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FaceplateContract:
    fidelity: str
    design_id: str
    note: str = ""


def _native(design_id: str) -> FaceplateContract:
    return FaceplateContract("native", design_id)


def _adapted(design_id: str, note: str) -> FaceplateContract:
    return FaceplateContract("adapted", design_id, note)


CONTRACTS: dict[str, FaceplateContract] = {
    "VFDSpeedFaceplate": _adapted("machine-speed", "Pairs the trainer PID speed loop and DEVCTL run controller; no drive parameter or hardware protocol is invented."),
    "TurbineSpeedFaceplate": _adapted("machine-speed", "Pairs the trainer PID governor interface and DEVCTL run controller; turbine protection remains in controller logic."),
    "CompressorSpeedFaceplate": _adapted("machine-speed", "Pairs the trainer PID speed loop and DEVCTL run controller; anti-surge and trip logic remain independent."),
    "AnalogFaceplate": _native("analog-input"),
    "AnalogOutputFaceplate": _native("analog-output"),
    "PulseInputFaceplate": _native("pulse-input"),
    "PIDFaceplate": _native("pid-loop"),
    "FLCFaceplate": _native("fuzzy-loop"),
    "DeviceFaceplate": _adapted(
        "device-control",
        "The trainer DEVCTL contract omits unavailable bypass, permit, "
        "simulation, accept, and delay states."),
    "MotorInterlockFaceplate": _adapted(
        "condition-control",
        "The trainer MOTOR_INTERLOCK contract exposes three real condition "
        "families in the shared condition-table shell."),
    "ATFaceplate": _adapted(
        "analog-tracking",
        "The trainer AT block exposes one tracking request and clamped result."),
    "ISELFaceplate": _native("input-selector"),
    "CTLSLFaceplate": _adapted(
        "enhanced-selector",
        "The trainer CTLSL block exposes three selector inputs."),
    "AVTRFaceplate": _native("analog-voter"),
    "DVTRFaceplate": _native("discrete-voter"),
    "SEQFaceplate": _native("step-sequence"),
    "STDFaceplate": _native("state-transition"),
    "LowSelectFaceplate": _adapted(
        "input-selector", "The trainer MIN_SELECT block reuses the selector shell."),
    "HighSelectFaceplate": _adapted(
        "input-selector", "The trainer MAX_SELECT block reuses the selector shell."),
    "SfcChartFaceplate": _adapted(
        "step-sequence", "The trainer SFC chart reuses the sequence shell."),
    "RampSoakFaceplate": _adapted(
        "enhanced-ramp", "The trainer block exposes reset instead of pause."),
    "CascadePairFaceplate": _adapted(
        "pid-loop", "Trainer-specific two-loop relationship view."),
    "PassBalanceFaceplate": _adapted(
        "analog-input", "Trainer-specific four-path comparison."),
    "RatioFaceplate": _adapted(
        "calculation", "Trainer-specific ratio calculation surface."),
    "TotalizerFaceplate": _adapted(
        "calculation", "Trainer-specific accumulation surface."),
}


__all__ = ["CONTRACTS", "FaceplateContract"]
