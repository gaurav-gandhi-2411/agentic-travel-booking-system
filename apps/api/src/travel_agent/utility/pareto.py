"""Pareto frontier extraction for two-objective optimisation.

An option A *dominates* option B when A is at least as good as B on ALL
objectives and strictly better on at least one.  The Pareto frontier is the
set of options not dominated by any other option.

Reference: ADR-0006 (scoring model).
"""
from __future__ import annotations

from collections.abc import Callable

import structlog

_logger = structlog.get_logger(__name__)


def pareto_frontier[T](
    options: list[T],
    score_a: Callable[[T], float],
    score_b: Callable[[T], float],
) -> list[T]:
    """Return the Pareto-optimal subset of *options* on two objectives.

    Args:
        options: candidate items to filter.
        score_a: first objective score function (higher is better).
        score_b: second objective score function (higher is better).

    Returns:
        Non-dominated items; order is preserved from *options*.
    """
    if not options:
        return []

    scored: list[tuple[float, float, T]] = [
        (score_a(opt), score_b(opt), opt) for opt in options
    ]

    frontier: list[tuple[float, float, T]] = []
    for sa, sb, opt in scored:
        dominated = False
        for fa, fb, _ in frontier:
            # fa,fb dominates sa,sb if fa >= sa AND fb >= sb AND (fa > sa OR fb > sb)
            if fa >= sa and fb >= sb and (fa > sa or fb > sb):
                dominated = True
                break
        if not dominated:
            # Remove any existing frontier members now dominated by this point
            frontier = [
                (fa, fb, o)
                for fa, fb, o in frontier
                if not (sa >= fa and sb >= fb and (sa > fa or sb > fb))
            ]
            frontier.append((sa, sb, opt))

    result = [opt for _, _, opt in frontier]
    _logger.debug("pareto_frontier", input_size=len(options), frontier_size=len(result))
    return result
