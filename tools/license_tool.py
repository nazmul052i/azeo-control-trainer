"""Issue, inspect and verify Azeo evaluation licences; manage the signing key.

    python tools/license_tool.py keygen [--key-id azeo-eval-2026] [--private PATH] [--install]
    python tools/license_tool.py issue --licensee "Name" [--days 7] [--distribution-days 0]
                                       [--version 0.3.0] [--key-id ID] [--private PATH] [--out PATH]
    python tools/license_tool.py inspect PATH [--state PATH]

The private key lives outside the repository (default
``%USERPROFILE%\\.azeo-release\\azeo-license-<key-id>.pem``); the release owner
backs it up, because a lost key means a new key id, a new public key in
``config/licensing.py`` and a new build. ``keygen --install`` records the
public key between the PUBLIC KEYS markers of ``config/licensing.py``; commit
that change with the build that ships licences signed by it.

An evaluation licence is valid for ``--days`` days from the first launch on a
workstation and never past ``not_after``, which is the issue date plus
``--days`` plus ``--distribution-days`` (the allowance for handing the build
over before it is first used).
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from azeo_control_trainer.config import licensing  # noqa: E402

DEFAULT_KEY_ID = "azeo-eval-2026"
KEY_DIRECTORY = Path.home() / ".azeo-release"


def private_path(key_id: str, override: Path | None) -> Path:
    path = override or KEY_DIRECTORY / f"azeo-license-{key_id}.pem"
    if path.resolve().is_relative_to(ROOT):
        raise SystemExit("The private key must not be stored inside the repository")
    return path


def load_private(path: Path) -> bytes:
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    return key.private_bytes_raw()


def install_public_key(key_id: str, public_hex: str) -> Path:
    module = ROOT / "src" / "azeo_control_trainer" / "config" / "licensing.py"
    text = module.read_text(encoding="utf-8")
    keys = dict(licensing.PUBLIC_KEYS)
    keys[key_id] = public_hex
    body = "PUBLIC_KEYS: dict[str, str] = {\n" + "".join(
        f"    {json.dumps(name)}: {json.dumps(value)},\n" for name, value in sorted(keys.items())) + "}\n"
    pattern = re.compile(r"(# BEGIN PUBLIC KEYS[^\n]*\n)(.*?)(# END PUBLIC KEYS)", re.S)
    updated, count = pattern.subn(lambda m: m.group(1) + body + m.group(3), text)
    if count != 1:
        raise SystemExit("PUBLIC KEYS markers not found in config/licensing.py")
    module.write_text(updated, encoding="utf-8")
    return module


def keygen(options) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    path = private_path(options.key_id, options.private)
    if path.exists():
        raise SystemExit(f"{path} already exists; choose another --key-id or --private path")
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    public_hex = key.public_key().public_bytes_raw().hex()
    print(f"Private key written to {path} (back it up; it never enters the repository)")
    print(f"Key id: {options.key_id}\nPublic key: {public_hex}")
    if options.install:
        print(f"Public key recorded in {install_public_key(options.key_id, public_hex)}")
    return 0


def issue(options) -> int:
    private = load_private(private_path(options.key_id, options.private))
    if options.key_id not in licensing.PUBLIC_KEYS:
        raise SystemExit(f"Key id {options.key_id} is not in config/licensing.py; run keygen --install first")
    issued = options.issued or datetime.now(timezone.utc).date()
    not_after = issued + timedelta(days=options.days + options.distribution_days - 1)
    body = {
        "id": options.id or f"AZEO-EVAL-{issued:%Y%m%d}-{secrets.token_hex(3).upper()}",
        "product": licensing.PRODUCT, "edition": options.edition, "licensee": options.licensee,
        "issued": issued.isoformat(), "not_before": issued.isoformat(), "not_after": not_after.isoformat(),
        "duration_days": options.days, "version": options.version,
    }
    document = licensing.sign_document(body, private, options.key_id)
    licence = licensing.parse_document(document)      # round trip through the verifier
    output = options.out or ROOT / "build" / "license" / licensing.LICENSE_FILENAME
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    print(f"Licence {licence.id} for {licence.licensee!r}: {licence.duration_days} day(s) from first launch, "
          f"not after {licence.not_after.isoformat()}, version {licence.version or 'any'}\n{output}")
    return 0


def inspect(options) -> int:
    licence = licensing.load_license(options.path)
    if licence is None:
        raise SystemExit(f"No licence file at {options.path}")
    status = licensing.evaluate(licence, state_file=options.state, write=False)
    print(json.dumps({"license": licence.body(), "key_id": licence.key_id, "valid": status.valid,
                      "reason": status.reason, "expires": status.expires.isoformat() if status.expires else None,
                      "days_left": status.days_left}, indent=1))
    return 0 if status.valid else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("keygen", help="create a signing key and record its public half")
    gen.add_argument("--key-id", default=DEFAULT_KEY_ID)
    gen.add_argument("--private", type=Path)
    gen.add_argument("--install", action="store_true", help="write the public key into config/licensing.py")
    gen.set_defaults(run=keygen)
    make = commands.add_parser("issue", help="sign an evaluation licence")
    make.add_argument("--licensee", required=True)
    make.add_argument("--days", type=int, default=7)
    make.add_argument("--distribution-days", type=int, default=0)
    make.add_argument("--edition", default="evaluation")
    make.add_argument("--version", default="")
    make.add_argument("--issued", type=date.fromisoformat, help="ISO date; default today (UTC)")
    make.add_argument("--id", help="reuse a licence id so a patched version keeps the workstation's "
                                   "evaluation clock (the first-launch record is keyed by id)")
    make.add_argument("--key-id", default=DEFAULT_KEY_ID)
    make.add_argument("--private", type=Path)
    make.add_argument("--out", type=Path)
    make.set_defaults(run=issue)
    show = commands.add_parser("inspect", help="verify a licence file and show its status")
    show.add_argument("path", type=Path)
    show.add_argument("--state", type=Path, help="state file to evaluate against (default: none)")
    show.set_defaults(run=inspect)
    options = parser.parse_args(argv)
    return options.run(options)


if __name__ == "__main__":
    sys.exit(main())
