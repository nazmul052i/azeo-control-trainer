"""Evaluation licences: signed, windowed, clock-checked, and absent means unrestricted."""
from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from azeo_control_trainer.config import licensing  # noqa: E402
from azeo_control_trainer.core.presentation import headless, license_gate  # noqa: E402

KEY_ID = "test-key"
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def signer(monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(licensing, "PUBLIC_KEYS", {KEY_ID: key.public_key().public_bytes_raw().hex()})
    return key.private_bytes_raw()


def body(**overrides):
    base = {"id": "AZEO-EVAL-TEST", "product": licensing.PRODUCT, "edition": "evaluation",
            "licensee": "Test Site", "issued": "2026-09-15", "not_before": "2026-09-15",
            "not_after": "2026-09-21", "duration_days": 7, "version": ""}
    base.update(overrides)
    return base


def write(tmp_path, document):
    path = tmp_path / "azeo.lic"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_signed_licence_verifies_and_any_edit_is_refused(signer, tmp_path):
    document = licensing.sign_document(body(), signer, KEY_ID)
    licence = licensing.load_license(write(tmp_path, document))
    assert licence.id == "AZEO-EVAL-TEST" and licence.duration_days == 7
    forged = json.loads(json.dumps(document))
    forged["license"]["not_after"] = "2030-01-01"
    with pytest.raises(licensing.LicenseError, match="signature"):
        licensing.load_license(write(tmp_path, forged))
    with pytest.raises(licensing.LicenseError, match="not trusted"):
        licensing.parse_document(document, {"other": licensing.PUBLIC_KEYS[KEY_ID]})
    other = licensing.sign_document(body(product="something-else"), signer, KEY_ID)
    with pytest.raises(licensing.LicenseError, match="is for"):
        licensing.parse_document(other)


def test_window_runs_from_first_launch_and_is_capped(signer, tmp_path):
    licence = licensing.parse_document(licensing.sign_document(body(), signer, KEY_ID))
    state = tmp_path / "state.json"
    first = licensing.evaluate(licence, now=NOW, state_file=state)
    assert first.valid and first.days_left == 7 and state.exists()
    # Seven days from a midday first launch would end at midday on the 22nd,
    # but not_after (the 21st, inclusive) caps the window at 00:00 UTC that day.
    later = licensing.evaluate(licence, now=NOW + timedelta(days=6, hours=11), state_file=state)
    assert later.valid and later.days_left == 1
    assert later.expires == datetime(2026, 9, 22, tzinfo=timezone.utc)
    expired = licensing.evaluate(licence, now=NOW + timedelta(days=6, hours=12, minutes=1), state_file=state)
    assert not expired.valid and "ended" in expired.reason
    # A first launch late in the distribution window is capped by not_after.
    late_state = tmp_path / "late.json"
    late = licensing.evaluate(licence, now=NOW + timedelta(days=5), state_file=late_state)
    assert late.valid and late.days_left == 2
    assert late.expires == datetime(2026, 9, 22, tzinfo=timezone.utc)


def test_clock_rollback_and_early_use_are_refused(signer, tmp_path):
    licence = licensing.parse_document(licensing.sign_document(body(), signer, KEY_ID))
    state = tmp_path / "state.json"
    assert licensing.evaluate(licence, now=NOW + timedelta(days=2), state_file=state).valid
    back = licensing.evaluate(licence, now=NOW + timedelta(days=1), state_file=state)
    assert not back.valid and "clock" in back.reason
    assert licensing.evaluate(licence, now=NOW + timedelta(days=2, minutes=5), state_file=state).valid
    early = licensing.evaluate(licence, now=NOW - timedelta(days=3), state_file=tmp_path / "early.json")
    assert not early.valid and "not valid before" in early.reason


def test_tampered_state_resets_but_cannot_extend_past_the_cap(signer, tmp_path):
    licence = licensing.parse_document(licensing.sign_document(body(), signer, KEY_ID))
    state = tmp_path / "state.json"
    licensing.evaluate(licence, now=NOW, state_file=state)
    edited = json.loads(state.read_text(encoding="utf-8"))
    edited["first_launch"] = (NOW + timedelta(days=30)).isoformat()
    state.write_text(json.dumps(edited), encoding="utf-8")
    status = licensing.evaluate(licence, now=NOW + timedelta(days=6), state_file=state)
    assert status.valid and status.days_left == 1        # reset to now, capped by not_after
    state.unlink()
    assert not licensing.evaluate(licence, now=NOW + timedelta(days=8), state_file=state).valid


def test_version_bound_licence_only_runs_its_version(signer, tmp_path):
    licence = licensing.parse_document(licensing.sign_document(body(version="0.3.0"), signer, KEY_ID))
    state = tmp_path / "state.json"
    assert licensing.evaluate(licence, now=NOW, state_file=state, installed_version="0.3.0").valid
    other = licensing.evaluate(licence, now=NOW, state_file=state, installed_version="0.3.1")
    assert not other.valid and "version" in other.reason
    assert licensing.evaluate(licence, now=NOW, state_file=state, installed_version=None).valid


def test_missing_licence_file_means_unrestricted_and_gate_exit_code(signer, tmp_path, monkeypatch):
    monkeypatch.setenv("AZEO_LICENSE_FILE", str(tmp_path / "absent.lic"))
    monkeypatch.setenv("AZEO_LICENSE_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(headless, "is_headless", lambda: True)
    monkeypatch.setattr(license_gate, "is_headless", lambda: True)
    status = licensing.check()
    assert not status.required and status.valid
    assert license_gate.enforce_license("Azeo Explorer") is None
    valid = licensing.sign_document(body(issued="2026-09-15", not_after="2099-01-01"), signer, KEY_ID)
    monkeypatch.setenv("AZEO_LICENSE_FILE", str(write(tmp_path, valid)))
    assert license_gate.enforce_license("Azeo Explorer") is None
    expired = licensing.sign_document(body(id="OLD", issued="2020-01-01", not_before="2020-01-01",
                                           not_after="2020-01-07"), signer, KEY_ID)
    monkeypatch.setenv("AZEO_LICENSE_FILE", str(write(tmp_path, expired)))
    assert license_gate.enforce_license("Azeo Explorer") == licensing.EXIT_LICENSE_REFUSED
    (tmp_path / "azeo.lic").write_text("{not json", encoding="utf-8")
    assert license_gate.enforce_license("Azeo Explorer") == licensing.EXIT_LICENSE_REFUSED
    # A packaged check or scripted run declares itself unattended: the refusal is
    # logged and returned, never shown as a dialog that would block the run.
    monkeypatch.setattr(license_gate, "is_headless", lambda: False)
    monkeypatch.setenv("AZEO_UNATTENDED", "1")
    assert license_gate.enforce_license("Azeo Explorer") == licensing.EXIT_LICENSE_REFUSED


def test_release_tool_round_trip(tmp_path, monkeypatch):
    import license_tool
    monkeypatch.setattr(license_tool, "KEY_DIRECTORY", tmp_path / "keys")
    monkeypatch.setattr(licensing, "PUBLIC_KEYS", {})
    module = tmp_path / "licensing.py"
    module.write_text("# BEGIN PUBLIC KEYS (managed)\nPUBLIC_KEYS: dict[str, str] = {}\n# END PUBLIC KEYS\n",
                      encoding="utf-8")
    monkeypatch.setattr(license_tool, "install_public_key",
                        lambda key_id, public: licensing.PUBLIC_KEYS.__setitem__(key_id, public) or module)
    assert license_tool.main(["keygen", "--key-id", "unit", "--install"]) == 0
    out = tmp_path / "azeo.lic"
    assert license_tool.main(["issue", "--licensee", "Unit", "--key-id", "unit", "--days", "7",
                              "--distribution-days", "3", "--version", "0.3.0", "--out", str(out)]) == 0
    licence = licensing.load_license(out)
    assert licence.duration_days == 7 and licence.version == "0.3.0"
    assert (licence.not_after - licence.issued).days == 9
    assert license_tool.main(["inspect", str(out)]) == 0
    # A patch release reuses the id so the workstation's first-launch record still applies.
    patched = tmp_path / "patched.lic"
    assert license_tool.main(["issue", "--licensee", "Unit", "--key-id", "unit", "--id", licence.id,
                              "--issued", licence.issued.isoformat(), "--days", "7", "--distribution-days", "3",
                              "--version", "0.3.1", "--out", str(patched)]) == 0
    successor = licensing.load_license(patched)
    assert successor.id == licence.id and successor.version == "0.3.1"
    assert successor.not_after == licence.not_after
