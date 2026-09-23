"""Time-limited evaluation licences for installed and portable builds.

A licence is a small JSON document signed with Ed25519. The public keys in
``PUBLIC_KEYS`` are the only trust anchors; the matching private keys never
enter the repository (``tools/license_tool.py`` keeps them outside it). A
source checkout, and any build made without a licence file, is unrestricted:
development, tests and internal builds never see the gate. Every product entry
point enforces the licence through ``core/presentation/license_gate.py``; the
Help Center stays reachable so an expired evaluation can still read how to
obtain a licence.

Document shape, stored as ``license/azeo.lic`` beside ``installation.ini``::

    {"license": {"id": "...", "product": "azeo-control-trainer",
                 "edition": "evaluation", "licensee": "...",
                 "issued": "2026-09-15", "not_before": "2026-09-15",
                 "not_after": "2026-09-22", "duration_days": 7,
                 "version": "0.3.0"},
     "key_id": "azeo-eval-2026",
     "signature": "<hex Ed25519 signature over the canonical body>"}

The evaluation window is ``duration_days`` from the first launch on this
workstation, capped by ``not_after`` (inclusive, UTC). First-launch and
last-seen times live in a keyed state file in the workspace: deleting it can
restart the window only up to the cap, and a clock set back behind the
recorded last use refuses to run. This is an evaluation gate, not copy
protection; the limits are documented in the Installation Guide.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("azeo.license")

PRODUCT = "azeo-control-trainer"
LICENSE_DIRECTORY = "license"
LICENSE_FILENAME = "azeo.lic"
STATE_FILENAME = "state.json"
EXIT_LICENSE_REFUSED = 3
#: A clock this far behind the recorded last use is treated as moved backwards.
CLOCK_TOLERANCE = timedelta(hours=6)
#: A licence may be used this long before its ``not_before`` date (time zones).
EARLY_TOLERANCE = timedelta(days=1)

# BEGIN PUBLIC KEYS (written by tools/license_tool.py keygen --install)
PUBLIC_KEYS: dict[str, str] = {
    "azeo-eval-2026": "1b62f3c1617e3030e9643dde057220876f4c2da497bf04860df4e37c8b6a6836",
}
# END PUBLIC KEYS


class LicenseError(ValueError):
    """The licence document is missing, malformed, forged or unusable."""


@dataclass(frozen=True)
class License:
    id: str
    product: str
    edition: str
    licensee: str
    issued: date
    not_before: date
    not_after: date
    duration_days: int | None
    version: str
    key_id: str

    def body(self) -> dict:
        body = {
            "id": self.id, "product": self.product, "edition": self.edition,
            "licensee": self.licensee, "issued": self.issued.isoformat(),
            "not_before": self.not_before.isoformat(), "not_after": self.not_after.isoformat(),
            "version": self.version,
        }
        if self.duration_days is not None:
            body["duration_days"] = self.duration_days
        return body


@dataclass(frozen=True)
class LicenseStatus:
    required: bool
    valid: bool
    reason: str = ""
    license: License | None = None
    expires: datetime | None = None
    days_left: int | None = None
    first_launch: datetime | None = None

    @property
    def summary(self) -> str:
        if not self.required:
            return "unrestricted build (no licence file)"
        if self.license is None:
            return f"refused: {self.reason}"
        head = f"{self.license.edition} licence {self.license.id} for {self.license.licensee or 'unnamed licensee'}"
        if self.valid:
            when = self.expires.astimezone().strftime("%Y-%m-%d %H:%M") if self.expires else "unknown"
            return f"{head}, valid until {when} ({self.days_left} day(s) left)"
        return f"{head}, refused: {self.reason}"


def license_path() -> Path:
    """``AZEO_LICENSE_FILE`` or ``license/azeo.lic`` in the distribution root."""
    override = os.environ.get("AZEO_LICENSE_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    from .paths import project_root
    return project_root() / LICENSE_DIRECTORY / LICENSE_FILENAME


def state_path() -> Path:
    """``AZEO_LICENSE_STATE`` or ``license/state.json`` in the writable workspace."""
    override = os.environ.get("AZEO_LICENSE_STATE", "").strip()
    if override:
        return Path(override).expanduser()
    from .paths import workspace_root
    return workspace_root() / LICENSE_DIRECTORY / STATE_FILENAME


def canonical(body: dict) -> bytes:
    """The bytes that are signed: sorted keys, no whitespace, ASCII only."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sign_document(body: dict, private_key: bytes, key_id: str) -> dict:
    """Return the signed document for ``body`` (used by the release tool only)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    signer = Ed25519PrivateKey.from_private_bytes(private_key)
    signature = signer.sign(canonical(body))
    return {"license": dict(body), "key_id": key_id, "signature": signature.hex()}


def _date(value, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise LicenseError(f"licence field {field!r} is not an ISO date") from error


def parse_document(document: dict, public_keys: dict[str, str] | None = None) -> License:
    """Verify the signature and shape of a licence document."""
    keys = PUBLIC_KEYS if public_keys is None else public_keys
    if not isinstance(document, dict) or not isinstance(document.get("license"), dict):
        raise LicenseError("licence file does not contain a licence document")
    body, key_id = document["license"], str(document.get("key_id", ""))
    public = keys.get(key_id)
    if not public:
        raise LicenseError(f"licence key {key_id or '(none)'} is not trusted by this build")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        verifier = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public))
        verifier.verify(bytes.fromhex(str(document.get("signature", ""))), canonical(body))
    except ImportError as error:
        raise LicenseError("licence verification is unavailable in this environment") from error
    except (InvalidSignature, ValueError) as error:
        raise LicenseError("licence signature does not verify; the file was altered") from error
    if body.get("product") != PRODUCT:
        raise LicenseError(f"licence is for {body.get('product')!r}, not {PRODUCT}")
    duration = body.get("duration_days")
    if duration is not None and (not isinstance(duration, int) or duration < 1):
        raise LicenseError("licence duration_days must be a positive integer")
    licence = License(
        id=str(body.get("id", "")), product=PRODUCT, edition=str(body.get("edition", "evaluation")),
        licensee=str(body.get("licensee", "")), issued=_date(body.get("issued"), "issued"),
        not_before=_date(body.get("not_before"), "not_before"),
        not_after=_date(body.get("not_after"), "not_after"),
        duration_days=duration, version=str(body.get("version", "")), key_id=key_id)
    if not licence.id:
        raise LicenseError("licence has no id")
    if licence.not_after < licence.not_before:
        raise LicenseError("licence ends before it begins")
    return licence


def load_license(path: Path | None = None, public_keys: dict[str, str] | None = None) -> License | None:
    """The verified licence at ``path``, or ``None`` when no licence file exists."""
    path = license_path() if path is None else path
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LicenseError(f"licence file cannot be read: {error}") from error
    return parse_document(document, public_keys)


# ----------------------------------------------------------------- state
def _binding(licence: License) -> bytes:
    """Ties the state file to this licence, workstation and Windows user."""
    parts = "|".join((PRODUCT, licence.id, licence.key_id, PUBLIC_KEYS.get(licence.key_id, ""),
                      platform.node(), os.environ.get("USERNAME", os.environ.get("USER", ""))))
    return hashlib.sha256(b"azeo-license-state|" + parts.encode("utf-8")).digest()


def _read_state(path: Path, key: bytes, licence: License) -> dict | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        fields = {name: document[name] for name in ("license_id", "first_launch", "last_seen")}
        expected = hmac.new(key, canonical(fields), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, str(document.get("mac", ""))):
            log.warning("Licence state file failed its integrity check and was reset")
            return None
        if fields["license_id"] != licence.id:
            return None
        return {"first_launch": datetime.fromisoformat(fields["first_launch"]),
                "last_seen": datetime.fromisoformat(fields["last_seen"])}
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("Licence state file was unreadable and was reset")
        return None


def _write_state(path: Path, key: bytes, licence: License, first_launch: datetime, last_seen: datetime) -> None:
    fields = {"license_id": licence.id, "first_launch": first_launch.isoformat(),
              "last_seen": last_seen.isoformat()}
    fields["mac"] = hmac.new(key, canonical({k: fields[k] for k in ("license_id", "first_launch", "last_seen")}),
                             hashlib.sha256).hexdigest()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fields, indent=1), encoding="utf-8")
    except OSError as error:
        log.warning("Licence state could not be written: %s", error)


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def evaluate(licence: License, *, now: datetime | None = None, state_file: Path | None = None,
             installed_version: str | None = None, write: bool = True) -> LicenseStatus:
    """Apply the validity window, first-launch window and clock rules."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if licence.version and installed_version and installed_version != licence.version:
        return LicenseStatus(True, False, f"licence is for version {licence.version}, "
                                          f"this is {installed_version}", licence)
    if now < _day_start(licence.not_before) - EARLY_TOLERANCE:
        return LicenseStatus(True, False, f"licence is not valid before {licence.not_before.isoformat()}", licence)
    path = state_path() if state_file is None else state_file
    key = _binding(licence)
    state = _read_state(path, key, licence)
    first_launch = state["first_launch"] if state else now
    last_seen = state["last_seen"] if state else now
    if now + CLOCK_TOLERANCE < last_seen:
        return LicenseStatus(True, False, "the system clock is earlier than the last recorded use "
                                          f"({last_seen.astimezone():%Y-%m-%d %H:%M}); correct the clock",
                             licence, first_launch=first_launch)
    if write:
        _write_state(path, key, licence, first_launch, max(last_seen, now))
    hard_stop = _day_start(licence.not_after) + timedelta(days=1)
    expires = hard_stop
    if licence.duration_days is not None:
        expires = min(hard_stop, first_launch + timedelta(days=licence.duration_days))
    if now >= expires:
        return LicenseStatus(True, False, f"the evaluation period ended on {expires.astimezone():%Y-%m-%d %H:%M}",
                             licence, expires=expires, days_left=0, first_launch=first_launch)
    days_left = max(1, -(-(expires - now) // timedelta(days=1)))
    return LicenseStatus(True, True, "", licence, expires=expires, days_left=days_left, first_launch=first_launch)


def check(now: datetime | None = None, *, write: bool = True) -> LicenseStatus:
    """The status every product entry point consults before opening a window."""
    path = license_path()
    try:
        licence = load_license(path)
    except LicenseError as error:
        return LicenseStatus(True, False, str(error))
    if licence is None:
        return LicenseStatus(False, True, "no licence file; unrestricted build")
    installed_version = None
    try:
        from .distribution import read_installation
        installation = read_installation()
        installed_version = installation.version if installation.installed else None
    except ValueError:
        installed_version = None
    return evaluate(licence, now=now, installed_version=installed_version, write=write)
