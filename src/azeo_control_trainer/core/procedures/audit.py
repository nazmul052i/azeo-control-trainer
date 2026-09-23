"""Trainer's Windows persistence adapter; the imported audit contract stays intact."""
import time
import threading
from contextlib import contextmanager

from azeo_control_trainer.core.pa_designer.storage.sqlite_store import SQLiteStore


class ProcedureStore(SQLiteStore):
    flow_event_fn = None

    def log_event(self, run_id, event_type, step_id, message="", data=None):
        result = super().log_event(run_id, event_type, step_id, message, data)
        callback = self._flow_handlers.get(run_id) or self.flow_event_fn
        if callback and event_type.startswith(("FLOW_", "PARALLEL_", "SUBPROCEDURE_")):
            callback(event_type, dict(data or {}, step=step_id, message=message))
        return result

    def __init__(self, path):
        self._transaction = threading.local()
        self._flow_handlers = {}
        self._flow_lock = threading.Lock()
        super().__init__(path)

    def set_flow_handler(self, run_id, callback):
        with self._flow_lock:
            if callback is None:
                self._flow_handlers.pop(run_id, None)
            else:
                self._flow_handlers[run_id] = callback

    def _init_db(self):
        super()._init_db()
        # Coverage checks look up every operational row in the signed chain.
        # Without this index, reopening a scan-heavy history takes minutes.
        with self._write_transaction() as connection:
            connection.execute("CREATE INDEX IF NOT EXISTS audit_chain_record_idx ON audit_chain(record_type, record_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS procedure_tuning_idx ON run_events(step_id, event_type, id)")
            connection.execute("CREATE INDEX IF NOT EXISTS procedure_runs_by_procedure_idx ON procedure_runs(procedure_id, started_at)")

    @contextmanager
    def _write_transaction(self):
        connection = getattr(self._transaction, "connection", None)
        if connection is not None:
            yield connection
        else:
            with super()._write_transaction() as connection:
                yield connection

    @contextmanager
    def observation_batch(self):
        # One multi-condition observation is one durable transaction. Every
        # read retains its audit row/hash, with one atomic anchor publication.
        # Per-tag fsyncs made a 40-condition observation take seconds on Windows.
        with super()._write_transaction() as connection:
            self._transaction.connection = connection
            try:
                yield
            finally:
                self._transaction.connection = None

    def _write_audit_anchor(self, sequence, record_hash):
        # Windows indexers can briefly hold the previous anchor without
        # FILE_SHARE_DELETE. Retry its atomic publication, never skip evidence.
        for attempt in range(6):
            try:
                return super()._write_audit_anchor(sequence, record_hash)
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.02 * (attempt + 1))
