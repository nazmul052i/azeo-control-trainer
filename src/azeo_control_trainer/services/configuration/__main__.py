"""Local administrative CLI; database credentials are never HTTP/UI settings."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Azeo configuration service administration")
    parser.add_argument("--dsn-file", type=Path, help="Private JSON file containing a dsn field")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8766)
    identity = commands.add_parser("identity", help="Create or rotate a named API credential")
    identity.add_argument("name")
    identity.add_argument("--administrator", action="store_true")
    identity.add_argument("--output", type=Path, required=True)
    grant = commands.add_parser("grant")
    grant.add_argument("name")
    grant.add_argument("project_id")
    grant.add_argument("role", choices=["reader", "importer", "engineer"])
    revoke = commands.add_parser("revoke")
    revoke.add_argument("name")
    args = parser.parse_args(argv)
    from azeo_control_trainer.core.configuration.repository import Repository

    settings = json.loads(args.dsn_file.read_text(encoding="utf-8")) if args.dsn_file else {}
    dsn = settings.get("dsn", os.environ.get("AZEO_CONFIGURATION_DSN", ""))
    if not dsn:
        parser.error("Set AZEO_CONFIGURATION_DSN or provide --dsn-file")
    repository = Repository(dsn)
    if args.command == "migrate":
        repository.migrate()
        print("Configuration schema verified.")
    elif args.command == "identity":
        # Refuse accidental replacement of someone else's client configuration.
        with args.output.open("x", encoding="utf-8") as output:
            token = repository.provision_identity(args.name, administrator=args.administrator)
            json.dump({"url": "http://127.0.0.1:8766", "token": token}, output)
        print("Client credential written. Keep this file private to the named engineer.")
    elif args.command == "grant":
        repository.grant(args.name, args.project_id, args.role)
        print("Project access updated.")
    elif args.command == "revoke":
        repository.revoke(args.name)
        print("Credential revoked.")
    else:
        import uvicorn
        from .api import create_app
        repository.migrate()
        recovery = None
        if settings.get("pg_bin") and args.dsn_file:
            from azeo_control_trainer.core.configuration.recovery import RecoveryRepository
            recovery = RecoveryRepository(repository, settings.get("recovery_root", args.dsn_file.parent / "recovery"), settings["pg_bin"])
        # LAN exposure and Windows-service packaging are a later qualification gate.
        uvicorn.run(create_app(repository, recovery=recovery), host="127.0.0.1", port=args.port,
                    access_log=False, timeout_keep_alive=5, limit_concurrency=16)


if __name__ == "__main__":
    main()
