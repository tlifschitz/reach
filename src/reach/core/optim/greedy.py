"""Greedy construction of a feasible assignment, most frequently used items first."""

from __future__ import annotations

import time
from collections.abc import Mapping

from reach.core.compile import OP_SET_STATE, OP_STORE, OP_TAKE, Compiled
from reach.core.cost import Evaluator, compute_normalizers
from reach.core.model import Assignment, Problem
from reach.core.optim.base import (
    CancelToken,
    InfeasibleError,
    OptimizationResult,
    Optimizer,
    ProgressCallback,
)
from reach.core.optim.feasibility import Unplaced, diagnose

_EFFORT_SECONDS = 2.0
"""Rough exchange rate used only to rank candidate locations."""


def _anchors(c: Compiled) -> tuple[list[dict[int, float]], list[float]]:
    """For each item: the fixed points it is carried from and to (weighted), and its uses."""
    anchors: list[dict[int, float]] = [{} for _ in range(c.n_items)]
    uses = [0.0] * c.n_items
    for routine in c.routines:
        steps = routine.steps
        fixed_before: list[int] = []
        last = routine.start
        for op, _item, loc, _ in steps:
            fixed_before.append(last)
            if op not in (OP_TAKE, OP_STORE, OP_SET_STATE):
                last = loc
        nxt = -1
        fixed_after = [-1] * len(steps)
        for n in range(len(steps) - 1, -1, -1):
            fixed_after[n] = nxt
            op, _item, loc, _ = steps[n]
            if op not in (OP_TAKE, OP_STORE, OP_SET_STATE):
                nxt = loc
        for n, (op, item, _loc, _) in enumerate(steps):
            if op in (OP_TAKE, OP_STORE):
                uses[item] += routine.frequency
                for point in (fixed_before[n], fixed_after[n]):
                    if point >= 0:
                        anchors[item][point] = anchors[item].get(point, 0.0) + routine.frequency
    return anchors, uses


def _location_seconds(c: Compiled) -> list[float]:
    """Seconds spent on one visit to each location, ignoring walking and the item."""
    out = []
    default_blocking = [o.default_state == o.blocking_state for o in c.problem.obstructions]
    for loc in range(c.n_locs):
        seconds = c.open_time[loc]
        for o in c.blockers[loc]:
            if default_blocking[o]:
                seconds += c.obs_clear_time[o] + c.obs_restore_time[o]
        out.append(seconds)
    return out


def greedy_assignment(problem: Problem, compiled: Compiled | None = None) -> Assignment:
    """A feasible assignment, or :class:`InfeasibleError` with an explanation."""
    c = compiled or Compiled(problem)
    report = diagnose(problem, c)
    if not report.feasible:
        raise InfeasibleError(report)

    anchors, uses = _anchors(c)
    loc_seconds = _location_seconds(c)
    items = c.problem.items
    free_items = [i for i in range(c.n_items) if c.pinned[i] < 0]
    by_frequency = sorted(
        free_items,
        key=lambda i: (
            -c.item_frequency[i],
            -items[c.item_ids[i]].estimated_frequency,
            -c.total_volume[i],
            c.item_ids[i],
        ),
    )
    # Fallback order when the frequency order paints itself into a corner: the classic packing
    # heuristic, most constrained and largest first. Annealing recovers the lost quality.
    by_constraint = sorted(
        free_items, key=lambda i: (len(c.domains[i]), -c.volume([i]), c.item_ids[i])
    )

    unplaced: list[int] = []
    for order in (by_frequency, by_constraint):
        assign, contents, unplaced = _construct(c, order, anchors, uses, loc_seconds)
        if not unplaced:
            return c.decode(assign)

    for i in unplaced:
        room = sorted(((_free(c, contents, loc), loc) for loc in c.domains[i]), reverse=True)[:3]
        report.unplaced.append(
            Unplaced(
                c.item_ids[i], c.volume([i]), [(c.loc_ids[loc], max(f, 0.0)) for f, loc in room]
            )
        )
    raise InfeasibleError(report)


