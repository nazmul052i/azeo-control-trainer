"""Bounded controller state and repeated-scan timing probe."""
# ruff: noqa: E402
import gc
import json
from pathlib import Path
import sys
import threading
import time
import tracemalloc

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
(root / 'logs/diagnostics').mkdir(parents=True, exist_ok=True)
from azeo_control_trainer.core.strategy.blocks.action_block import ActionBlock
from azeo_control_trainer.core.strategy.blocks.expression_block import ExpressionBlock
from azeo_control_trainer.core.strategy.blocks.filter_blocks import DeadtimeBlock
from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
from azeo_control_trainer.core.strategy.engine.compiler import CompiledStrategy
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore

graph = StrategyGraph('RESOURCE-SOAK')
action = ActionBlock('ACT')
action.config.params.update(SCRIPT_MODE=2, SCRIPT="OUT1 = ewma(state, 'pv', IN1, .2)\nOUT2 = avg(buffer_push(state, 'history', IN1, 100))")
expression = ExpressionBlock('CALC')
expression.config.params['expression'] = 'sqrt(IN1 ** 2 + IN2 ** 2)'
delay = DeadtimeBlock('DELAY')
delay.config.params['DELAY'] = 2.0
for block in (action, expression, delay):
    graph.add_block(block)
runtime = StrategyRuntime()
runtime.load(CompiledStrategy(graph, list(graph.blocks), [], []), DataBridge(SharedDataStore()))
runtime.go_online()

def scans(count):
    for index in range(count):
        action.inputs['IN1'].value = float(index % 100)
        runtime.execute_scan(.1)

tracemalloc.start()
scans(2000)
gc.collect()
before = tracemalloc.get_traced_memory()[0]
scans(20000)
gc.collect()
after, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
assert after - before < 262144, (before, after)
assert not action.get_output('ERROR')
assert not expression.get_output('ERROR')
durations = []
for _ in range(3):
    start = time.perf_counter()
    scans(1000)
    durations.append((time.perf_counter() - start) * 1000 / 1000)
assert min(durations) < 10, durations
threads = len(threading.enumerate())
report = dict(scans=22000, retained_python_growth_bytes=after-before,
              peak_python_bytes=peak, best_scan_ms=min(durations), batches_ms=durations,
              state_keys=len(action._user_state), history_samples=len(action._user_state['history']),
              delay_samples=len(delay._buffer), active_threads=threads)
print(json.dumps(report, indent=2), flush=True)
(root / 'logs/diagnostics/runtime-safety-soak.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
