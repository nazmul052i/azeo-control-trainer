"""Model-accountability primitives.

The process models intentionally exchange a small number of values through a
shared bus.  A plain ``dict`` is convenient, but it cannot distinguish a new
stream from a misspelling, identify who owns a value, or say whether a
backwards dependency is the deliberate recycle tear.  ``ProcessBus`` keeps the
mapping API the models already use while making that contract executable.

This module also holds the neutral records used for material-balance and model
parameter reporting.  They are diagnostics only: none of them closes a loop or
changes process behaviour.
"""

from __future__ import annotations

import math
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, Iterator, Mapping, Optional, Sequence


class BusContractError(RuntimeError):
    """A process-bus access violated the declared model contract."""


@dataclass(frozen=True)
class BusSignalSpec:
    """One cross-unit value and its ownership/topology contract."""

    name: str
    default: float
    eu: str
    producer: str
    consumers: tuple[str, ...] = ()
    lo: Optional[float] = None
    hi: Optional[float] = None
    tear: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("bus signal name cannot be empty")
        if not self.producer:
            raise ValueError(f"{self.name}: bus producer cannot be empty")
        if not math.isfinite(float(self.default)):
            raise ValueError(f"{self.name}: default must be finite")
        if self.lo is not None and not math.isfinite(float(self.lo)):
            raise ValueError(f"{self.name}: low limit must be finite")
        if self.hi is not None and not math.isfinite(float(self.hi)):
            raise ValueError(f"{self.name}: high limit must be finite")
        if (self.lo is not None and self.hi is not None
                and float(self.lo) >= float(self.hi)):
            raise ValueError(f"{self.name}: low limit must be below high limit")


@dataclass
class ProcessBusStats:
    reads: int = 0
    writes: int = 0
    range_excursions: int = 0
    producer_violations: int = 0
    consumer_violations: int = 0
    unknown_signals: int = 0
    rejected_nonfinite: int = 0
    last_violation: str = ""
    excursions_by_signal: Dict[str, int] = field(default_factory=dict)


