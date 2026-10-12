"""Total cost: normalized, weighted routine metrics plus soft penalties."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from reach.core.compile import Compiled, CompiledSoft
from reach.core.model import METRICS, Assignment, Problem
from reach.core.simulate import Metrics, simulate


def soft_penalty(c: Compiled, soft: CompiledSoft, assign: list[int]) -> float:
    if soft.same_location:
        return soft.penalty * (len({assign[i] for i in soft.items}) - 1)
    dist = c.dist
    members = soft.items
    total = 0.0
    pairs = 0
    for a in range(len(members)):
        la = assign[members[a]]
        row = dist[la]
        for b in range(a + 1, len(members)):
            pairs += 1
            d = row[assign[members[b]]] - soft.radius
            if d > 0:
                total += d
    return soft.penalty * total / pairs / 100.0 if pairs else 0.0


def occasional_metrics(c: Compiled, item: int, loc: int) -> Metrics:
    """One generic access to an item that no routine mentions.

    A round trip from the origin to the location: walk there, clear any obstruction that blocks
    by default, open, take the item, put the obstruction back, walk back.
    """
    distance = 2.0 * c.dist[c.origin][loc]
    openings = c.open_cost[loc]
    time = c.open_time[loc] + c.handling
    clearings = 0
    for o in c.blockers[loc]:
        if c.obs_default_blocking[o]:
            clearings += 1
            openings += c.obs_clear_open[o] + c.obs_restore_open[o]
            time += c.obs_clear_time[o] + c.obs_restore_time[o]
    effort = c.level_penalty[loc] * c.weight_mult[item]
    return (distance, openings, time + distance / c.speed, effort, 0.0, float(clearings))


def weekly_totals(c: Compiled, assign: list[int]) -> dict[str, float]:
    """Weekly metrics of the routines only (occasional accesses are not included)."""
    totals = [0.0] * len(METRICS)
    for routine in c.routines:
        m = simulate(c, routine, assign)
        for k in range(len(METRICS)):
            totals[k] += routine.frequency * m[k]
    return dict(zip(METRICS, totals, strict=True))


def compute_normalizers(c: Compiled, assign: list[int]) -> dict[str, float]:
    """Weekly total of each metric under ``assign`` (1 where that total is 0).

    Explicit ``params.normalizers`` take precedence metric by metric.
    """
    explicit = c.problem.params.normalizers or {}
    totals = weekly_totals(c, assign)
    out = {}
    for m in METRICS:
        value = explicit.get(m) or totals[m]
        out[m] = value if value > 0 else 1.0
    return out


class Evaluator:
    """Caches the cost of every routine and soft constraint and updates it incrementally."""

    def __init__(self, c: Compiled, normalizers: Mapping[str, float]) -> None:
        self.c = c
        weights = c.problem.params.weights
        self.coef = tuple(weights.get(m, 0.0) / (normalizers.get(m) or 1.0) for m in METRICS)
        self.routine_cost = [0.0] * len(c.routines)
        self.soft_cost = [0.0] * len(c.softs)
        self.occasional_cost = [0.0] * c.n_items
        self.total = 0.0
        # Occasional access cost per location, split into the part that does not depend on the
        # item and the effort part, which scales with the item's weight.
        k = self.coef
        self._occ_base = []
        self._occ_effort = []
        for loc in range(c.n_locs):
            m = occasional_metrics(c, 0, loc) if c.n_items else (0.0,) * 6
            self._occ_base.append(k[0] * m[0] + k[1] * m[1] + k[2] * m[2] + k[5] * m[5])
            self._occ_effort.append(k[3] * c.level_penalty[loc])
        self._pending_occasional: list[tuple[int, float]] = []
        self._pending_routines: list[tuple[int, float]] = []
        self._pending_softs: list[tuple[int, float]] = []
        self._pending_delta = 0.0

    def _routine(self, r: int, assign: list[int]) -> float:
        routine = self.c.routines[r]
        m = simulate(self.c, routine, assign)
        k = self.coef
        return routine.frequency * (
            k[0] * m[0] + k[1] * m[1] + k[2] * m[2] + k[3] * m[3] + k[4] * m[4] + k[5] * m[5]
        )

    def _occasional(self, item: int, loc: int) -> float:
        c = self.c
        return c.estimated[item] * (
            self._occ_base[loc] + self._occ_effort[loc] * c.weight_mult[item]
        )

    def reset(self, assign: list[int]) -> float:
        """Recompute everything from scratch."""
        c = self.c
        self.routine_cost = [self._routine(r, assign) for r in range(len(c.routines))]
        self.soft_cost = [soft_penalty(c, s, assign) for s in c.softs]
        self.occasional_cost = [
            self._occasional(i, assign[i]) if c.is_occasional[i] else 0.0 for i in range(c.n_items)
        ]
        self.total = sum(self.routine_cost) + sum(self.soft_cost) + sum(self.occasional_cost)
        return self.total

    def delta(self, assign: list[int], changed: tuple[int, ...] | list[int]) -> float:
        """Cost change after the items in ``changed`` were moved in ``assign``.

        Only the routines and soft constraints that mention a changed item are recomputed.
        Call :meth:`commit` to keep the change; do nothing (and restore ``assign``) to drop it.
        """
        c = self.c
        if len(changed) == 1:
            routines = c.item_routines[changed[0]]
            softs = c.item_softs[changed[0]]
        else:
            rs: set[int] = set()
            ss: set[int] = set()
            for i in changed:
                rs.update(c.item_routines[i])
                ss.update(c.item_softs[i])
            routines = tuple(rs)
            softs = tuple(ss)
        delta = 0.0
        pr = self._pending_routines = []
        for r in routines:
            new = self._routine(r, assign)
            delta += new - self.routine_cost[r]
            pr.append((r, new))
        ps = self._pending_softs = []
        for s in softs:
            new = soft_penalty(c, c.softs[s], assign)
            delta += new - self.soft_cost[s]
            ps.append((s, new))
        po = self._pending_occasional = []
        for i in changed:
            if c.is_occasional[i]:
                new = self._occasional(i, assign[i])
                delta += new - self.occasional_cost[i]
                po.append((i, new))
        self._pending_delta = delta
        return delta

    def commit(self) -> None:
        for r, v in self._pending_routines:
            self.routine_cost[r] = v
        for s, v in self._pending_softs:
            self.soft_cost[s] = v
        for i, v in self._pending_occasional:
            self.occasional_cost[i] = v
        self.total += self._pending_delta


@dataclass(slots=True)
class RoutineResult:
    id: str
    name: str
    frequency: float
    metrics: dict[str, float]
    """One execution of the routine."""
    weekly: dict[str, float]
    """Metrics multiplied by the weekly frequency."""
    cost: float
    """Weighted, normalized weekly cost contributed by the routine."""


@dataclass(slots=True)
class Evaluation:
    cost: float
    routines: list[RoutineResult]
    totals: dict[str, float]
    soft: dict[str, float]
    normalizers: dict[str, float]
    soft_total: float = field(default=0.0)
    routine_totals: dict[str, float] = field(default_factory=dict)
    """Weekly metrics of the routines alone. ``totals`` also includes the occasional accesses."""
    occasional: dict[str, float] = field(default_factory=dict)
    """Weekly metrics of the generic accesses to items that no routine mentions."""
    occasional_cost: float = 0.0
    occasional_items: int = 0


def evaluate(
    problem: Problem,
    assignment: Assignment,
    normalizers: Mapping[str, float] | None = None,
    compiled: Compiled | None = None,
) -> Evaluation:
    """Full, readable evaluation of an assignment (not used in the optimizer's inner loop)."""
    c = compiled or Compiled(problem)
    assign = c.encode(assignment)
    norm = dict(normalizers) if normalizers else compute_normalizers(c, assign)
    weights = problem.params.weights
    totals = dict.fromkeys(METRICS, 0.0)
    routines = []
    cost = 0.0
    for routine, compiled_routine in zip(problem.routines, c.routines, strict=True):
        m: Metrics = simulate(c, compiled_routine, assign)
        metrics = dict(zip(METRICS, m, strict=True))
        weekly = {k: v * routine.frequency for k, v in metrics.items()}
        rc = sum(weights.get(k, 0.0) * weekly[k] / norm[k] for k in METRICS)
        for k in METRICS:
            totals[k] += weekly[k]
        cost += rc
        routines.append(
            RoutineResult(routine.id, routine.name, routine.frequency, metrics, weekly, rc)
        )
    routine_totals = dict(totals)
    occasional = dict.fromkeys(METRICS, 0.0)
    n_occasional = 0
    for i in range(c.n_items):
        if c.is_occasional[i]:
            n_occasional += 1
            for k, v in zip(METRICS, occasional_metrics(c, i, assign[i]), strict=True):
                occasional[k] += c.estimated[i] * v
    occasional_cost = sum(weights.get(k, 0.0) * occasional[k] / norm[k] for k in METRICS)
    for k in METRICS:
        totals[k] += occasional[k]
    soft = {s.id: soft_penalty(c, s, assign) for s in c.softs}
    soft_total = sum(soft.values())
    return Evaluation(
        cost + occasional_cost + soft_total,
        routines,
        totals,
        soft,
        norm,
        soft_total,
        routine_totals,
        occasional,
        occasional_cost,
        n_occasional,
    )
