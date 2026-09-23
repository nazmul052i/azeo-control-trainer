"""Compare the DMC FIR implementation with its scalar reference."""
# ruff: noqa: E402
from pathlib import Path
import json
import sys
import time
import numpy as np

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
(root / 'logs/diagnostics').mkdir(parents=True, exist_ok=True)
from azeo_control_trainer.core.strategy.blocks.dmc_blocks import DMCControllerBlock, N_CV, N_MV, N_DV

block = DMCControllerBlock('TIMING')
block._build_model()
block._past_du.fill(.1)
block._past_ddv.fill(.2)
def previous():
    result = np.zeros((block._P, N_CV))
    for k in range(block._P):
        for cv in range(N_CV):
            value = 0.0
            for t in range(block._N_model):
                index = k+t+1
                if index < block._N_model:
                    for mv in range(N_MV):
                        value += block._S[cv,index,mv]*block._past_du[mv,t]
            for t in range(block._N_model):
                index = k+t+1
                if index < block._N_model:
                    for dv in range(N_DV):
                        value += block._Sd[cv,index,dv]*block._past_ddv[dv,t]
            result[k,cv] = value
    return result
np.testing.assert_allclose(block._compute_free_response(), previous(), rtol=1e-12, atol=1e-12)
def best(fn):
    batches = []
    for _ in range(3):
        start = time.perf_counter()
        for _ in range(3):
            fn()
        batches.append((time.perf_counter()-start)*1000/3)
    return min(batches)
report = dict(prediction_horizon=block._P, model_horizon=block._N_model,
              previous_ms=best(previous), bounded_vectorized_ms=best(block._compute_free_response))
print(json.dumps(report, indent=2), flush=True)
(root / 'logs/diagnostics/dmc-fir-timing.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
