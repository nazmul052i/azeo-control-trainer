from __future__ import annotations

from pathlib import Path
from contextlib import closing, contextmanager
import sqlite3
from datetime import datetime, timezone
from typing import Any
import json
from hashlib import sha256
import os
import secrets
import threading
import time
from weakref import WeakValueDictionary

from ..core.integrity import canonical_json, load_or_create_integrity_key, sign_payload, verify_signature


class SQLiteStore:
    _locks_guard = threading.Lock()
    # Temporary/test databases must not leave one permanent lock object each.
    _locks: WeakValueDictionary[Path, threading.RLock] = WeakValueDictionary()

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._locks_guard:
            self._write_lock = self._locks.setdefault(self.db_path, threading.RLock())
        self._integrity_key = load_or_create_integrity_key(self.db_path.parent)
        self._init_db()
        audit_ok, audit_message = self.verify_audit_chain()
        if not audit_ok:
            raise RuntimeError(f"PA Designer history integrity check failed: {audit_message}")
        self.recover_abandoned_runs()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30.0)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA synchronous=FULL")
        return con

    @contextmanager
    def _interprocess_lock(self):
        """Serialize DB+anchor publication across UI and CLI processes."""

        lock_path = self.db_path.with_suffix(self.db_path.suffix + ".lock")
        if os.name == "nt":
            import msvcrt

            with lock_path.open("a+b") as lock_file:
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                deadline = time.monotonic() + 30.0
                while True:
                    lock_file.seek(0)
                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                "Timed out waiting for the PA Designer history lock"
                            ) from exc
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _write_transaction(self):
        with self._write_lock, self._interprocess_lock():
            con = self._connect()
            self._pending_anchor: tuple[int, str] | None = None
            try:
                yield con
                con.commit()
                if self._pending_anchor is not None:
                    self._write_audit_anchor(*self._pending_anchor)
            except BaseException:
                con.rollback()
                raise
            finally:
                self._pending_anchor = None
                con.close()

    def _init_db(self):
        with self._write_transaction() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=FULL")
            con.execute("""
                CREATE TABLE IF NOT EXISTS procedure_runs (
                    run_id TEXT PRIMARY KEY,
                    procedure_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    metadata_json TEXT DEFAULT '{}'
                )
            """)
            self._ensure_column(con, "procedure_runs", "metadata_json", "TEXT DEFAULT '{}'")
            con.execute("""
                CREATE TABLE IF NOT EXISTS step_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    data_json TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS tag_writes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS tag_reads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    value_json TEXT,
                    quality TEXT NOT NULL,
                    source TEXT,
                    tag_timestamp TEXT,
                    message TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    step_id TEXT,
                    message TEXT,
                    data_json TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS operator_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    step_id TEXT,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    acknowledged_by TEXT,
                    acknowledgement_comment TEXT,
                    data_json TEXT DEFAULT '{}'
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS run_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    runtime_variables_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS audit_chain (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    record_type TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    integrity_hmac TEXT NOT NULL
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS audit_chain_run_idx ON audit_chain(run_id, sequence)")
            con.row_factory = sqlite3.Row
            self._baseline_legacy_records(con)

    @staticmethod
    def _ensure_column(con: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _baseline_legacy_records(self, con: sqlite3.Connection) -> None:
        """Sign one explicit upgrade snapshot when a pre-audit database is opened."""

        if con.execute("SELECT 1 FROM audit_chain LIMIT 1").fetchone():
            return
        anchor_path = self.db_path.with_suffix(self.db_path.suffix + ".audit-anchor.json")
        if anchor_path.exists():
            raise RuntimeError(
                "Audit chain is empty but its signed anchor exists; refusing to baseline possible truncation"
            )
        tables = (
            "procedure_runs", "step_events", "run_events", "operator_messages",
            "run_checkpoints", "tag_writes", "tag_reads",
        )
        counts = {
            table: int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }
        if not any(counts.values()):
            return

        imported_at = self.now()
        self._append_audit(
            con, None, "AUDIT_BASELINE", f"legacy-{imported_at}", imported_at,
            {
                "scope": "legacy_upgrade_snapshot",
                "captured_at": imported_at,
                "record_counts": counts,
                "warning": "Records before this baseline were not protected contemporaneously",
            },
            update_anchor=False,
        )
        for row in con.execute("SELECT * FROM procedure_runs ORDER BY started_at, run_id"):
            self._append_audit(
                con, row["run_id"], "RUN_STARTED", row["run_id"], row["started_at"],
                {
                    "procedure_id": row["procedure_id"], "name": row["name"],
                    "status": "RUNNING", "metadata": self._json_value(row["metadata_json"], {}),
                    "integrity_scope": "legacy_upgrade_snapshot",
                },
                update_anchor=False,
            )

        specs = (
            ("step_events", "STEP_EVENT", "created_at", lambda row: {
                "step_id": row["step_id"], "step_type": row["step_type"],
                "status": row["status"], "message": row["message"] or "",
                "data": self._json_value(row["data_json"], {}),
            }),
            ("run_events", "RUN_EVENT", "created_at", lambda row: {
                "event_type": row["event_type"], "step_id": row["step_id"],
                "message": row["message"] or "", "data": self._json_value(row["data_json"], {}),
            }),
            ("operator_messages", "OPERATOR_MESSAGE", "occurred_at", lambda row: {
                "step_id": row["step_id"], "event_type": row["event_type"],
                "severity": row["severity"], "message": row["message"],
                "data": self._json_value(row["data_json"], {}),
            }),
            ("run_checkpoints", "CHECKPOINT", "created_at", lambda row: {
                "step_id": row["step_id"], "status": row["status"],
                "runtime_variables": self._json_value(row["runtime_variables_json"], {}),
            }),
            ("tag_writes", "TAG_WRITE", "created_at", lambda row: {
                "tag": row["tag"], "value": self._json_value(row["value_json"], None),
            }),
            ("tag_reads", "TAG_READ", "created_at", lambda row: {
                "tag": row["tag"], "value": self._json_value(row["value_json"], None),
                "quality": row["quality"], "source": row["source"] or "",
                "tag_timestamp": row["tag_timestamp"] or "", "message": row["message"] or "",
            }),
        )
        for table, record_type, time_column, payload_for in specs:
            for row in con.execute(f"SELECT * FROM {table} ORDER BY id"):
                self._append_audit(
                    con, row["run_id"], record_type, str(row["id"]), row[time_column],
                    payload_for(row) | {"integrity_scope": "legacy_upgrade_snapshot"},
                    update_anchor=False,
                )
                if record_type == "OPERATOR_MESSAGE" and row["acknowledged_at"]:
                    self._append_audit(
                        con, row["run_id"], "OPERATOR_ACK", str(row["id"]), row["acknowledged_at"],
                        {
                            "operator": row["acknowledged_by"],
                            "comment": row["acknowledgement_comment"],
                            "integrity_scope": "legacy_upgrade_snapshot",
                        },
                        update_anchor=False,
                    )

        for row in con.execute(
            "SELECT * FROM procedure_runs WHERE status<>'RUNNING' ORDER BY ended_at, run_id"
        ):
            record_type = "RUN_INTERRUPTED" if row["status"] == "INTERRUPTED" else "RUN_FINISHED"
            self._append_audit(
                con, row["run_id"], record_type, row["run_id"], row["ended_at"] or imported_at,
                {"status": row["status"], "integrity_scope": "legacy_upgrade_snapshot"},
                update_anchor=False,
            )
        tail = con.execute(
            "SELECT sequence, record_hash FROM audit_chain ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        self._pending_anchor = (int(tail["sequence"]), str(tail["record_hash"]))

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _json_text(value: Any) -> str:
        return canonical_json(value).decode("utf-8")

    def _append_audit(
        self,
        con: sqlite3.Connection,
        run_id: str | None,
        record_type: str,
        record_id: str,
        occurred_at: str,
        payload: dict[str, Any],
        *,
        update_anchor: bool = True,
    ) -> None:
        row = con.execute(
            "SELECT record_hash FROM audit_chain ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous = str(row[0]) if row else "0" * 64
        envelope = {
            "run_id": run_id,
            "record_type": record_type,
            "record_id": record_id,
            "occurred_at": occurred_at,
            "payload": payload,
            "previous_hash": previous,
        }
        record_hash = sha256(canonical_json(envelope)).hexdigest()
        signature = sign_payload(self._integrity_key, {"record_hash": record_hash})
        cursor = con.execute(
            "INSERT INTO audit_chain(run_id, record_type, record_id, occurred_at, payload_json, previous_hash, record_hash, integrity_hmac) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                run_id, record_type, record_id, occurred_at,
                canonical_json(payload).decode("utf-8"), previous, record_hash, signature,
            ),
        )
        if update_anchor:
            self._pending_anchor = (int(cursor.lastrowid), record_hash)

    def _write_audit_anchor(self, sequence: int, record_hash: str) -> None:
        anchor_path = self.db_path.with_suffix(self.db_path.suffix + ".audit-anchor.json")
        payload = {"sequence": sequence, "record_hash": record_hash}
        document = payload | {"integrity_hmac": sign_payload(self._integrity_key, payload)}
        temporary = anchor_path.with_name(f".{anchor_path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(document, sort_keys=True, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, anchor_path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _json_value(value: str | None, fallback: Any) -> Any:
        try:
            return json.loads(value or "")
        except json.JSONDecodeError:
            return fallback

    def _audit_record_matches(
        self, con: sqlite3.Connection, row: sqlite3.Row, payload: dict[str, Any]
    ) -> bool:
        kind = row["record_type"]
        record_id = row["record_id"]
        if kind == "RUN_STARTED":
            current = con.execute(
                "SELECT * FROM procedure_runs WHERE run_id=?", (row["run_id"],)
            ).fetchone()
            return bool(current) and all((
                current["procedure_id"] == payload["procedure_id"],
                current["name"] == payload["name"],
                current["started_at"] == row["occurred_at"],
                self._json_value(current["metadata_json"], {}) == payload["metadata"],
            ))
        if kind in {"RUN_FINISHED", "RUN_INTERRUPTED"}:
            current = con.execute(
                "SELECT status, ended_at FROM procedure_runs WHERE run_id=?", (row["run_id"],)
            ).fetchone()
            expected_status = payload.get("status", "INTERRUPTED")
            return bool(current) and current["status"] == expected_status and current["ended_at"] == row["occurred_at"]
        table_map = {
            "STEP_EVENT": "step_events",
            "RUN_EVENT": "run_events",
            "OPERATOR_MESSAGE": "operator_messages",
            "CHECKPOINT": "run_checkpoints",
            "TAG_WRITE": "tag_writes",
            "TAG_READ": "tag_reads",
        }
        if kind == "OPERATOR_ACK":
            current = con.execute(
                "SELECT * FROM operator_messages WHERE id=?", (record_id,)
            ).fetchone()
            return bool(current) and all((
                current["acknowledged_at"] == row["occurred_at"],
                current["acknowledged_by"] == payload["operator"],
                current["acknowledgement_comment"] == payload["comment"],
            ))
        table = table_map.get(kind)
        if table is None:
            return True
        current = con.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        if not current or current["run_id"] != row["run_id"]:
            return False
        if kind == "STEP_EVENT":
            return all((current["step_id"] == payload["step_id"], current["step_type"] == payload["step_type"], current["status"] == payload["status"], (current["message"] or "") == payload["message"], self._json_value(current["data_json"], {}) == payload["data"], current["created_at"] == row["occurred_at"]))
        if kind == "RUN_EVENT":
            return all((current["event_type"] == payload["event_type"], current["step_id"] == payload["step_id"], (current["message"] or "") == payload["message"], self._json_value(current["data_json"], {}) == payload["data"], current["created_at"] == row["occurred_at"]))
        if kind == "OPERATOR_MESSAGE":
            return all((current["step_id"] == payload["step_id"], current["event_type"] == payload["event_type"], current["severity"] == payload["severity"], current["message"] == payload["message"], self._json_value(current["data_json"], {}) == payload["data"], current["occurred_at"] == row["occurred_at"]))
        if kind == "CHECKPOINT":
            return all((current["step_id"] == payload["step_id"], current["status"] == payload["status"], self._json_value(current["runtime_variables_json"], {}) == payload["runtime_variables"], current["created_at"] == row["occurred_at"]))
        if kind == "TAG_WRITE":
            return all((current["tag"] == payload["tag"], self._json_value(current["value_json"], None) == payload["value"], current["created_at"] == row["occurred_at"]))
        if kind == "TAG_READ":
            return all((current["tag"] == payload["tag"], self._json_value(current["value_json"], None) == payload["value"], current["quality"] == payload["quality"], (current["source"] or "") == payload["source"], (current["tag_timestamp"] or "") == payload["tag_timestamp"], (current["message"] or "") == payload["message"], current["created_at"] == row["occurred_at"]))
        return True

    @staticmethod
    def _first_uncovered_record(
        con: sqlite3.Connection, table: str, audit_type: str
    ) -> str | None:
        row = con.execute(
            f"SELECT CAST(source.id AS TEXT) FROM {table} AS source "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM audit_chain AS audit "
            "WHERE audit.record_type=? AND audit.record_id=CAST(source.id AS TEXT)"
            ") LIMIT 1",
            (audit_type,),
        ).fetchone()
        return str(row[0]) if row else None

    def _verify_audit_coverage(self, con: sqlite3.Connection) -> tuple[bool, str]:
        """Prove that every mutable operational row is represented in the chain."""

        for table, audit_type in (
            ("step_events", "STEP_EVENT"),
            ("run_events", "RUN_EVENT"),
            ("operator_messages", "OPERATOR_MESSAGE"),
            ("run_checkpoints", "CHECKPOINT"),
            ("tag_writes", "TAG_WRITE"),
            ("tag_reads", "TAG_READ"),
        ):
            record_id = self._first_uncovered_record(con, table, audit_type)
            if record_id is not None:
                return False, f"{table} record {record_id} has no audit entry"

        run = con.execute(
            "SELECT run_id FROM procedure_runs AS source WHERE NOT EXISTS ("
            "SELECT 1 FROM audit_chain AS audit WHERE audit.record_type='RUN_STARTED' "
            "AND audit.record_id=source.run_id) LIMIT 1"
        ).fetchone()
        if run:
            return False, f"Procedure run {run[0]} has no start audit entry"

        terminal = con.execute(
            "SELECT run_id FROM procedure_runs AS source WHERE status<>'RUNNING' AND NOT EXISTS ("
            "SELECT 1 FROM audit_chain AS audit WHERE audit.record_id=source.run_id "
            "AND audit.record_type IN ('RUN_FINISHED','RUN_INTERRUPTED')) LIMIT 1"
        ).fetchone()
        if terminal:
            return False, f"Terminal procedure run {terminal[0]} has no completion audit entry"

        acknowledgement = con.execute(
            "SELECT CAST(id AS TEXT) FROM operator_messages AS source "
            "WHERE acknowledged_at IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM audit_chain AS audit WHERE audit.record_type='OPERATOR_ACK' "
            "AND audit.record_id=CAST(source.id AS TEXT)) LIMIT 1"
        ).fetchone()
        if acknowledgement:
            return False, f"Operator acknowledgement {acknowledgement[0]} has no audit entry"
        return True, "Operational records are fully covered"

    @staticmethod
    def _process_exists(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False

    def recover_abandoned_runs(self) -> int:
        """Mark runs whose owning process no longer exists as interrupted."""

        recovered = 0
        with self._write_transaction() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT run_id, metadata_json FROM procedure_runs WHERE status='RUNNING'"
            ).fetchall()
            for row in rows:
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                except json.JSONDecodeError:
                    metadata = {}
                try:
                    owner_pid = int(metadata.get("process_id") or 0)
                except (TypeError, ValueError):
                    owner_pid = 0
                if owner_pid and self._process_exists(owner_pid):
                    continue
                when = self.now()
                con.execute(
                    "UPDATE procedure_runs SET status='INTERRUPTED', ended_at=? WHERE run_id=? AND status='RUNNING'",
                    (when, row["run_id"]),
                )
                self._append_audit(
                    con, row["run_id"], "RUN_INTERRUPTED", row["run_id"], when,
                    {"reason": "owning process is no longer active", "owner_pid": owner_pid},
                )
                recovered += 1
        return recovered

    def _verify_audit_chain_connection(self, con: sqlite3.Connection) -> tuple[bool, str]:
        previous = "0" * 64
        count = 0
        last_sequence: int | None = None
        has_legacy_baseline = False
        for row in con.execute("SELECT * FROM audit_chain ORDER BY sequence ASC"):
            count += 1
            last_sequence = int(row["sequence"])
            try:
                payload = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError):
                return False, f"Audit row {row['sequence']} has invalid JSON"
            if not isinstance(payload, dict):
                return False, f"Audit row {row['sequence']} payload is not an object"
            if row["record_type"] == "AUDIT_BASELINE":
                has_legacy_baseline = True
            envelope = {
                "run_id": row["run_id"],
                "record_type": row["record_type"],
                "record_id": row["record_id"],
                "occurred_at": row["occurred_at"],
                "payload": payload,
                "previous_hash": previous,
            }
            expected_hash = sha256(canonical_json(envelope)).hexdigest()
            if row["previous_hash"] != previous or row["record_hash"] != expected_hash:
                return False, f"Audit chain mismatch at sequence {row['sequence']}"
            if not verify_signature(
                self._integrity_key,
                {"record_hash": expected_hash},
                row["integrity_hmac"],
            ):
                return False, f"Audit signature mismatch at sequence {row['sequence']}"
            try:
                if not self._audit_record_matches(con, row, payload):
                    return False, f"Operational record mismatch at audit sequence {row['sequence']}"
            except (KeyError, TypeError, ValueError, sqlite3.Error):
                return False, f"Audit payload is malformed at sequence {row['sequence']}"
            previous = expected_hash

        coverage_ok, coverage_message = self._verify_audit_coverage(con)
        if not coverage_ok:
            return False, coverage_message

        anchor_path = self.db_path.with_suffix(self.db_path.suffix + ".audit-anchor.json")
        if count:
            try:
                anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return False, "Audit chain anchor is missing or invalid"
            if not isinstance(anchor, dict):
                return False, "Audit chain anchor is not an object"
            signature = str(anchor.pop("integrity_hmac", ""))
            if not verify_signature(self._integrity_key, anchor, signature):
                return False, "Audit chain anchor signature is invalid"
            if anchor != {"sequence": last_sequence, "record_hash": previous}:
                return False, "Audit chain is truncated or does not match its anchor"
        elif anchor_path.exists():
            return False, "Audit chain is empty but a non-empty chain anchor exists"
        qualifier = "; includes signed legacy upgrade baseline" if has_legacy_baseline else ""
        return True, f"Audit chain verified ({count} records{qualifier})"

    def verify_audit_chain(self) -> tuple[bool, str]:
        with self._write_lock, self._interprocess_lock(), closing(self._connect()) as con:
            con.row_factory = sqlite3.Row
            return self._verify_audit_chain_connection(con)

    def run_report_snapshot(self, run_id: str) -> dict[str, Any] | None:
        """Read one run and its integrity result from a single DB snapshot."""

        with self._write_lock, self._interprocess_lock(), closing(self._connect()) as con:
            con.row_factory = sqlite3.Row
            con.execute("BEGIN")
            audit_ok, audit_message = self._verify_audit_chain_connection(con)
            run = con.execute(
                "SELECT * FROM procedure_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                return None

            def rows(query: str) -> list[dict[str, Any]]:
                return [self._row_to_dict(row) for row in con.execute(query, (run_id,)).fetchall()]

            checkpoint = con.execute(
                "SELECT * FROM run_checkpoints WHERE run_id=? ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            return {
                "run": self._row_to_dict(run),
                "steps": rows("SELECT * FROM step_events WHERE run_id=? ORDER BY id ASC"),
                "events": rows("SELECT * FROM run_events WHERE run_id=? ORDER BY id ASC"),
                "operator_messages": rows("SELECT * FROM operator_messages WHERE run_id=? ORDER BY id ASC"),
                "latest_checkpoint": self._row_to_dict(checkpoint) if checkpoint else None,
                "tag_writes": rows("SELECT * FROM tag_writes WHERE run_id=? ORDER BY id ASC"),
                "audit_integrity": {"valid": audit_ok, "message": audit_message},
            }

    def sign_report_digest(self, digest: str) -> str:
        return sign_payload(self._integrity_key, {"report_digest": digest})

    def verify_report_digest(self, digest: str, signature: str) -> bool:
        return verify_signature(
            self._integrity_key, {"report_digest": digest}, signature
        )

    def start_run(self, run_id: str, procedure_id: str, name: str, status: str, metadata: dict[str, Any] | None = None):
        metadata = dict(metadata or {})
        metadata.setdefault("process_id", os.getpid())
        when = self.now()
        with self._write_transaction() as con:
            con.execute(
                "INSERT INTO procedure_runs(run_id, procedure_id, name, status, started_at, metadata_json) VALUES(?,?,?,?,?,?)",
                (run_id, procedure_id, name, status, when, self._json_text(metadata)),
            )
            self._append_audit(con, run_id, "RUN_STARTED", run_id, when, {
                "procedure_id": procedure_id, "name": name, "status": status, "metadata": metadata,
            })

    def finish_run(self, run_id: str, status: str):
        when = self.now()
        with self._write_transaction() as con:
            cursor = con.execute(
                "UPDATE procedure_runs SET status=?, ended_at=? WHERE run_id=? AND status='RUNNING'",
                (status, when, run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Run {run_id} is missing or is not active")
            self._append_audit(con, run_id, "RUN_FINISHED", run_id, when, {"status": status})

    def log_step(self, run_id: str, step_id: str, step_type: str, status: str, message: str = "", data: dict[str, Any] | None = None):
        when = self.now()
        payload = data or {}
        with self._write_transaction() as con:
            cursor = con.execute(
                "INSERT INTO step_events(run_id, step_id, step_type, status, message, data_json, created_at) VALUES(?,?,?,?,?,?,?)",
                (run_id, step_id, step_type, status, message, self._json_text(payload), when),
            )
            self._append_audit(con, run_id, "STEP_EVENT", str(cursor.lastrowid), when, {
                "step_id": step_id, "step_type": step_type, "status": status, "message": message, "data": payload,
            })

    def log_event(self, run_id: str, event_type: str, step_id: str | None, message: str = "", data: dict[str, Any] | None = None):
        when = self.now()
        payload = data or {}
        with self._write_transaction() as con:
            cursor = con.execute(
                "INSERT INTO run_events(run_id, event_type, step_id, message, data_json, created_at) VALUES(?,?,?,?,?,?)",
                (run_id, event_type, step_id, message, self._json_text(payload), when),
            )
            self._append_audit(con, run_id, "RUN_EVENT", str(cursor.lastrowid), when, {
                "event_type": event_type, "step_id": step_id, "message": message, "data": payload,
            })

    def create_operator_message(
        self,
        run_id: str,
        event_type: str,
        message: str,
        *,
        step_id: str | None = None,
        severity: str = "info",
        data: dict[str, Any] | None = None,
    ) -> int:
        when = self.now()
        payload = data or {}
        with self._write_transaction() as con:
            cursor = con.execute(
                "INSERT INTO operator_messages(run_id, step_id, event_type, severity, message, occurred_at, data_json) VALUES(?,?,?,?,?,?,?)",
                (run_id, step_id, event_type, severity, message, when, self._json_text(payload)),
            )
            self._append_audit(con, run_id, "OPERATOR_MESSAGE", str(cursor.lastrowid), when, {
                "step_id": step_id, "event_type": event_type, "severity": severity, "message": message, "data": payload,
            })
            return int(cursor.lastrowid)

    def acknowledge_operator_message(self, message_id: int, operator: str, comment: str = "") -> bool:
        operator = operator.strip()
        if not operator:
            raise ValueError("Operator identity is required to acknowledge a message")
        when = self.now()
        with self._write_transaction() as con:
            cursor = con.execute(
                "UPDATE operator_messages SET acknowledged_at=?, acknowledged_by=?, acknowledgement_comment=? "
                "WHERE id=? AND acknowledged_at IS NULL",
                (when, operator, comment.strip(), message_id),
            )
            if cursor.rowcount == 1:
                row = con.execute("SELECT run_id FROM operator_messages WHERE id=?", (message_id,)).fetchone()
                self._append_audit(con, str(row[0]), "OPERATOR_ACK", str(message_id), when, {
                    "operator": operator, "comment": comment.strip(),
                })
            return cursor.rowcount == 1

    def save_checkpoint(
        self,
        run_id: str,
        step_id: str,
        status: str,
        runtime_variables: dict[str, Any] | None = None,
    ) -> int:
        when = self.now()
        variables = runtime_variables or {}
        with self._write_transaction() as con:
            cursor = con.execute(
                "INSERT INTO run_checkpoints(run_id, step_id, status, runtime_variables_json, created_at) VALUES(?,?,?,?,?)",
                (run_id, step_id, status, self._json_text(variables), when),
            )
            self._append_audit(con, run_id, "CHECKPOINT", str(cursor.lastrowid), when, {
                "step_id": step_id, "status": status, "runtime_variables": variables,
            })
            return int(cursor.lastrowid)

    def latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM run_checkpoints WHERE run_id=? ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["runtime_variables_json"] = json.loads(data.get("runtime_variables_json") or "{}")
        except json.JSONDecodeError:
            pass
        return data

    def log_write(self, run_id: str, tag: str, value: Any):
        when = self.now()
        with self._write_transaction() as con:
            cursor = con.execute(
                "INSERT INTO tag_writes(run_id, tag, value_json, created_at) VALUES(?,?,?,?)",
                (run_id, tag, self._json_text(value), when),
            )
            self._append_audit(con, run_id, "TAG_WRITE", str(cursor.lastrowid), when, {"tag": tag, "value": value})

    def log_read(self, run_id: str, tag: str, value: Any, quality: str = "Good", source: str = "", tag_timestamp: str = "", message: str = ""):
        when = self.now()
        with self._write_transaction() as con:
            cursor = con.execute(
                "INSERT INTO tag_reads(run_id, tag, value_json, quality, source, tag_timestamp, message, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, tag, self._json_text(value), quality, source, tag_timestamp, message, when),
            )
            self._append_audit(con, run_id, "TAG_READ", str(cursor.lastrowid), when, {
                "tag": tag, "value": value, "quality": quality, "source": source,
                "tag_timestamp": tag_timestamp, "message": message,
            })

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with closing(self._connect()) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM procedure_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as con:
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT * FROM procedure_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def run_steps(self, run_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM step_events WHERE run_id=? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def run_events(self, run_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM run_events WHERE run_id=? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def run_operator_messages(self, run_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM operator_messages WHERE run_id=? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def run_writes(self, run_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM tag_writes WHERE run_id=? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def run_reads(self, run_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM tag_reads WHERE run_id=? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in ("metadata_json", "data_json", "value_json", "runtime_variables_json"):
            if key in data and data[key]:
                try:
                    data[key] = json.loads(data[key])
                except json.JSONDecodeError:
                    pass
        return data
