"""Manage an isolated loopback PostgreSQL pilot without changing installed services.

Run with the repository venv. Generated credentials and databases live in ignored
data/configuration; never print them or put a password on a process command line.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
PILOT_HOME = ROOT / "data" / "configuration"
CLUSTER = PILOT_HOME / "postgres"
SERVER = PILOT_HOME / "server.json"
PROFILE = PILOT_HOME / "client.json"
LOGS = ROOT / "logs" / "configuration"
PG_BIN = Path("C:/Program Files/PostgreSQL/16/bin")
FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def run_pg(*args):
    subprocess.run([str(PG_BIN / args[0]), *map(str, args[1:])], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=FLAGS)


def setup():
    from psycopg.conninfo import make_conninfo
    from azeo_control_trainer.core.configuration.repository import Repository

    if SERVER.exists() or CLUSTER.exists() or PROFILE.exists():
        raise RuntimeError("Pilot already exists; use start or status. Setup never replaces a database.")
    if not (PG_BIN / "initdb.exe").is_file():
        raise RuntimeError("Install PostgreSQL 16 binaries or supply --pg-bin")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 55432))
    PILOT_HOME.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        account = subprocess.check_output(["whoami"], text=True).strip()
        subprocess.run(["icacls", str(PILOT_HOME), "/inheritance:r", "/grant:r",
                        f"{account}:(OI)(CI)F", "SYSTEM:(OI)(CI)F"], check=True,
                       stdout=subprocess.DEVNULL, creationflags=FLAGS)
    else:
        PILOT_HOME.chmod(0o700)
    password = secrets.token_urlsafe(40)
    password_file = PILOT_HOME / "init-password"
    try:
        password_file.write_text(password, encoding="utf-8")
        run_pg("initdb.exe", "-D", CLUSTER, "-U", "azeo_pilot_admin", "--encoding=UTF8",
               "--locale=C", "--auth=scram-sha-256", "--pwfile", password_file)
    finally:
        password_file.unlink(missing_ok=True)
    with (CLUSTER / "postgresql.conf").open("a", encoding="utf-8") as output:
        output.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\nmax_connections = 40\n")
    dsn = make_conninfo(host="127.0.0.1", port=55432, dbname="postgres",
                       user="azeo_pilot_admin", password=password)
    SERVER.write_text(json.dumps({"dsn": dsn, "pg_bin": str(PG_BIN)}), encoding="utf-8")
    start_database()
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("CREATE DATABASE azeo_configuration_pilot")
    dsn = make_conninfo(dsn, dbname="azeo_configuration_pilot")
    SERVER.write_text(json.dumps({"dsn": dsn, "pg_bin": str(PG_BIN)}), encoding="utf-8")
    repository = Repository(dsn)
    repository.migrate()
    token = repository.provision_identity("pilot-engineer", administrator=True)
    PROFILE.write_text(json.dumps({"url": "http://127.0.0.1:8766", "token": token}), encoding="utf-8")
    print("Isolated pilot initialized: PostgreSQL 55432, configuration API 8766.")


def start_database():
    result = subprocess.run([str(PG_BIN / "pg_ctl.exe"), "status", "-D", str(CLUSTER)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=FLAGS)
    if result.returncode != 0:
        run_pg("pg_ctl.exe", "start", "-D", CLUSTER, "-l", LOGS / "postgres.log", "-w", "-t", "30")


def start_service():
    from azeo_control_trainer.core.configuration.client import ConfigurationClient, read_profile
    start_database()
    client = ConfigurationClient(**read_profile(PROFILE))
    try:
        client.request("/v1/status")
        print("Configuration pilot is already running.")
        return
    except ValueError:
        pass
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8766))
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    with (LOGS / "service.log").open("ab") as output:
        process = subprocess.Popen([sys.executable, "-m",
                                    "azeo_control_trainer.services.configuration",
                                    "--dsn-file", str(SERVER), "serve"],
                                   cwd=ROOT, env=environment, stdout=output, stderr=output,
                                   creationflags=FLAGS)
    (PILOT_HOME / "service.pid").write_text(str(process.pid), encoding="ascii")
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Service startup failed; inspect logs/configuration/service.log")
        try:
            client.request("/v1/status")
            print("Configuration pilot is ready. Open Project Administrator > Configuration Database.")
            return
        except ValueError:
            time.sleep(0.1)
    raise RuntimeError("Service startup timed out; inspect the service log")


def main():
    global PG_BIN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup", "start", "status", "import"])
    parser.add_argument("--pg-bin", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--name")
    args = parser.parse_args()
    if args.pg_bin:
        PG_BIN = args.pg_bin
    elif SERVER.exists():
        PG_BIN = Path(json.loads(SERVER.read_text(encoding="utf-8"))["pg_bin"])
    if args.command == "setup":
        setup()
        start_service()
    elif args.command == "start":
        start_service()
    else:
        from azeo_control_trainer.core.configuration.client import ConfigurationClient, read_profile
        client = ConfigurationClient(**read_profile(PROFILE))
        if args.command == "status":
            print(json.dumps({"status": client.request("/v1/status"),
                              "projects": client.request("/v1/projects")}, indent=2))
        else:
            from azeo_control_trainer.core.configuration.documents import read_project
            if args.project is None:
                parser.error("import requires --project")
            bundle = read_project(args.project)
            payload = {"name": args.name or args.project.name, "files": bundle["files"]}
            preview = client.request("/v1/imports/preview", payload)
            result = client.request("/v1/imports", {**payload,
                                    "expected_generation": preview["expected_generation"],
                                    "command_id": str(uuid4())})
            print(json.dumps({**result, "excluded_files": len(bundle["excluded"]),
                              "warnings": preview["warnings"]}, indent=2))


if __name__ == "__main__":
    main()
