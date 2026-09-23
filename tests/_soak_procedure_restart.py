"""Procedure worker restart, memory and audit-integrity probe."""
# ruff: noqa: E402
import gc
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tracemalloc

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
(root / 'logs/diagnostics').mkdir(parents=True, exist_ok=True)
from azeo_control_trainer.core.procedures.authoring import ProcedureDraft
from azeo_control_trainer.core.procedures.runtime import ProcedureRun

draft = ProcedureDraft.new()
draft.data['variables'] = [dict(name='result', data_type='float', value=0)]
draft.data['steps'] = [dict(id='calculate', type='calculate', variable='result', expression='2 + 3'),
                       dict(id='done', type='complete')]
definition = draft.definition()
with tempfile.TemporaryDirectory(prefix='azeo-pa-soak-') as directory:
    run = ProcedureRun(Path(directory) / 'history.sqlite')
    def cycles(count):
        for _ in range(count):
            run.observe(0, {})
            run.start(definition, actor='Runtime soak')
            deadline = time.monotonic() + 10
            while run.active:
                assert time.monotonic() < deadline, 'Procedure did not finish'
                time.sleep(.005)
            assert run.result.status.value == 'COMPLETE'
            assert any(event['kind'] == 'finished' for event in run.drain())
            assert run._engine is None
    cycles(3)
    tracemalloc.start()
    cycles(10)
    gc.collect()
    before = tracemalloc.get_traced_memory()[0]
    cycles(30)
    gc.collect()
    after, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert after - before < 262144, (before, after)
    assert run.close()
    assert run.store.verify_audit_chain()[0]
    assert not any(thread.name == 'azeo-advisory-procedure' for thread in threading.enumerate())
    report = dict(completed_runs=43, measured_runs=30, retained_python_growth_bytes=after-before,
                  peak_python_bytes=peak, active_procedure_threads=0,
                  pending_events=run.events.qsize(), audit_chain_valid=True)
    print(json.dumps(report, indent=2), flush=True)
    (root / 'logs/diagnostics/procedure-restart-soak.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
