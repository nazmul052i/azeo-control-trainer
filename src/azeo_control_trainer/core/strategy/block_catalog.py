"""Authoritative Azeo primary function-block inventory.

The installed ``function_blocks.chm`` contains 97 primary catalog entries.
Historically Control Designer compared itself with an 88-entry transcription and
treated a palette search alias as an implementation.  That made a generic AI
look like a Fieldbus multiplexed input and an APC communications stub look like
MPCPro.  This module keeps the engineering claim honest and Qt-free:

``NATIVE``
    The public Azeo mnemonic is a registered block type.
``COMPATIBILITY_ALIAS``
    The implementation is the same trainer contract under a stable legacy
    ``block_type`` (for example RTO -> RATIO).
``SUBSTITUTE``
    A useful trainer block is offered in search, but it does not claim the
    complete device/product-specific Azeo contract.
``OUT_OF_SCOPE``
    Deliberately absent proprietary/licensed functionality.

The UI may re-export :data:`AZEO_MNEMONICS`, but the catalog itself must
never import Qt or the palette.  Runtime and tests can therefore audit the
same contract without loading an application.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping


PRIMARY_BLOCKS: Mapping[str, tuple[str, ...]] = {
    "I/O": (
        "ALARM_DET", "AI", "AO", "DI", "DO", "FFMDI", "FFMDI_STD",
        "FFMDO", "FFMDO_STD", "FFMAI_RMT", "FFMAO", "PIN", "SHDI",
        "SHDO", "TAGAI", "TAGAO", "TAGDI", "TAGDO", "TAGIO",
    ),
    "Analog Control": (
        "AT", "BG", "CALC", "CTLSL", "DEADTIME", "FLTR", "ECTLSL",
        "ERAMP", "ISEL", "FFISELX", "LL", "LIM", "MANLD", "PID",
        "RAMP", "RTLM", "RTO", "SCLR", "SGCR", "SGGN", "SGSL",
        "SPLTR",
    ),
    "Math": ("ABS", "ADD", "ARITH", "CMP", "DIV", "INT", "MLTY", "SUB"),
    "Timer/Counter": ("CTR", "DTE", "OFFD", "OND", "RET", "TP"),
    "Energy Metering": (
        "AGA_SI", "AGA_US", "ISE", "SST", "TSS", "SDR", "STM", "WTH",
        "WTS",
    ),
    "Logical": (
        "ACT", "AND", "BDE", "BFI", "BFO", "CND", "DC", "DCC", "EDC",
        "MLTX", "NDE", "NOT", "OR", "PDE", "RS", "SR", "XFR",
    ),
    "Advanced Control": (
        "DIAG", "FLC", "INSPECT", "LE", "MPC", "MPC_SIM", "MPCPro",
        "MPCPlus", "NN",
    ),
    "Advanced Function": ("AVTR", "CEM", "DVTR", "SEQ", "STD"),
    "SpectralPAT": ("SIMCAQ", "MVA"),
}

EXPECTED_FAMILY_COUNTS: Mapping[str, int] = {
    "I/O": 19,
    "Analog Control": 22,
    "Math": 8,
    "Timer/Counter": 6,
    "Energy Metering": 9,
    "Logical": 17,
    "Advanced Control": 9,
    "Advanced Function": 5,
    "SpectralPAT": 2,
}


# Stable trainer type names that implement the same core contract.  Renaming
# these classes would break shipped strategy JSON; the public mnemonic remains
# available in the palette and catalog instead.
COMPATIBILITY_ALIASES: Mapping[str, str] = {
    "RTO": "RATIO",
    "BG": "BIAS",
    "SCLR": "SCALER",
    "CALC": "EXPRESSION",
    "SPLTR": "SPLITTER",
    "FLTR": "FILTER",
    "LL": "LEAD_LAG",
    "LIM": "LIMITER",
    "RTLM": "RATE_LIMITER",
    "SGCR": "SIGNAL_CHAR",
    "ADD": "SUMMER",
    "CMP": "COMPARATOR",
    "DIV": "DIVIDER",
    "INT": "INTEGRATOR",
    "MLTY": "MULTIPLIER",
    "CTR": "COUNTER",
    "OFFD": "TIMER_OFF",
    "OND": "TIMER_ON",
    "TP": "PULSE",
    "PDE": "POS_EDGE",
    "NDE": "NEG_EDGE",
    "SR": "SR_LATCH",
    "RS": "RS_LATCH",
    "XFR": "TRANSFER",
    "MLTX": "MUX",
    "DC": "DEVCTL",
}


# These are intentionally search/display substitutes, not parity claims.  Full
# Fieldbus/HART device definitions and licensed Azeo APC products are outside
# the trainer's present product boundary.
SUBSTITUTES: Mapping[str, tuple[str, str]] = {
    "FFMAI_RMT": ("AI", "Multiplexed Fieldbus channel semantics are not modelled"),
    "FFMAO": ("AO", "Multiplexed Fieldbus channel semantics are not modelled"),
    "FFMDI": ("DI", "H1 carrier multi-channel semantics are not modelled"),
    "FFMDI_STD": ("DI", "Fieldbus multi-channel semantics are not modelled"),
    "FFMDO": ("DO", "H1 carrier multi-channel semantics are not modelled"),
    "FFMDO_STD": ("DO", "Fieldbus multi-channel semantics are not modelled"),
    "SHDI": ("DI", "Smart HART named-set/device semantics are not modelled"),
    "SHDO": ("DO", "Smart HART named-set/device semantics are not modelled"),
    "FFISELX": ("ISEL", "Fieldbus eight-input and device semantics are not modelled"),
    "MPC": ("DMC_CONTROLLER", "Trainer-specific DMC, not the Azeo MPC product"),
    "MPCPro": ("APC_CONTROL", "Supervisory APC interface, not Azeo MPCPro"),
    "MPCPlus": ("APC_CONTROL", "Supervisory APC interface, not Azeo MPCPlus"),
}


OUT_OF_SCOPE: Mapping[str, str] = {
    "MPC_SIM": "Azeo proprietary MPC model/simulator",
    "NN": "Azeo neural-network propagation/model format",
    "SIMCAQ": "Licensed SpectralPAT product block",
    "MVA": "Licensed SpectralPAT product block",
}


class CoverageKind(StrEnum):
    NATIVE = "native"
    COMPATIBILITY_ALIAS = "compatibility_alias"
    SUBSTITUTE = "substitute"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class CoverageRecord:
    family: str
    mnemonic: str
    kind: CoverageKind
    implementation: str | None
    note: str = ""


def primary_mnemonics() -> tuple[str, ...]:
    """Return all 97 primary mnemonics in CHM palette order."""
    return tuple(
        mnemonic
        for mnemonics in PRIMARY_BLOCKS.values()
        for mnemonic in mnemonics
    )


def coverage_records() -> tuple[CoverageRecord, ...]:
    """Describe the intended implementation status of every primary entry."""
    result: list[CoverageRecord] = []
    for family, mnemonics in PRIMARY_BLOCKS.items():
        for mnemonic in mnemonics:
            if mnemonic in OUT_OF_SCOPE:
                result.append(CoverageRecord(
                    family, mnemonic, CoverageKind.OUT_OF_SCOPE, None,
                    OUT_OF_SCOPE[mnemonic],
                ))
            elif mnemonic in SUBSTITUTES:
                target, note = SUBSTITUTES[mnemonic]
                result.append(CoverageRecord(
                    family, mnemonic, CoverageKind.SUBSTITUTE, target, note,
                ))
            elif mnemonic in COMPATIBILITY_ALIASES:
                result.append(CoverageRecord(
                    family, mnemonic, CoverageKind.COMPATIBILITY_ALIAS,
                    COMPATIBILITY_ALIASES[mnemonic],
                ))
            else:
                result.append(CoverageRecord(
                    family, mnemonic, CoverageKind.NATIVE, mnemonic,
                ))
    return tuple(result)


def audit_registered_types(registered: Iterable[str]) -> tuple[str, ...]:
    """Return catalog/registry contradictions; an empty tuple is success."""
    available = set(registered)
    problems: list[str] = []
    mnemonics = primary_mnemonics()
    if len(mnemonics) != 97 or len(set(mnemonics)) != 97:
        problems.append(
            f"Primary block catalog must contain 97 unique entries, got "
            f"{len(mnemonics)}/{len(set(mnemonics))}"
        )
    for family, expected in EXPECTED_FAMILY_COUNTS.items():
        actual = len(PRIMARY_BLOCKS.get(family, ()))
        if actual != expected:
            problems.append(f"{family}: expected {expected} entries, got {actual}")
    for record in coverage_records():
        if record.kind is CoverageKind.OUT_OF_SCOPE:
            if record.mnemonic in available:
                problems.append(
                    f"{record.mnemonic}: implemented but still marked out of scope"
                )
            continue
        if record.implementation not in available:
            problems.append(
                f"{record.mnemonic}: {record.kind.value} target "
                f"{record.implementation!r} is not registered"
            )
    return tuple(problems)


# Public search/display mapping retained for existing UI imports.  A substitute
# is deliberately included so engineers can find the nearest trainer tool, but
# its CoverageKind remains visible to audits and documentation.
AZEO_MNEMONICS: dict[str, str] = {
    record.mnemonic: record.implementation
    for record in coverage_records()
    if record.implementation is not None
}
