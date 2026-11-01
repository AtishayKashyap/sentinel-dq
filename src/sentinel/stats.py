"""Pure statistical helpers. Kept dependency-free (no numpy) and side-effect free so
they can be property-tested in isolation."""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence

# Floor applied to empty bins. Without it, PSI is infinite whenever a bin is empty in
# either distribution; 1e-4 is the convention used in credit-risk model monitoring.
PSI_EPSILON = 1e-4


def proportions(counts: Sequence[float]) -> list[float]:
    total = float(sum(counts))
    if total <= 0:
        return [0.0 for _ in counts]
    return [c / total for c in counts]


def psi(expected: Sequence[float], actual: Sequence[float]) -> float:
    """Population Stability Index between two binned distributions (given as counts or
    proportions over identical bins).

    Rule of thumb: < 0.1 stable, 0.1-0.2 moderate shift, > 0.2 significant shift.
    """
    if len(expected) != len(actual):
        raise ValueError("expected and actual must have the same number of bins")
    if not expected:
        raise ValueError("at least one bin is required")
    e = [max(p, PSI_EPSILON) for p in proportions(expected)]
    a = [max(p, PSI_EPSILON) for p in proportions(actual)]
    return sum((ai - ei) * math.log(ai / ei) for ei, ai in zip(e, a, strict=True))


def zscore(value: float, history: Sequence[float]) -> float | None:
    """Z-score of `value` relative to `history` (sample std-dev).

    Returns None when there is too little history or zero variance with an equal value;
    returns +/-inf when history is constant but the value differs (a real anomaly).
    """
    n = len(history)
    if n < 2:
        return None
    mean = sum(history) / n
    var = sum((x - mean) ** 2 for x in history) / (n - 1)
    std = math.sqrt(var)
    if std == 0:
        if value == mean:
            return 0.0
        return math.inf if value > mean else -math.inf
    return (value - mean) / std


def edges_from_tiles(tiles: Sequence[tuple[float, float]]) -> list[float]:
    """Turn per-tile (min, max) pairs, ordered by tile, into strictly increasing inner
    edges. Each edge sits midway between one tile's max and the next tile's min; ties
    across tiles (heavy repeated values) collapse into a single edge."""
    if not tiles:
        raise ValueError("at least one tile is required")
    edges: list[float] = []
    for (_, hi), (lo, _) in itertools.pairwise(tiles):
        e = (hi + lo) / 2
        if not edges or e > edges[-1]:
            edges.append(e)
    return edges or [tiles[0][0]]
