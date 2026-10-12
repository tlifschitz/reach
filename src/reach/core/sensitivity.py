"""Sensitivity analysis: how stable is each item's location when frequencies are uncertain?"""

from __future__ import annotations

import random
import statistics
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field

from reach.core.compile import Compiled
from reach.core.cost import Evaluator, compute_normalizers
from reach.core.model import Assignment, Problem
from reach.core.optim.annealing import SimulatedAnnealing
from reach.core.optim.base import CancelToken
from reach.core.optim.greedy import greedy_assignment


@dataclass(slots=True)
class ItemStability:
    item: str
    baseline_location: str
    modal_location: str
    modal_share: float
    """Share of perturbed runs in which the item landed in its most common location."""
    baseline_share: float
    """Share of perturbed runs in which the item landed where the baseline put it."""
    label: str
    """``robust``, ``sensitive``, ``unused`` or ``pinned``."""
    locations: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class SensitivityReport:
    runs: int
    perturbation: float
    seed: int
    iterations: int
    robust_threshold: float
    baseline_cost: float
    costs: list[float]
    """Cost of each perturbed solution, under its own perturbed frequencies."""
    baseline_costs: list[float]
    """Cost of the baseline solution under the same perturbed frequencies."""
    items: list[ItemStability]
    cost_summary: dict[str, float]
    stopped_early: bool = False


def perturbed_frequencies(problem: Problem, perturbation: float, seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    return {
        r.id: r.frequency * rng.uniform(1.0 - perturbation, 1.0 + perturbation)
        for r in problem.routines
    }


def _run(
    problem: Problem,
    baseline: Assignment,
    normalizers: Mapping[str, float],
    perturbation: float,
    seed: int,
    iterations: int,
) -> tuple[Assignment, float, float]:
    perturbed = problem.with_frequencies(perturbed_frequencies(problem, perturbation, seed))
    result = SimulatedAnnealing(seed=seed, iterations=iterations).optimize(
        perturbed, normalizers=normalizers
    )
    c = Compiled(perturbed)
    baseline_cost = Evaluator(c, normalizers).reset(c.encode(baseline))
    return result.assignment, result.cost, baseline_cost


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    q = (
        statistics.quantiles(ordered, n=4, method="inclusive")
        if len(ordered) > 1
        else [ordered[0]] * 3
    )
    return {
        "min": ordered[0],
        "q1": q[0],
        "median": q[1],
        "q3": q[2],
        "max": ordered[-1],
        "mean": statistics.fmean(ordered),
        "stdev": statistics.stdev(ordered) if len(ordered) > 1 else 0.0,
    }


def run_sensitivity(
    problem: Problem,
    *,
    runs: int = 20,
    perturbation: float = 0.3,
    seed: int = 0,
    iterations: int = 50_000,
    workers: int | None = None,
    robust_threshold: float = 0.8,
    baseline: Assignment | None = None,
    normalizers: Mapping[str, float] | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancel: CancelToken | None = None,
) -> SensitivityReport:
    """Re-optimize ``runs`` times with routine frequencies scaled by ``U(1 - x, 1 + x)``.

    Run ``k`` uses seed ``seed + k`` for both the perturbation and the optimizer, so the report
    is reproducible. ``workers=1`` runs inline; otherwise a process pool is used.
    """
    c = Compiled(problem)
    if normalizers is None:
        normalizers = compute_normalizers(c, c.encode(greedy_assignment(problem, c)))
    if baseline is None:
        baseline = (
            SimulatedAnnealing(seed=seed, iterations=iterations)
            .optimize(problem, normalizers=normalizers)
            .assignment
        )
    baseline_cost = Evaluator(c, normalizers).reset(c.encode(baseline))

    args = [
        (problem, baseline, dict(normalizers), perturbation, seed + k, iterations)
        for k in range(1, runs + 1)
    ]
    results: dict[int, tuple[Assignment, float, float]] = {}
    stopped = False
    if workers == 1 or runs <= 1:
        for k, a in enumerate(args):
            if cancel is not None and cancel.is_set():
                stopped = True
                break
            results[k] = _run(*a)
            if progress:
                progress(len(results), runs)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run, *a): k for k, a in enumerate(args)}
            for future in as_completed(futures):
                if future.cancelled():
                    continue
                results[futures[future]] = future.result()
                if progress:
                    progress(len(results), runs)
                if cancel is not None and cancel.is_set() and not stopped:
                    stopped = True
                    for f in futures:
                        f.cancel()

    ordered = [results[k] for k in sorted(results)]
    done = len(ordered)
    frequencies = problem.derived_frequencies()
    items: list[ItemStability] = []
    for item_id in problem.items:
        counts = Counter(a[item_id] for a, _, _ in ordered)
        base = baseline[item_id]
        modal, modal_count = counts.most_common(1)[0] if counts else (base, 0)
        modal_share = modal_count / done if done else 1.0
        base_share = counts.get(base, 0) / done if done else 1.0
        if item_id in problem.pins:
            label = "pinned"
        elif frequencies.get(item_id, 0.0) <= 0:
            label = "unused"
        elif base_share >= robust_threshold:
            label = "robust"
        else:
            label = "sensitive"
        items.append(
            ItemStability(item_id, base, modal, modal_share, base_share, label, dict(counts))
        )
    costs = [cost for _, cost, _ in ordered]
    return SensitivityReport(
        runs=done,
        perturbation=perturbation,
        seed=seed,
        iterations=iterations,
        robust_threshold=robust_threshold,
        baseline_cost=baseline_cost,
        costs=costs,
        baseline_costs=[b for _, _, b in ordered],
        items=items,
        cost_summary=_summary(costs),
        stopped_early=stopped,
    )
