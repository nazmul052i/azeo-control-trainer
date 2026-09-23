"""Provider-neutral signal simulation profiles for Local Virtual I/O.

The waveform belongs to the I/O link, not to a particular plant package.
Providers opt in by exposing ``set_simulated_input`` and
``clear_simulated_input``; Explorer therefore never imports a simulator.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


SIMULATION_MODES = ("static", "sawtooth", "square", "sine")
SIMULATION_QUALITIES = ("GOOD", "UNCERTAIN", "BAD")


@dataclass(frozen=True)
class SignalSimulation:
    """One validated input override, evaluated against elapsed seconds."""

    store_tag: str
    mode: str = "static"
    low: float = 0.0
    high: float = 100.0
    period_s: float = 10.0
    value: float | bool = 0.0
    quality: str = "GOOD"
    started_at: float = 0.0

    def __post_init__(self) -> None:
        mode = str(self.mode).strip().lower()
        quality = str(self.quality).strip().upper()
        if not self.store_tag.strip():
            raise ValueError("a simulated signal needs a store tag")
        if mode not in SIMULATION_MODES:
            raise ValueError(f"unsupported simulation mode {self.mode!r}")
        if quality not in SIMULATION_QUALITIES:
            raise ValueError(f"unsupported signal quality {self.quality!r}")
        if not math.isfinite(float(self.low)) or not math.isfinite(
                float(self.high)):
            raise ValueError("simulation limits must be finite")
        if float(self.high) < float(self.low):
            raise ValueError("simulation high limit must not be below low")
        if not math.isfinite(float(self.period_s)) or self.period_s <= 0.0:
            raise ValueError("simulation period must be positive and finite")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "quality", quality)

    def sample(self, now: float, *, discrete: bool = False) -> float | bool:
        """Evaluate this profile without retaining mutable timer state."""
        if self.mode == "static":
            return bool(self.value) if discrete else float(self.value)
        phase = ((now - self.started_at) / self.period_s) % 1.0
        if self.mode == "square":
            return phase >= 0.5 if discrete else (
                self.high if phase >= 0.5 else self.low)
        if discrete:
            # Discrete hardware has no intermediate state.  Non-square
            # patterns remain deterministic threshold patterns instead of
            # pretending a Boolean channel carries an analogue value.
            return phase >= 0.5
        if self.mode == "sawtooth":
            return self.low + (self.high - self.low) * phase
        centre = (self.low + self.high) / 2.0
        amplitude = (self.high - self.low) / 2.0
        return centre + amplitude * math.sin(phase * math.tau)

    def document(self) -> dict[str, Any]:
        result = asdict(self)
        # A loaded scenario begins a fresh repeatable pattern; persisting a
        # process-local monotonic timestamp would shift it unpredictably.
        result.pop("started_at", None)
        return result

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, started_at: float = 0.0
                     ) -> "SignalSimulation":
        return cls(
            store_tag=str(raw.get("store_tag") or ""),
            mode=str(raw.get("mode") or "static"),
            low=float(raw.get("low", 0.0)),
            high=float(raw.get("high", 100.0)),
            period_s=float(raw.get("period_s", 10.0)),
            value=raw.get("value", 0.0),
            quality=str(raw.get("quality") or "GOOD"),
            started_at=float(started_at),
        )


def write_profile(path: Path | str,
                  profiles: Mapping[str, SignalSimulation]) -> Path:
    """Atomically write a repeatable Virtual I/O scenario."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "type": "azeo.virtual_io_scenario",
        "signals": [profiles[name].document() for name in sorted(profiles)],
    }
    fd, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def read_profile(path: Path | str, *, started_at: float = 0.0
                 ) -> dict[str, SignalSimulation]:
    """Read and fully validate a scenario before its caller applies it."""
    source = Path(path).expanduser().resolve()
    document = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ValueError("Virtual I/O scenario must be a JSON object")
    if document.get("type") != "azeo.virtual_io_scenario":
        raise ValueError("file is not an Azeo Virtual I/O scenario")
    if int(document.get("schema_version", 0)) != 1:
        raise ValueError("unsupported Virtual I/O scenario schema")
    rows = document.get("signals")
    if not isinstance(rows, list):
        raise ValueError("Virtual I/O scenario signals must be a list")
    profiles: dict[str, SignalSimulation] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("each Virtual I/O scenario signal must be an object")
        profile = SignalSimulation.from_mapping(raw, started_at=started_at)
        if profile.store_tag in profiles:
            raise ValueError(
                f"duplicate simulated signal {profile.store_tag!r}")
        profiles[profile.store_tag] = profile
    return profiles


__all__ = [
    "SIMULATION_MODES", "SIMULATION_QUALITIES", "SignalSimulation",
    "read_profile", "write_profile",
]
