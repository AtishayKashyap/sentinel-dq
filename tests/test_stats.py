import itertools
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sentinel.stats import edges_from_tiles, proportions, psi, zscore

counts = st.lists(st.integers(min_value=0, max_value=10_000), min_size=2, max_size=20)


@given(counts)
def test_psi_identical_is_zero(c: list[int]) -> None:
    assert psi(c, c) == pytest.approx(0.0, abs=1e-12)


@given(st.data())
def test_psi_non_negative_and_symmetric(data: st.DataObject) -> None:
    a = data.draw(counts)
    b = data.draw(st.lists(st.integers(0, 10_000), min_size=len(a), max_size=len(a)))
    assert psi(a, b) >= -1e-12
    assert psi(a, b) == pytest.approx(psi(b, a))


@given(counts, st.integers(min_value=1, max_value=50))
def test_psi_scale_invariant(c: list[int], k: int) -> None:
    assert psi(c, [x * k for x in c]) == pytest.approx(0.0, abs=1e-9)


def test_psi_known_value_and_errors() -> None:
    # (0.5-0.25)ln(2) + (0.5-0.75)ln(2/3)
    assert psi([25, 75], [50, 50]) == pytest.approx(0.25 * math.log(2) - 0.25 * math.log(2 / 3))
    with pytest.raises(ValueError):
        psi([1], [1, 2])
    with pytest.raises(ValueError):
        psi([], [])


def test_proportions_zero_total() -> None:
    assert proportions([0, 0]) == [0.0, 0.0]


@given(st.lists(st.floats(-1e6, 1e6), min_size=2, max_size=50), st.floats(-1e6, 1e6))
def test_zscore_sign(history: list[float], value: float) -> None:
    z = zscore(value, history)
    assert z is not None
    mean = sum(history) / len(history)
    if value > mean + 1e-6:
        assert z > 0
    if value < mean - 1e-6:
        assert z < 0


def test_zscore_edge_cases() -> None:
    assert zscore(5, [5]) is None
    assert zscore(5, [5, 5]) == 0
    assert zscore(6, [5, 5]) == math.inf
    assert zscore(4, [5, 5]) == -math.inf
    assert zscore(7, [5, 6, 7]) == pytest.approx(1.0)


@given(st.lists(st.floats(-1e6, 1e6), min_size=1, max_size=200), st.integers(2, 20))
def test_tile_edges_strictly_increasing(values: list[float], k: int) -> None:
    v = sorted(values)
    size = -(-len(v) // k)
    tiles = [(c[0], c[-1]) for c in (v[i : i + size] for i in range(0, len(v), size))]
    e = edges_from_tiles(tiles)
    assert e and all(a < b for a, b in itertools.pairwise(e))


def test_tile_edges_examples() -> None:
    assert edges_from_tiles([(1, 2), (4, 5), (7, 9)]) == [3, 6]
    assert edges_from_tiles([(3, 3), (3, 3)]) == [3]  # constant column
    assert edges_from_tiles([(1, 1)]) == [1]
    with pytest.raises(ValueError):
        edges_from_tiles([])
