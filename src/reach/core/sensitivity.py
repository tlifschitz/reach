"""Sensitivity analysis: how stable is each item's location when frequencies are uncertain?

Two kinds of re-optimization are compared against one baseline layout:

- **noise** runs keep the frequencies and change only the optimizer's seed. Whatever moves here
  moves because the optimizer cannot tell the alternatives apart, not because of the estimates.
- **perturbed** runs scale every routine frequency by a random factor as well.

An item that is stable under noise but not under perturbation genuinely depends on the
frequency estimates. Stability is reported for the exact location and for the *area and level*
(interchangeable neighbouring locations count as the same place).
"""

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

_NOISE_SEED_OFFSET = 100_003


@dataclass(slots=True)
class Shares:
    """Where one item ended up across a set of runs, relative to the baseline layout."""

    exact: float
    """Share of runs in the same location as the baseline."""
    area: float
    """Share of runs in the same area and level as the baseline."""
    modal_location: str
    modal_share: float
    locations: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ItemStability:
    item: str
    baseline_location: str
    baseline_area: str
    label: str
    """``robust``, ``noisy``, ``sensitive``, ``unused`` or ``pinned``."""
    noise: Shares | None = None
    perturbed: Shares | None = None


@dataclass(slots=True)
class RunSet:
    runs: int
    costs: list[float]
    summary: dict[str, float]
    exact_stability: float
    """Mean share of runs in which an item stays in its baseline location (items that matter)."""
    area_stability: float
    """The same, counting any location in the same area and level as staying."""


@dataclass(slots=True)
class SensitivityReport:
    perturbation: float
    seed: int
    iterations: int
    restarts: int
    robust_threshold: float
    baseline_cost: float
    noise: RunSet | None
    perturbed: RunSet | None
    baseline_costs: list[float]
    """Cost of the baseline layout under each perturbed set of frequencies."""
    items: list[ItemStability]
    counts: dict[str, int]
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
    restarts: int,
) -> tuple[Assignment, float, float]:
    if perturbation > 0:
        problem = problem.with_frequencies(perturbed_frequencies(problem, perturbation, seed))
    result = SimulatedAnnealing(seed=seed, iterations=iterations, restarts=restarts).optimize(
        problem, normalizers=normalizers
    )
    c = Compiled(problem)
    baseline_cost = Evaluator(c, normalizers).reset(c.encode(baseline))
    return result.assignment, result.cost, baseline_cost


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    if len(ordered) > 1:
        q = statistics.quantiles(ordered, n=4, method="inclusive")
    else:
        q = [ordered[0]] * 3
    return {
        "min": ordered[0],
        "q1": q[0],
        "median": q[1],
        "q3": q[2],
        "max": ordered[-1],
        "mean": statistics.fmean(ordered),
        "stdev": statistics.stdev(ordered) if len(ordered) > 1 else 0.0,
    }


def area_key(problem: Problem, location: str) -> tuple[str, int]:
    loc = problem.locations[location]
    return (loc.area or loc.id, loc.level)


def _shares(problem: Problem, item: str, base: str, runs: list[Assignment]) -> Shares:
    counts = Counter(a[item] for a in runs)
    n = len(runs)
    key = area_key(problem, base)
    same_area = sum(k for loc, k in counts.items() if area_key(problem, loc) == key)
    modal, modal_count = counts.most_common(1)[0]
    return Shares(counts.get(base, 0) / n, same_area / n, modal, modal_count / n, dict(counts))


