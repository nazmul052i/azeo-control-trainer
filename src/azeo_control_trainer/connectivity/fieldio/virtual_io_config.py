"""Strict runtime configuration contract for Local Virtual I/O.

Project metadata is the authority for communication identity, cadence and
ownership.  This validator deliberately rejects contradictory duplicate
declarations instead of choosing whichever value one code path happens to
read; an Explorer summary that differs from the running provider is an
engineering fault, not a convenience default.
"""
from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from .dynamic_provider import ProviderConfigurationError


@dataclass(frozen=True)
class ValidatedVirtualIoConfig:
    """The communication values consumed directly by the local driver."""

    startup_mode: str
    period_s: float
    source: str
    claim_outputs: bool
    provider_manages_claim: bool
    input_stale_timeout_s: float | None


def positive_finite(value: Any, field: str) -> float:
    """Return one positive finite number without accepting bool as 0/1."""
    if isinstance(value, bool):
        raise ProviderConfigurationError(
            f"{field} must be a positive finite number, not a boolean")
    try:
        answer = float(value)
    except (TypeError, ValueError) as error:
        raise ProviderConfigurationError(
            f"{field} must be a positive finite number") from error
    if not math.isfinite(answer) or answer <= 0.0:
        raise ProviderConfigurationError(
            f"{field} must be a positive finite number")
    return answer


def _required_boolean(config: Mapping[str, Any], field: str) -> bool:
    if field not in config:
        raise ProviderConfigurationError(
            f"local_virtual_io.{field} must be explicitly true or false")
    value = config[field]
    if type(value) is not bool:  # noqa: E721 - reject truthy strings/numbers
        raise ProviderConfigurationError(
            f"local_virtual_io.{field} must be a JSON boolean")
    return value


def _optional_boolean(config: Mapping[str, Any], field: str) -> None:
    if field in config and type(config[field]) is not bool:  # noqa: E721
        raise ProviderConfigurationError(
            f"provider.options.{field} must be a JSON boolean")


def _same(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)


def _agree(field_a: str, value_a: float, field_b: str, value_b: float) -> None:
    if not _same(value_a, value_b):
        raise ProviderConfigurationError(
            f"{field_a} ({value_a:g}) must match {field_b} ({value_b:g})")


def _path_value(value: Any, field: str) -> str:
    if isinstance(value, Mapping):
        value = value.get("path")
    answer = str(value or "").strip()
    if not answer:
        raise ProviderConfigurationError(f"{field} path is required")
    return answer


def _resolved_path(value: Any, field: str, project_dir: Path) -> Path:
    raw = _path_value(value, field)
    variables = {
        "PROJECT_DIR": str(project_dir),
        "AREA_DIR": str(project_dir),
        "CONFIG_DIR": str(project_dir),
    }
    expanded = os.path.expandvars(Template(raw).safe_substitute(variables))
    path = Path(expanded)
    return (path if path.is_absolute() else project_dir / path).resolve()


def _agree_path(config: Mapping[str, Any], options: Mapping[str, Any],
                field: str, project_dir: Path | None) -> None:
    top_present = field in config and config.get(field) not in (None, "")
    provider_present = field in options and options.get(field) not in (None, "")
    if top_present != provider_present:
        raise ProviderConfigurationError(
            f"local_virtual_io.{field} and provider.options.{field} must "
            "both declare the same project artifact")
    if not top_present:
        return
    if project_dir is None:
        return
    top = _resolved_path(config[field], f"local_virtual_io.{field}", project_dir)
    provider = _resolved_path(
        options[field], f"provider.options.{field}", project_dir)
    if top != provider:
        raise ProviderConfigurationError(
            f"local_virtual_io.{field} ({top}) must match "
            f"provider.options.{field} ({provider})")