class ProcessBus(dict):
    """A schema-checked mapping used for process-unit handoffs.

    Accesses are attributed only while the flowsheet executive has installed a
    source scope.  Direct unit tests and snapshot migration code may still use
    the mapping without pretending to be a process unit, but names and finite
    values are validated at every doorway.

    Expected ranges are diagnostic rather than clamps.  The physics is allowed
    to show an excursion; the accountability report records it.
    """

    SYSTEM_SOURCES = frozenset({"SNAPSHOT", "INITIAL"})

    def __init__(self, specs: Iterable[BusSignalSpec]) -> None:
        self._specs: Dict[str, BusSignalSpec] = {}
        self._scope = threading.local()
        self.stats = ProcessBusStats()
        for spec in specs:
            if spec.name in self._specs:
                raise ValueError(f"duplicate bus signal {spec.name!r}")
            self._specs[spec.name] = spec
        dict.__init__(self)
        with self.source("INITIAL"):
            for spec in self._specs.values():
                self[spec.name] = spec.default
        # Construction is not runtime activity.
        self.stats = ProcessBusStats()

    @property
    def specs(self) -> Mapping[str, BusSignalSpec]:
        return self._specs

    @property
    def active_source(self) -> Optional[str]:
        return getattr(self._scope, "source", None)

    @contextmanager
    def source(self, name: str) -> Iterator[None]:
        previous = self.active_source
        self._scope.source = str(name)
        try:
            yield
        finally:
            self._scope.source = previous

    def _unknown(self, name: str) -> BusContractError:
        self.stats.unknown_signals += 1
        self.stats.last_violation = f"undeclared process-bus signal {name!r}"
        return BusContractError(self.stats.last_violation)

    def _check_consumer(self, name: str, spec: BusSignalSpec) -> None:
        source = self.active_source
        if (source is None or source in self.SYSTEM_SOURCES
                or source == spec.producer or source in spec.consumers):
            return
        self.stats.consumer_violations += 1
        self.stats.last_violation = (
            f"{source} read {name!r}, declared consumers are "
            f"{', '.join(spec.consumers) or 'none'}"
        )
        raise BusContractError(self.stats.last_violation)

    def __getitem__(self, name: str) -> float:
        spec = self._specs.get(name)
        if spec is None:
            raise self._unknown(name)
        self._check_consumer(name, spec)
        self.stats.reads += 1
        return dict.__getitem__(self, name)

    def get(self, name: str, default=None):
        spec = self._specs.get(name)
        if spec is None:
            raise self._unknown(name)
        self._check_consumer(name, spec)
        self.stats.reads += 1
        return dict.__getitem__(self, name)

    def __setitem__(self, name: str, value) -> None:
        spec = self._specs.get(name)
        if spec is None:
            raise self._unknown(name)
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            self.stats.last_violation = f"{name}: non-numeric bus value {value!r}"
            raise BusContractError(self.stats.last_violation) from exc
        if not math.isfinite(numeric):
            self.stats.rejected_nonfinite += 1
            self.stats.last_violation = f"{name}: non-finite bus value rejected"
            raise BusContractError(self.stats.last_violation)

        source = self.active_source
        if (source is not None and source not in self.SYSTEM_SOURCES
                and source != spec.producer):
            self.stats.producer_violations += 1
            self.stats.last_violation = (
                f"{source} wrote {name!r}, owned by {spec.producer}"
            )
            raise BusContractError(self.stats.last_violation)

        if ((spec.lo is not None and numeric < spec.lo)
                or (spec.hi is not None and numeric > spec.hi)):
            self.stats.range_excursions += 1
            self.stats.excursions_by_signal[name] = (
                self.stats.excursions_by_signal.get(name, 0) + 1
            )
        dict.__setitem__(self, name, numeric)
        self.stats.writes += 1

    def restore(self, name: str, value) -> None:
        """Validated snapshot write that deliberately bypasses ownership."""
        with self.source("SNAPSHOT"):
            self[name] = value

    def current_range_issues(self) -> list[str]:
        issues = []
        for name, spec in self._specs.items():
            value = dict.__getitem__(self, name)
            if spec.lo is not None and value < spec.lo:
                issues.append(f"{name}={value:g} below {spec.lo:g} {spec.eu}".strip())
            if spec.hi is not None and value > spec.hi:
                issues.append(f"{name}={value:g} above {spec.hi:g} {spec.eu}".strip())
        return issues

    def validate_topology(self, unit_order: Sequence[str]) -> list[str]:
        """Validate owners, consumers and deliberate backwards dependencies."""
        errors: list[str] = []
        positions = {code: i for i, code in enumerate(unit_order)}
        allowed_producers = set(positions) | {"FLOWSHEET", "ENVIRONMENT"}
        for spec in self._specs.values():
            if spec.producer not in allowed_producers:
                errors.append(f"{spec.name}: unknown producer {spec.producer}")
            for consumer in spec.consumers:
                if consumer not in positions:
                    errors.append(f"{spec.name}: unknown consumer {consumer}")
                    continue
                if (spec.producer in positions
                        and positions[spec.producer] > positions[consumer]
                        and not spec.tear):
                    errors.append(
                        f"{spec.name}: backwards dependency "
                        f"{spec.producer}->{consumer} is not declared as a tear"
                    )
            if (spec.lo is not None and spec.default < spec.lo) or (
                    spec.hi is not None and spec.default > spec.hi):
                errors.append(f"{spec.name}: default is outside expected range")
        return errors

    def contract_rows(self) -> list[dict]:
        return [asdict(spec) for spec in self._specs.values()]

    def stats_snapshot(self) -> ProcessBusStats:
        """Detached statistics suitable for a point-in-time report."""
        s = self.stats
        return ProcessBusStats(
            reads=s.reads,
            writes=s.writes,
            range_excursions=s.range_excursions,
            producer_violations=s.producer_violations,
            consumer_violations=s.consumer_violations,
            unknown_signals=s.unknown_signals,
            rejected_nonfinite=s.rejected_nonfinite,
            last_violation=s.last_violation,
            excursions_by_signal=dict(s.excursions_by_signal),
        )