def _free(c: Compiled, contents: list[list[int]], loc: int) -> float:
    cap = c.cap_volume[loc]
    return float("inf") if cap is None else cap - c.volume(contents[loc])


def _construct(
    c: Compiled,
    order: list[int],
    anchors: list[dict[int, float]],
    uses: list[float],
    loc_seconds: list[float],
) -> tuple[list[int], list[list[int]], list[int]]:
    """Place items in ``order``. Returns the assignment, the contents and what did not fit."""
    assign = [-1] * c.n_items
    contents: list[list[int]] = [[] for _ in range(c.n_locs)]
    for i, loc in enumerate(c.pinned):
        if loc >= 0:
            assign[i] = loc
            contents[loc].append(i)

    def free(loc: int) -> float:
        return _free(c, contents, loc)

    def score(i: int, loc: int) -> float:
        walk = sum(w * c.dist[loc][p] for p, w in anchors[i].items()) / c.speed
        visit = loc_seconds[loc] + _EFFORT_SECONDS * c.level_penalty[loc] * c.weight_mult[i]
        return walk + uses[i] * visit

    def occasional(i: int, loc: int) -> float:
        walk = 2.0 * c.dist[c.origin][loc] / c.speed
        return walk + loc_seconds[loc] + _EFFORT_SECONDS * c.level_penalty[loc] * c.weight_mult[i]

    def candidates(i: int) -> list[int]:
        return [loc for loc in c.domains[i] if c.location_ok(loc, [*contents[loc], i])]

    unplaced: list[int] = []
    for i in order:
        options = candidates(i)
        if not options:
            unplaced.append(i)
            continue
        if uses[i] > 0:
            loc = min(options, key=lambda loc: (score(i, loc), -free(loc), loc))
        elif c.is_occasional[i]:
            loc = min(options, key=lambda loc: (occasional(i, loc), -free(loc), loc))
        else:
            loc = max(options, key=lambda loc: (free(loc), -loc))
        assign[i] = loc
        contents[loc].append(i)

    # Repair: make room by evicting less-used items to other locations they are allowed in.
    still = [i for i in unplaced if not _make_room(c, i, assign, contents, candidates, free)]
    return assign, contents, still


def _make_room(c: Compiled, i: int, assign, contents, candidates, free) -> bool:
    for loc in sorted(c.domains[i], key=lambda loc: -free(loc)):
        moved: list[tuple[int, int]] = []
        residents = sorted(
            (j for j in contents[loc] if c.pinned[j] < 0),
            key=lambda j: (c.item_frequency[j], -c.total_volume[j]),
        )
        ok = c.location_ok(loc, [*contents[loc], i])
        for j in residents:
            if ok:
                break
            contents[loc].remove(j)
            options = [other for other in candidates(j) if other != loc]
            if not options:
                contents[loc].append(j)
                continue
            target = max(options, key=free)
            assign[j] = target
            contents[target].append(j)
            moved.append((j, target))
            ok = c.location_ok(loc, [*contents[loc], i])
        if ok:
            assign[i] = loc
            contents[loc].append(i)
            return True
        for j, target in moved:
            contents[target].remove(j)
            contents[loc].append(j)
            assign[j] = loc
    return False


class GreedyOptimizer(Optimizer):
    """The constructive heuristic on its own. Mostly useful as a baseline."""

    name = "greedy"

    def optimize(
        self,
        problem: Problem,
        *,
        initial: Assignment | None = None,
        normalizers: Mapping[str, float] | None = None,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> OptimizationResult:
        started = time.perf_counter()
        c = Compiled(problem)
        assignment = greedy_assignment(problem, c)
        assign = c.encode(assignment)
        norm = dict(normalizers) if normalizers else compute_normalizers(c, assign)
        cost = Evaluator(c, norm).reset(assign)
        return OptimizationResult(
            assignment=assignment,
            cost=cost,
            initial_cost=cost,
            normalizers=norm,
            elapsed_s=time.perf_counter() - started,
            optimizer=self.name,
        )
