"""Strict project-data contract for the Local Virtual-I/O runtime."""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from azeo_control_trainer.connectivity.fieldio.dynamic_provider import (  # noqa: E402
    ProviderConfigurationError,
)
from azeo_control_trainer.connectivity.fieldio.local_virtual_io import (  # noqa: E402
    LocalVirtualIoDriver,
    load_signal_routes,
)
from azeo_control_trainer.connectivity.fieldio.virtual_io_config import (  # noqa: E402
    validate_virtual_io_runtime_config,
)
from azeo_control_trainer.core.datastore.shared_data_store import (  # noqa: E402
    SharedDataStore,
)


PROJECT = REPO / "projects" / "AzeoPlantVirtualController"


def _project_config() -> dict:
    document = json.loads(
        (PROJECT / "_project.json").read_text(encoding="utf-8"))
    return document["areas"][0]["virtual_io"]


def _validated(config: dict | None = None):
    config = deepcopy(config or _project_config())
    routes = load_signal_routes(config, PROJECT)
    return validate_virtual_io_runtime_config(
        config,
        has_inputs=any(route.direction == "read" for route in routes.values()),
        has_outputs=any(route.direction == "write" for route in routes.values()),
        project_dir=PROJECT,
    )


def test_project_declares_every_runtime_communication_choice() -> None:
    config = _project_config()
    validated = _validated(config)
    assert validated.period_s == pytest.approx(0.1)
    assert validated.source == "APVC-CTRL-1"
    assert validated.claim_outputs is True
    assert validated.provider_manages_claim is True
    assert validated.input_stale_timeout_s == pytest.approx(2.0)
    assert "signals" not in config
    assert len(load_signal_routes(config, PROJECT)) == 612
    assert config["provider"]["call_style"] == "mapping"
    assert config["provider"]["options"] == {
        "source": "APVC-CTRL-1",
        "dt": 0.1,
        "snapshot": "${PROJECT_DIR}/virtual_io/lined_up.snapshot.json",
        "catalog": "${PROJECT_DIR}/virtual_io/opcua_tag_catalog.json",
        "stale_timeout_s": 2.0,
        "speed_factor": 1.0,
        "autorun": True,
        "open_loop": True,
    }


def test_startup_mode_defaults_to_automatic_and_accepts_manual() -> None:
    config = _project_config()
    config.pop("startup_mode", None)
    assert _validated(config).startup_mode == "automatic"

    config["startup_mode"] = " MANUAL "
    assert _validated(config).startup_mode == "manual"


@pytest.mark.parametrize("value", ["", "always", None, True, 1])
def test_startup_mode_rejects_unknown_or_non_string_values(value) -> None:
    config = _project_config()
    config["startup_mode"] = value
    with pytest.raises(ProviderConfigurationError, match="startup_mode"):
        _validated(config)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_period_must_be_positive_finite_number(value) -> None:
    config = _project_config()
    config["period_ms"] = value
    with pytest.raises(ProviderConfigurationError, match="period_ms.*positive"):
        _validated(config)


def test_exchange_period_duplicate_must_agree() -> None:
    config = _project_config()
    config["timing"]["exchange_period_ms"] = 250
    with pytest.raises(ProviderConfigurationError, match="period_ms.*match"):
        _validated(config)


def test_integration_step_duplicates_must_agree() -> None:
    config = _project_config()
    config["provider"]["options"]["dt"] = 0.2
    with pytest.raises(ProviderConfigurationError, match="dt.*match"):
        _validated(config)


@pytest.mark.parametrize("field", ["claim_outputs", "provider_manages_claim"])
def test_claim_flags_reject_truthy_strings(field: str) -> None:
    config = _project_config()
    config[field] = "false"
    with pytest.raises(ProviderConfigurationError, match=f"{field}.*boolean"):
        _validated(config)


def test_provider_boolean_rejects_truthy_string() -> None:
    config = _project_config()
    config["provider"]["options"]["autorun"] = "false"
    with pytest.raises(ProviderConfigurationError, match="autorun.*boolean"):
        _validated(config)


def test_provider_and_driver_source_must_match() -> None:
    config = _project_config()
    config["provider"]["options"]["source"] = "ANOTHER-CONTROLLER"
    with pytest.raises(ProviderConfigurationError, match="source must match"):
        _validated(config)