@dataclass(frozen=True)
class BalanceReading:
    """Latest conservation residual for one dynamic inventory or split."""

    unit: str
    name: str
    inflow: float
    outflow: float
    accumulation: float
    residual: float
    tolerance: float
    eu: str
    kind: str = "material"

    @property
    def healthy(self) -> bool:
        return math.isfinite(self.residual) and abs(self.residual) <= self.tolerance


@dataclass(frozen=True)
class ParameterSpec:
    """A discoverable numeric model constant."""

    unit: str
    name: str
    value: float
    eu: str
    description: str
    source: str
    lo: Optional[float] = None
    hi: Optional[float] = None
    documented: bool = False

    @property
    def qualified_name(self) -> str:
        return f"{self.unit}.{self.name}"

    @property
    def healthy(self) -> bool:
        return (math.isfinite(self.value)
                and (self.lo is None or self.value >= self.lo)
                and (self.hi is None or self.value <= self.hi))


def discover_parameters(unit) -> list[ParameterSpec]:
    """Inventory effective uppercase numeric constants on a process unit.

    ``PARAMETER_META`` is intentionally optional so the registry can be added
    without a risky bulk rewrite.  Units can progressively attach structured
    units, limits, descriptions and provenance while every numeric constant is
    visible from day one.
    """
    values: Dict[str, tuple[float, type]] = {}
    metadata: Dict[str, dict] = {}
    for cls in reversed(type(unit).mro()):
        for name, value in vars(cls).items():
            if (name.isupper() and isinstance(value, (int, float))
                    and not isinstance(value, bool)):
                values[name] = (float(value), cls)
        own_meta = vars(cls).get("PARAMETER_META", {})
        if isinstance(own_meta, dict):
            metadata.update(own_meta)

    out = []
    for name, (value, owner) in sorted(values.items()):
        meta = metadata.get(name, {})
        out.append(ParameterSpec(
            unit=unit.code,
            name=name,
            value=value,
            eu=str(meta.get("eu", "")),
            description=str(meta.get("description", name.replace("_", " ").title())),
            source=str(meta.get("source", f"{owner.__module__}.{owner.__name__}")),
            lo=(float(meta["lo"]) if meta.get("lo") is not None else None),
            hi=(float(meta["hi"]) if meta.get("hi") is not None else None),
            documented=name in metadata,
        ))
    return out


@dataclass(frozen=True)
class AccountabilityReport:
    topology_errors: tuple[str, ...]
    current_range_issues: tuple[str, ...]
    balances: tuple[BalanceReading, ...]
    parameters: tuple[ParameterSpec, ...]
    bus_stats: ProcessBusStats

    @property
    def healthy(self) -> bool:
        return not (
            self.topology_errors
            or self.current_range_issues
            or self.bus_stats.producer_violations
            or self.bus_stats.consumer_violations
            or self.bus_stats.unknown_signals
            or self.bus_stats.rejected_nonfinite
            or any(not reading.healthy for reading in self.balances)
            or any(not parameter.documented or not parameter.healthy
                   for parameter in self.parameters)
        )

    @property
    def issues(self) -> list[str]:
        issues = list(self.topology_errors) + list(self.current_range_issues)
        issues.extend(
            f"{b.unit}.{b.name}: residual {b.residual:g} {b.eu} "
            f"exceeds {b.tolerance:g}"
            for b in self.balances if not b.healthy
        )
        issues.extend(
            f"{p.qualified_name}: value {p.value:g} outside declared limits"
            for p in self.parameters if not p.healthy
        )
        issues.extend(
            f"{p.qualified_name}: numeric model constant has no PARAMETER_META"
            for p in self.parameters if not p.documented
        )
        if self.bus_stats.last_violation:
            issues.append(self.bus_stats.last_violation)
        return issues
