"""Display envelopes only; measurements and exports retain raw samples."""
from __future__ import annotations

import numpy as np


def plot_indices(values, qualities, budget):
    """Keep endpoints, bucket extrema and both sides of quality/finite gaps.

    Pathological alternating quality can exceed the budget deliberately: a
    discontinuity must never disappear just to make a curve cheaper to paint.
    """
    length = len(values)
    budget = max(8, int(budget))
    if length <= budget:
        return np.arange(length)
    width = max(1, int(np.ceil(length / max(1, budget // 4))))
    full = length // width * width
    blocks = np.asarray(values[:full]).reshape(-1, width)
    finite = np.isfinite(blocks)
    origins = np.arange(len(blocks)) * width
    lows = origins + np.where(finite, blocks, np.inf).argmin(axis=1)
    highs = origins + np.where(finite, blocks, -np.inf).argmax(axis=1)
    changes = np.flatnonzero((np.isfinite(values[1:]) != np.isfinite(values[:-1]))
                             | (qualities[1:] != qualities[:-1])) + 1
    # The final partial bucket has fewer than width samples; retain its extrema
    # with the same rule without padding the complete array.
    tail = np.asarray(values[full:])
    tail_finite = np.isfinite(tail)
    extra = []
    if tail.size:
        extra = [full, length - 1,
                 full + np.where(tail_finite, tail, np.inf).argmin(),
                 full + np.where(tail_finite, tail, -np.inf).argmax()]
    return np.unique(np.concatenate((origins, origins + width - 1, lows, highs,
                                     changes - 1, changes, extra, [0, length - 1])).astype(int))