def validate_virtual_io_runtime_config(
    config: Mapping[str, Any], *, has_outputs: bool, has_inputs: bool = False,
    project_dir: Path | None = None,
) -> ValidatedVirtualIoConfig:
    """Validate communication configuration before importing its provider."""
    raw_startup_mode = config.get("startup_mode", "automatic")
    if not isinstance(raw_startup_mode, str):
        raise ProviderConfigurationError(
            "local_virtual_io.startup_mode must be 'manual' or 'automatic'")
    startup_mode = raw_startup_mode.strip().lower()
    if startup_mode not in {"manual", "automatic"}:
        raise ProviderConfigurationError(
            "local_virtual_io.startup_mode must be 'manual' or 'automatic'")

    transport = config.get("transport")
    if transport is not None and str(transport).strip().lower() != "in_process":
        raise ProviderConfigurationError(
            "local_virtual_io.transport must be 'in_process'")

    source = str(config.get("source") or "").strip()
    if not source:
        raise ProviderConfigurationError(
            "local_virtual_io.source is required for output arbitration")

    if "period_ms" not in config:
        raise ProviderConfigurationError(
            "local_virtual_io.period_ms is required; exchange cadence must "
            "come from project configuration")
    period_ms = positive_finite(
        config["period_ms"], "local_virtual_io.period_ms")

    claim_outputs = _required_boolean(config, "claim_outputs")
    provider_manages = _required_boolean(config, "provider_manages_claim")
    if provider_manages and not claim_outputs:
        raise ProviderConfigurationError(
            "provider_manages_claim=true requires claim_outputs=true")
    if has_outputs and not claim_outputs:
        raise ProviderConfigurationError(
            "configured output routes require claim_outputs=true")

    provider = config.get("provider") or {}
    if not isinstance(provider, Mapping):
        raise ProviderConfigurationError(
            "local_virtual_io.provider must be an object")
    options = provider.get("options") or {}
    if not isinstance(options, Mapping):
        raise ProviderConfigurationError(
            "provider.options must be an object")

    provider_source = str(options.get("source") or "").strip()
    if provider_source and provider_source != source:
        raise ProviderConfigurationError(
            "local_virtual_io.source must match provider.options.source "
            f"({source!r} != {provider_source!r})")
    if provider_manages and not provider_source:
        raise ProviderConfigurationError(
            "provider.options.source is required when the provider manages "
            "the output claim")

    _agree_path(config, options, "catalog", project_dir)
    _agree_path(config, options, "snapshot", project_dir)

    input_stale_timeout_s = None
    if "input_stale_timeout_s" in config:
        input_stale_timeout_s = positive_finite(
            config["input_stale_timeout_s"],
            "local_virtual_io.input_stale_timeout_s",
        )
    elif has_inputs:
        raise ProviderConfigurationError(
            "local_virtual_io.input_stale_timeout_s is required when input "
            "routes are configured")

    top_dt = None
    if "dt" in config:
        top_dt = positive_finite(config["dt"], "local_virtual_io.dt")
    provider_dt = None
    if "dt" in options:
        provider_dt = positive_finite(options["dt"], "provider.options.dt")
    if top_dt is not None and provider_dt is not None:
        _agree("local_virtual_io.dt", top_dt,
               "provider.options.dt", provider_dt)

    timing = config.get("timing") or {}
    if not isinstance(timing, Mapping):
        raise ProviderConfigurationError(
            "local_virtual_io.timing must be an object")
    numeric_timing: dict[str, float] = {}
    for field in (
        "integration_step_s", "exchange_period_ms",
        "regulatory_scan_rate_ms",
    ):
        if field in timing:
            numeric_timing[field] = positive_finite(
                timing[field], f"local_virtual_io.timing.{field}")
    exchange_ms = numeric_timing.get("exchange_period_ms")
    if exchange_ms is not None:
        _agree("local_virtual_io.period_ms", period_ms,
               "local_virtual_io.timing.exchange_period_ms", exchange_ms)
    integration_s = numeric_timing.get("integration_step_s")
    if integration_s is not None and top_dt is not None:
        _agree("local_virtual_io.dt", top_dt,
               "local_virtual_io.timing.integration_step_s", integration_s)
    if integration_s is not None and provider_dt is not None:
        _agree("provider.options.dt", provider_dt,
               "local_virtual_io.timing.integration_step_s", integration_s)

    if "sequence" in timing:
        raise ProviderConfigurationError(
            "local_virtual_io.timing.sequence is not an executable runtime "
            "contract; use scheduling and write_guarantee")
    scheduling = timing.get("scheduling")
    if scheduling is not None and scheduling != \
            "independent_provider_and_controller_workers":
        raise ProviderConfigurationError(
            "local_virtual_io.timing.scheduling must describe the actual "
            "independent provider and controller workers")
    write_guarantee = timing.get("write_guarantee")
    if write_guarantee is not None and write_guarantee != \
            "drain_queued_writes_between_plant_steps":
        raise ProviderConfigurationError(
            "local_virtual_io.timing.write_guarantee must be "
            "'drain_queued_writes_between_plant_steps'")

    for field in ("stale_timeout_s", "speed_factor"):
        if field in options:
            positive_finite(options[field], f"provider.options.{field}")
    for field in ("autorun", "open_loop"):
        _optional_boolean(options, field)

    return ValidatedVirtualIoConfig(
        startup_mode=startup_mode,
        period_s=period_ms / 1000.0,
        source=source,
        claim_outputs=claim_outputs,
        provider_manages_claim=provider_manages,
        input_stale_timeout_s=input_stale_timeout_s,
    )


__all__ = [
    "ValidatedVirtualIoConfig",
    "positive_finite",
    "validate_virtual_io_runtime_config",
]