def test_controller_input_freshness_must_be_explicit() -> None:
    config = _project_config()
    del config["input_stale_timeout_s"]
    with pytest.raises(ProviderConfigurationError,
                       match="input_stale_timeout_s is required"):
        _validated(config)


@pytest.mark.parametrize("field", ["catalog", "snapshot"])
def test_provider_and_driver_artifact_paths_must_match(field: str) -> None:
    config = _project_config()
    config["provider"]["options"][field] = f"elsewhere/{field}.json"
    with pytest.raises(ProviderConfigurationError, match=f"{field}.*match"):
        _validated(config)


def test_provider_managed_claim_requires_provider_source() -> None:
    config = _project_config()
    del config["provider"]["options"]["source"]
    with pytest.raises(ProviderConfigurationError,
                       match="provider.options.source is required"):
        _validated(config)


@pytest.mark.parametrize("claim,managed", [(False, True), (False, False)])
def test_output_routes_require_a_coherent_claim(claim: bool,
                                                managed: bool) -> None:
    config = _project_config()
    config["claim_outputs"] = claim
    config["provider_manages_claim"] = managed
    with pytest.raises(ProviderConfigurationError, match="claim_outputs=true"):
        _validated(config)


def test_nonlocal_transport_is_rejected_before_provider_import() -> None:
    config = _project_config()
    config["transport"] = "opcua"
    with pytest.raises(ProviderConfigurationError, match="in_process"):
        LocalVirtualIoDriver(SharedDataStore(), config, PROJECT)


def test_signal_stale_timeout_must_be_positive_and_finite(tmp_path) -> None:
    with pytest.raises(ProviderConfigurationError,
                       match="stale_timeout_s.*positive"):
        load_signal_routes({
            "signals": {
                "PV": {
                    "signal": "PV", "direction": "read",
                    "stale_timeout_s": float("nan"),
                },
            },
        }, tmp_path)


def test_catalog_duplicate_store_tag_is_rejected(tmp_path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"tags": [
        {"name": "PV", "kind": "AI"},
        {"name": "PV", "kind": "AI"},
    ]}), encoding="utf-8")
    with pytest.raises(ProviderConfigurationError,
                       match="duplicate store tag 'PV'"):
        load_signal_routes({"catalog": "catalog.json"}, tmp_path)


def test_kind_direction_contradiction_is_rejected(tmp_path) -> None:
    with pytest.raises(ProviderConfigurationError, match="contradicts"):
        load_signal_routes({"signals": {
            "PV": {"signal": "PV", "kind": "AO", "direction": "read"},
        }}, tmp_path)


def test_duplicate_provider_signal_route_is_rejected(tmp_path) -> None:
    with pytest.raises(ProviderConfigurationError,
                       match="routed by both"):
        load_signal_routes({"signals": {
            "PV.A": {"signal": "PV", "direction": "read"},
            "PV.B": {"signal": "PV", "direction": "read"},
        }}, tmp_path)


def test_sparse_override_cannot_drift_from_catalog_kind(tmp_path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"tags": [
        {"name": "PV", "kind": "AI"},
    ]}), encoding="utf-8")
    with pytest.raises(ProviderConfigurationError, match="kind drifts"):
        load_signal_routes({
            "catalog": "catalog.json",
            "signals": {
                "PV": {"signal": "PV-ALIAS", "kind": "DI",
                       "direction": "read"},
            },
        }, tmp_path)


def test_top_level_freshness_is_inherited_by_each_input(tmp_path) -> None:
    config = {
        "type": "local_virtual_io",
        "transport": "in_process",
        "source": "CTRL",
        "period_ms": 100,
        "input_stale_timeout_s": 3.5,
        "claim_outputs": False,
        "provider_manages_claim": False,
        "provider": {"factory": "unused:create"},
        "signals": {
            "PV": {"signal": "PV", "kind": "AI", "direction": "read"},
            "FAST": {"signal": "FAST", "kind": "DI", "direction": "read",
                     "stale_timeout_s": 0.25},
        },
    }
    driver = LocalVirtualIoDriver(SharedDataStore(), config, tmp_path)
    assert driver.routes["PV"].stale_timeout_s == pytest.approx(3.5)
    assert driver.routes["FAST"].stale_timeout_s == pytest.approx(0.25)
