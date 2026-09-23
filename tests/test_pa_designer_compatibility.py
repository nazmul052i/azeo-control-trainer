"""Compatibility checks for PA Designer identity migrations."""

import base64
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azeo_control_trainer.core.pa_designer.core.integrity import (
    integrity_key_path,
    load_or_create_integrity_key,
)


def test_former_integrity_key_filename_is_migrated_without_changing_the_key(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("PA_DESIGNER_INTEGRITY_KEY", raising=False)
    monkeypatch.delenv("PROCEDURE_PILOT_INTEGRITY_KEY", raising=False)
    key = bytes(range(32))
    legacy = tmp_path / ".procedure_pilot_integrity.key"
    legacy.write_text(
        "plain:" + base64.urlsafe_b64encode(key).decode("ascii") + "\n",
        encoding="ascii",
    )

    assert load_or_create_integrity_key(tmp_path) == key
    assert integrity_key_path(tmp_path).is_file()
    assert load_or_create_integrity_key(tmp_path) == key
