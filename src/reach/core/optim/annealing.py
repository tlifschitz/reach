"""Simulated annealing over item-to-location assignments."""

from __future__ import annotations

import math
import random
import time
from collections.abc import Mapping

from reach.core.compile import OP_STORE, OP_TAKE, Compiled
from reach.core.constraints import check_assignment
from reach.core.cost import Evaluator, compute_normalizers
from reach.core.model import Assignment, Problem
from reach.core.optim.base import (
    CancelToken,
    OptimizationResult,
    Optimizer,
    ProgressCallback,
    ProgressEvent,
)
from reach.core.optim.greedy import greedy_assignment

Change = list[tuple[int, int]]
"""``(item, new location)`` pairs."""

_CHECK_EVERY = 256
_RESYNC_EVERY = 1 << 16


class SimulatedAnnealing(Optimizer):
    """Local search with relocate, swap, nest-group, routine-cluster and exchange moves.

    Every state visited is feasible, so the search can be stopped at any time. With an iteration
    budget the result depends only on the problem and the seed. With ``time_limit_s`` the
    iteration budget is ignored and the result also depends on machine speed.

    When ``normalizers`` is not given they are taken from the initial solution, which is the
    greedy one unless ``initial`` is passed.
    """

    name = "simulated_annealing"

    def __init__(
        self,
        *,
        seed: int = 0,
        iterations: int = 200_000,
        time_limit_s: float | None = None,
        initial_acceptance: float = 0.1,
        final_temperature_ratio: float = 1e-3,
        move_weights: tuple[float, float, float, float, float] = (0.40, 0.30, 0.10, 0.10, 0.10),
        progress_interval_s: float = 0.25,
        restarts: int = 1,
    ) -> None:
        self.seed = seed
        self.iterations = max(0, iterations)
        self.time_limit_s = time_limit_s
        self.initial_acceptance = initial_acceptance
        self.final_temperature_ratio = final_temperature_ratio
        self.move_weights = move_weights
        self.progress_interval_s = progress_interval_s
        self.restarts = max(1, restarts)

    def optimize(
        self,
        problem: Problem,
        *,
        initial: Assignment | None = None,
        normalizers: Mapping[str, float] | None = None,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> OptimizationResult:
        if self.restarts > 1:
            return self._multi_start(problem, initial, normalizers, progress, cancel)
        started = time.perf_counter()
        c = Compiled(problem)
        if initial is None:
            initial = greedy_assignment(problem, c)
        else:
            violations = check_assignment(problem, initial, c)
            if violations:
                raise ValueError("the initial assignment is infeasible: " + violations[0].message)
        assign = c.encode(initial)
        norm = dict(normalizers) if normalizers else compute_normalizers(c, assign)
        ev = Evaluator(c, norm)
        search = _Search(c, ev, assign, random.Random(self.seed), self.move_weights)

        initial_cost = ev.reset(assign)
        best_cost = initial_cost
        best = assign[:]
        history: list[tuple[int, float]] = [(0, best_cost)]

        timed = self.time_limit_s is not None
        total = None if timed else self.iterations
        t0 = search.calibrate(self.initial_acceptance) if (timed or self.iterations) else 0.0
        ratio = self.final_temperature_ratio
        alpha = ratio ** (1.0 / self.iterations) if self.iterations else 1.0
        temperature = t0
        hist_every = max(_CHECK_EVERY, (self.iterations // 200) if not timed else 4096)

        rng = search.rng
        last_report = started
        best_sent = False
        stopped = False
        it = 0
        while True:
            if it % _CHECK_EVERY == 0:
                now = time.perf_counter()
                elapsed = now - started
                if timed:
                    fraction = min(1.0, elapsed / self.time_limit_s) if self.time_limit_s else 1.0
                    temperature = t0 * ratio**fraction
                    done = fraction >= 1.0
                else:
                    fraction = min(1.0, it / self.iterations) if self.iterations else 1.0
                    done = it >= self.iterations
                if it % hist_every == 0:
                    history.append((it, best_cost))
                if it % _RESYNC_EVERY == 0 and it:
                    ev.reset(assign)
                if cancel is not None and cancel.is_set():
                    stopped = not done
                    done = True
                if progress is not None and (done or now - last_report >= self.progress_interval_s):
                    last_report = now
                    progress(
                        ProgressEvent(
                            iteration=it,
                            total_iterations=total,
                            elapsed_s=elapsed,
                            temperature=temperature,
                            current_cost=ev.total,
                            best_cost=best_cost,
                            initial_cost=initial_cost,
                            fraction=fraction,
                            best_assignment=None if best_sent else c.decode(best),
                        )
                    )
                    best_sent = True
                if done:
                    break
            it += 1

            change = search.propose()
            if change is not None:
                undo = search.apply(change)
                delta = ev.delta(assign, [i for i, _ in change])
                if delta <= 0 or (
                    temperature > 0 and rng.random() < math.exp(-delta / temperature)
                ):
                    ev.commit()
                    if ev.total < best_cost - 1e-12:
                        best_cost = ev.total
                        best = assign[:]
                        best_sent = False
                else:
                    search.apply(undo)
            if not timed:
                temperature *= alpha

        final_cost = ev.reset(best)
        history.append((it, final_cost))
        return OptimizationResult(
            assignment=c.decode(best),
            cost=final_cost,
            initial_cost=initial_cost,
            normalizers=norm,
            iterations=it,
            elapsed_s=time.perf_counter() - started,
            stopped_early=stopped,
            seed=self.seed,
            history=history,
            optimizer=self.name,
        )

    def _multi_start(
        self,
        problem: Problem,
        initial: Assignment | None,
        normalizers: Mapping[str, float] | None,
        progress: ProgressCallback | None,
        cancel: CancelToken | None,
    ) -> OptimizationResult:
        """Run ``restarts`` independent annealings from the same start and keep the best.

        Each restart gets the full iteration budget and its own seed, so the result is still a
        pure function of the problem and the seed. A time limit is shared between the restarts.
        """
        started = time.perf_counter()
        best: OptimizationResult | None = None
        history: list[tuple[int, float]] = []
        iterations = 0
        stopped = False
        for n in range(self.restarts):
            single = SimulatedAnnealing(
                seed=self.seed + 7919 * n,
                iterations=self.iterations,
                time_limit_s=(
                    None if self.time_limit_s is None else self.time_limit_s / self.restarts
                ),
                initial_acceptance=self.initial_acceptance,
                final_temperature_ratio=self.final_temperature_ratio,
                move_weights=self.move_weights,
                progress_interval_s=self.progress_interval_s,
            )
            offset = iterations
            floor = best.cost if best is not None else float("inf")
            keep = best.assignment if best is not None else None

            def relay(
                e: ProgressEvent,
                n: int = n,
                offset: int = offset,
                floor: float = floor,
                keep: Assignment | None = keep,
            ) -> None:
                if progress is None:
                    return
                improved = e.best_cost < floor
                progress(
                    ProgressEvent(
                        iteration=offset + e.iteration,
                        total_iterations=(
                            None
                            if e.total_iterations is None
                            else e.total_iterations * self.restarts
                        ),
                        elapsed_s=time.perf_counter() - started,
                        temperature=e.temperature,
                        current_cost=e.current_cost,
                        best_cost=min(e.best_cost, floor),
                        initial_cost=e.initial_cost,
                        fraction=(n + e.fraction) / self.restarts,
                        best_assignment=e.best_assignment if improved else keep,
                    )
                )

            result = single.optimize(
                problem, initial=initial, normalizers=normalizers, progress=relay, cancel=cancel
            )
            normalizers = result.normalizers
            history += [(offset + it, min(cost, floor)) for it, cost in result.history]
            iterations += result.iterations
            if best is None or result.cost < best.cost:
                best = result
            if result.stopped_early:
                stopped = True
                break
        assert best is not None
        return OptimizationResult(
            assignment=best.assignment,
            cost=best.cost,
            initial_cost=best.initial_cost,
            normalizers=best.normalizers,
            iterations=iterations,
            elapsed_s=time.perf_counter() - started,
            stopped_early=stopped,
            seed=self.seed,
            history=history,
            optimizer=self.name,
        )


class _Search:
    """Mutable search state and the move generators."""

    def __init__(
        self,
        c: Compiled,
        ev: Evaluator,
        assign: list[int],
        rng: random.Random,
        move_weights: tuple[float, float, float, float, float],
        active_bias: float = 0.9,
    ) -> None:
        self.c = c
        self.ev = ev
        self.assign = assign
        self.contents = c.contents(assign)
        self.rng = rng
        self.movable = c.movable
        movable = set(c.movable)
        # Items with no routine, no soft constraint and no estimated use never change the cost.
        # Picking them as often as the others wastes most of the budget, so the first item of a
        # move is usually an active one. They still move, mostly as the partner of a swap.
        self.active = [
            i for i in c.movable if c.item_routines[i] or c.item_softs[i] or c.is_occasional[i]
        ]
        self.p_active = active_bias if self.active else 0.0
        self.group_seeds = [
            i
            for members in c.group_members
            if sum(1 for m in members if m in movable) >= 2
            for i in members
            if i in movable
        ]
        self.routine_items = [
            frozenset(item for op, item, _, _ in r.steps if op in (OP_TAKE, OP_STORE))
            for r in c.routines
        ]
        self.cluster_seeds = [i for i in c.movable if c.item_routines[i]]
        w_move, w_swap, w_group, w_cluster, w_exchange = move_weights
        if not self.group_seeds:
            w_group = 0.0
        if not self.cluster_seeds:
            w_cluster = 0.0
        total = (w_move + w_swap + w_group + w_cluster + w_exchange) or 1.0
        self.p_move = w_move / total
        self.p_swap = self.p_move + w_swap / total
        self.p_group = self.p_swap + w_group / total
        self.p_cluster = self.p_group + w_cluster / total

    def propose(self) -> Change | None:
        if not self.movable:
            return None
        u = self.rng.random()
        if u < self.p_move:
            return self._relocate()
        if u < self.p_swap:
            return self._swap()
        if u < self.p_group:
            return self._group()
        if u < self.p_cluster:
            return self._cluster()
        return self._exchange()

    def _pick(self) -> int:
        rng = self.rng
        if rng.random() < self.p_active:
            return self.active[rng.randrange(len(self.active))]
        return self.movable[rng.randrange(len(self.movable))]

    def _relocate(self) -> Change | None:
        c, rng = self.c, self.rng
        i = self._pick()
        dom = c.domains[i]
        new = dom[rng.randrange(len(dom))]
        if new == self.assign[i] or not c.location_ok(new, [*self.contents[new], i]):
            return None
        return [(i, new)]

    def _swap(self) -> Change | None:
        c, rng, assign = self.c, self.rng, self.assign
        i = self._pick()
        j = self.movable[rng.randrange(len(self.movable))]
        la, lb = assign[i], assign[j]
        if la == lb or lb not in c.domain_sets[i] or la not in c.domain_sets[j]:
            return None
        new_a = [m for m in self.contents[la] if m != i]
        new_a.append(j)
        new_b = [m for m in self.contents[lb] if m != j]
        new_b.append(i)
        if not (c.location_ok(la, new_a) and c.location_ok(lb, new_b)):
            return None
        return [(i, lb), (j, la)]

    def _group(self) -> Change | None:
        c, rng, assign = self.c, self.rng, self.assign
        i = self.group_seeds[rng.randrange(len(self.group_seeds))]
        src = assign[i]
        dom = c.domains[i]
        target = dom[rng.randrange(len(dom))]
        if target == src:
            return None
        members = [
            m
            for m in c.group_members[c.group_of[i]]
            if assign[m] == src and c.pinned[m] < 0 and target in c.domain_sets[m]
        ]
        if not c.location_ok(target, self.contents[target] + members):
            return None
        return [(m, target) for m in members]

    def _cluster(self) -> Change | None:
        """Move the items of one routine that share a location, so they keep sharing an opening."""
        c, rng, assign = self.c, self.rng, self.assign
        i = self.cluster_seeds[rng.randrange(len(self.cluster_seeds))]
        routines = c.item_routines[i]
        together = self.routine_items[routines[rng.randrange(len(routines))]]
        src = assign[i]
        dom = c.domains[i]
        target = dom[rng.randrange(len(dom))]
        if target == src:
            return None
        members = [
            m
            for m in self.contents[src]
            if m in together and c.pinned[m] < 0 and target in c.domain_sets[m]
        ]
        if not c.location_ok(target, self.contents[target] + members):
            return None
        return [(m, target) for m in members]

    def _exchange(self) -> Change | None:
        """Swap everything movable between two locations."""
        c, rng, assign = self.c, self.rng, self.assign
        i = self._pick()
        la = assign[i]
        dom = c.domains[i]
        lb = dom[rng.randrange(len(dom))]
        if lb == la:
            return None
        stay_a = [m for m in self.contents[la] if c.pinned[m] >= 0]
        stay_b = [m for m in self.contents[lb] if c.pinned[m] >= 0]
        go_a = [m for m in self.contents[la] if c.pinned[m] < 0]
        go_b = [m for m in self.contents[lb] if c.pinned[m] < 0]
        if any(lb not in c.domain_sets[m] for m in go_a):
            return None
        if any(la not in c.domain_sets[m] for m in go_b):
            return None
        if not (c.location_ok(la, stay_a + go_b) and c.location_ok(lb, stay_b + go_a)):
            return None
        return [(m, lb) for m in go_a] + [(m, la) for m in go_b]

    def apply(self, change: Change) -> Change:
        """Apply ``change`` and return the change that undoes it."""
        assign, contents = self.assign, self.contents
        undo: Change = []
        for i, new in change:
            old = assign[i]
            undo.append((i, old))
            contents[old].remove(i)
            contents[new].append(i)
            assign[i] = new
        return undo

    def calibrate(self, acceptance: float, samples: int = 300) -> float:
        """Initial temperature at which an average uphill move is accepted with ``acceptance``."""
        uphill: list[float] = []
        for _ in range(samples):
            change = self.propose()
            if change is None:
                continue
            undo = self.apply(change)
            delta = self.ev.delta(self.assign, [i for i, _ in change])
            self.apply(undo)
            if delta > 1e-12:
                uphill.append(delta)
        if not uphill:
            return 1e-9
        return (sum(uphill) / len(uphill)) / -math.log(min(max(acceptance, 1e-6), 0.999))