def run_sensitivity(
    problem: Problem,
    *,
    runs: int = 20,
    noise_runs: int | None = None,
    perturbation: float = 0.3,
    seed: int = 0,
    iterations: int = 200_000,
    restarts: int = 1,
    workers: int | None = None,
    robust_threshold: float = 0.8,
    baseline: Assignment | None = None,
    normalizers: Mapping[str, float] | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancel: CancelToken | None = None,
) -> SensitivityReport:
    """Re-optimize with new seeds (``noise_runs``) and with perturbed frequencies (``runs``).

    ``noise_runs`` defaults to ``runs``. ``runs=0`` is the baseline mode: optimizer noise only.
    Every run uses the given ``iterations`` and ``restarts``. Seeds derive from ``seed``, so the
    report is reproducible. ``workers=1`` runs inline; otherwise a process pool is used.
    """
    noise_runs = runs if noise_runs is None else noise_runs
    c = Compiled(problem)
    if normalizers is None:
        normalizers = compute_normalizers(c, c.encode(greedy_assignment(problem, c)))
    if baseline is None:
        baseline = (
            SimulatedAnnealing(seed=seed, iterations=iterations, restarts=restarts)
            .optimize(problem, normalizers=normalizers)
            .assignment
        )
    baseline_cost = Evaluator(c, normalizers).reset(c.encode(baseline))

    jobs: list[tuple[str, tuple]] = []
    for k in range(1, noise_runs + 1):
        args = (problem, baseline, dict(normalizers), 0.0, seed + _NOISE_SEED_OFFSET + k)
        jobs.append(("noise", (*args, iterations, restarts)))
    for k in range(1, runs + 1):
        args = (problem, baseline, dict(normalizers), perturbation, seed + k)
        jobs.append(("perturbed", (*args, iterations, restarts)))

    results: dict[int, tuple[Assignment, float, float]] = {}
    stopped = False
    total = len(jobs)
    if workers == 1 or total <= 1:
        for n, (_, args) in enumerate(jobs):
            if cancel is not None and cancel.is_set():
                stopped = True
                break
            results[n] = _run(*args)
            if progress:
                progress(len(results), total)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run, *args): n for n, (_, args) in enumerate(jobs)}
            for future in as_completed(futures):
                if future.cancelled():
                    continue
                results[futures[future]] = future.result()
                if progress:
                    progress(len(results), total)
                if cancel is not None and cancel.is_set() and not stopped:
                    stopped = True
                    for f in futures:
                        f.cancel()

    by_kind: dict[str, list[tuple[Assignment, float, float]]] = {"noise": [], "perturbed": []}
    for n in sorted(results):
        by_kind[jobs[n][0]].append(results[n])

    frequencies = problem.derived_frequencies()
    matters = {
        item_id
        for item_id in problem.items
        if item_id not in problem.pins
        and (frequencies[item_id] > 0 or c.is_occasional[c.item_index[item_id]])
    }
    items: list[ItemStability] = []
    for item_id in problem.items:
        base = baseline[item_id]
        area, level = area_key(problem, base)
        noise = perturbed = None
        if by_kind["noise"]:
            noise = _shares(problem, item_id, base, [a for a, _, _ in by_kind["noise"]])
        if by_kind["perturbed"]:
            perturbed = _shares(problem, item_id, base, [a for a, _, _ in by_kind["perturbed"]])
        if item_id in problem.pins:
            label = "pinned"
        elif item_id not in matters:
            label = "unused"
        elif noise is not None and noise.area < robust_threshold:
            label = "noisy"
        elif perturbed is not None and perturbed.area < robust_threshold:
            label = "sensitive"
        else:
            label = "robust"
        items.append(
            ItemStability(item_id, base, f"{area} · level {level}", label, noise, perturbed)
        )

    def run_set(kind: str) -> RunSet | None:
        got = by_kind[kind]
        if not got:
            return None
        relevant = [getattr(s, kind) for s in items if s.item in matters]
        costs = [cost for _, cost, _ in got]
        return RunSet(
            len(got),
            costs,
            summarize(costs),
            statistics.fmean(s.exact for s in relevant) if relevant else 1.0,
            statistics.fmean(s.area for s in relevant) if relevant else 1.0,
        )

    labels = ("robust", "noisy", "sensitive", "unused", "pinned")
    return SensitivityReport(
        perturbation=perturbation,
        seed=seed,
        iterations=iterations,
        restarts=restarts,
        robust_threshold=robust_threshold,
        baseline_cost=baseline_cost,
        noise=run_set("noise"),
        perturbed=run_set("perturbed"),
        baseline_costs=[b for _, _, b in by_kind["perturbed"]],
        items=items,
        counts={label: sum(1 for s in items if s.label == label) for label in labels},
        stopped_early=stopped,
    )
