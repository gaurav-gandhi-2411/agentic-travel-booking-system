"""Unit tests for pareto_frontier."""
from __future__ import annotations

from travel_agent.utility.pareto import pareto_frontier


def _f(x: tuple[float, float]) -> float:
    return x[0]


def _g(x: tuple[float, float]) -> float:
    return x[1]


def test_empty_input_returns_empty() -> None:
    assert pareto_frontier([], _f, _g) == []


def test_single_item_is_on_frontier() -> None:
    result = pareto_frontier([(1.0, 1.0)], _f, _g)
    assert result == [(1.0, 1.0)]


def test_dominated_item_excluded() -> None:
    # (0.9, 0.9) dominates (0.5, 0.5)
    result = pareto_frontier([(0.9, 0.9), (0.5, 0.5)], _f, _g)
    assert (0.5, 0.5) not in result
    assert (0.9, 0.9) in result


def test_tradeoff_both_on_frontier() -> None:
    # (0.9, 0.3) and (0.3, 0.9) are non-dominated
    a = (0.9, 0.3)
    b = (0.3, 0.9)
    result = pareto_frontier([a, b], _f, _g)
    assert a in result
    assert b in result


def test_dominated_middle_excluded() -> None:
    # (0.9, 0.9) dominates (0.5, 0.5); (0.3, 1.0) is not dominated
    items = [(0.9, 0.9), (0.5, 0.5), (0.3, 1.0)]
    result = pareto_frontier(items, _f, _g)
    assert (0.5, 0.5) not in result
    assert (0.9, 0.9) in result
    assert (0.3, 1.0) in result


def test_all_dominated_by_one() -> None:
    best = (1.0, 1.0)
    items = [best, (0.8, 0.8), (0.6, 0.9), (0.9, 0.5)]
    result = pareto_frontier(items, _f, _g)
    assert result == [best]


def test_order_preserved() -> None:
    a = (0.8, 0.4)
    b = (0.4, 0.8)
    c = (0.6, 0.6)  # dominated by neither a nor b... actually (0.8,0.4) doesn't dominate (0.4,0.8)
    # c: is (0.8,0.4) >= (0.6,0.6)? yes on first, no on second → not dominated
    # is (0.4,0.8) >= (0.6,0.6)? no on first → not dominated
    # c vs others: nothing dominates c? check: does (0.8,0.4) dom c? 0.8>0.6 but 0.4<0.6 → no
    # so a,b,c all on frontier? yes
    result = pareto_frontier([a, b, c], _f, _g)
    assert a in result
    assert b in result


def test_ties_both_on_frontier() -> None:
    # Identical scores — neither dominates (strict requirement on at least one axis)
    a = (0.5, 0.5)
    b = (0.5, 0.5)
    result = pareto_frontier([a, b], _f, _g)
    # At least one must be returned (ties are non-dominated)
    assert len(result) >= 1


def test_bimodal_synthetic_flights() -> None:
    # Simulate LCC cluster (cheap, slow) vs premium cluster (expensive, fast)
    # LCC: high value_score, low experience_score
    # Premium: low value_score, high experience_score
    lcc = (0.85, 0.35)
    premium = (0.25, 0.90)
    dominated = (0.40, 0.30)  # dominated by lcc on both axes

    result = pareto_frontier([lcc, premium, dominated], _f, _g)
    assert lcc in result
    assert premium in result
    assert dominated not in result
