"""Integer-indexed form of a :class:`Problem`, built once and shared by the hot loops."""

from __future__ import annotations

import math
from dataclasses import dataclass

from reach.core.capacity import used_volume, used_weight
from reach.core.model import (
    Assignment,
    HardConstraint,
    HardKind,
    Item,
    Location,
    Point,
    Problem,
    StepKind,
)

OP_GO_TO, OP_TAKE, OP_USE, OP_PUT_DOWN, OP_STORE, OP_SET_STATE = range(6)
_OPS = {
    StepKind.GO_TO: OP_GO_TO,
    StepKind.TAKE: OP_TAKE,
    StepKind.USE: OP_USE,
    StepKind.PUT_DOWN: OP_PUT_DOWN,
    StepKind.STORE: OP_STORE,
    StepKind.SET_STATE: OP_SET_STATE,
}


class ProblemError(ValueError):
    """The problem references something that does not exist."""


@dataclass(slots=True)
class CompiledRoutine:
    id: str
    frequency: float
    start: int
    steps: list[tuple[int, int, int, int]]
    """``(op, item, location, next_drop)``; ``set_state`` is ``(op, obstruction, blocking, -1)``."""
    initial_blocking: tuple[bool, ...]


@dataclass(slots=True)
class CompiledSoft:
    id: str
    items: tuple[int, ...]
    same_location: bool
    penalty: float
    radius: float


def unary_violation(problem: Problem, item: Item, loc: Location) -> HardConstraint | None:
    """The first unary hard constraint that forbids ``item`` in ``loc``, if any."""
    for hc in problem.hard:
        if hc.kind is HardKind.SEPARATE or not hc.select.matches(item):
            continue
        if hc.kind is HardKind.REQUIRE_TAG:
            bad = hc.tag not in loc.tags
        elif hc.kind is HardKind.FORBID_TAG:
            bad = hc.tag in loc.tags
        elif hc.kind is HardKind.MIN_LEVEL:
            bad = loc.level < (hc.level or 0)
        elif hc.kind is HardKind.MAX_LEVEL:
            bad = loc.level > (hc.level or 0)
        else:
            ref = problem.locations.get(hc.reference or "")
            if ref is None:
                raise ProblemError(f"constraint {hc.id}: unknown location {hc.reference!r}")
            d = _dist(loc.position, ref.position)
            limit = hc.distance or 0.0
            bad = d < limit if hc.kind is HardKind.MIN_DISTANCE else d > limit
        if bad:
            return hc
    return None


def domain_exclusions(problem: Problem, item: Item) -> dict[str, str]:
    """For every assignable location ``item`` cannot use: the reason."""
    out: dict[str, str] = {}
    for loc in problem.locations.values():
        if not loc.assignable:
            continue
        hc = unary_violation(problem, item, loc)
        if hc is not None:
            out[loc.id] = f"constraint {hc.id}"
        elif loc.capacity_volume is not None and (
            used_volume([item], problem.params) > loc.capacity_volume + 1e-9
        ):
            out[loc.id] = "too large"
        elif loc.capacity_weight is not None and (used_weight([item]) > loc.capacity_weight + 1e-9):
            out[loc.id] = "too heavy"
    return out


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


class Compiled:
    """Arrays indexed by item, location, obstruction and routine number."""

    def __init__(self, problem: Problem) -> None:
        self.problem = problem
        p = problem.params

        self.loc_ids: list[str] = list(problem.locations)
        self.loc_index = {lid: i for i, lid in enumerate(self.loc_ids)}
        self.item_ids: list[str] = list(problem.items)
        self.item_index = {iid: i for i, iid in enumerate(self.item_ids)}
        locs = [problem.locations[lid] for lid in self.loc_ids]
        items = [problem.items[iid] for iid in self.item_ids]
        self.n_locs = len(locs)
        self.n_items = len(items)

        # Points: every location, then routine start points and obstruction positions.
        self.points: list[Point] = [loc.position for loc in locs]

        # Locations.
        self.open_time = [0.0] * self.n_locs
        self.open_cost = [0.0] * self.n_locs
        for i, loc in enumerate(locs):
            profile = p.opening(loc.kind, loc.level)
            if profile is not None:
                self.open_time[i] = profile.time_s
                self.open_cost[i] = profile.cost
        self.level_penalty = [p.penalty(loc.level) for loc in locs]
        self.assignable = [loc.assignable for loc in locs]
        self.cap_volume = [loc.capacity_volume for loc in locs]
        self.cap_weight = [loc.capacity_weight for loc in locs]

        # Items.
        self.weight_mult = [1.0 + p.weight_factor * it.weight for it in items]
        self.total_volume = [it.volume * it.quantity for it in items]
        self.unit_volume = [it.volume if it.quantity > 0 else 0.0 for it in items]
        self.total_weight = [it.weight * it.quantity for it in items]
        groups = sorted({it.nest_group for it in items if it.nest_group})
        gindex = {g: i for i, g in enumerate(groups)}
        self.group_ids = groups
        self.group_of = [gindex[it.nest_group] if it.nest_group else -1 for it in items]
        self.group_alpha = [p.nest_factors.get(g, p.nest_factor) for g in groups]
        self.group_members: list[list[int]] = [[] for _ in groups]
        for i, g in enumerate(self.group_of):
            if g >= 0:
                self.group_members[g].append(i)

        # Pins and domains.
        self.pinned = [-1] * self.n_items
        for item_id, loc_id in problem.pins.items():
            if item_id not in self.item_index:
                raise ProblemError(f"pin: unknown item {item_id!r}")
            if loc_id not in self.loc_index:
                raise ProblemError(f"pin of {item_id}: unknown location {loc_id!r}")
            self.pinned[self.item_index[item_id]] = self.loc_index[loc_id]
        self.domains: list[tuple[int, ...]] = []
        for i, it in enumerate(items):
            if self.pinned[i] >= 0:
                self.domains.append((self.pinned[i],))
                continue
            excluded = domain_exclusions(problem, it)
            self.domains.append(
                tuple(j for j, loc in enumerate(locs) if loc.assignable and loc.id not in excluded)
            )
        self.domain_sets = [frozenset(d) for d in self.domains]

        # Separation constraints: per constraint, which side(s) each item is on.
        self.separations: list[tuple[str, list[bool], list[bool]]] = []
        for hc in problem.hard:
            if hc.kind is HardKind.SEPARATE and hc.other is not None:
                side_a = [hc.select.matches(it) for it in items]
                side_b = [hc.other.matches(it) for it in items]
                if any(side_a) and any(side_b):
                    self.separations.append((hc.id, side_a, side_b))

        # Obstructions.
        self.obs_ids = [o.id for o in problem.obstructions]
        self.obs_index = {oid: i for i, oid in enumerate(self.obs_ids)}
        self.n_obs = len(self.obs_ids)
        self.obs_clear_time = [o.clear.time_s for o in problem.obstructions]
        self.obs_clear_open = [o.clear.openings for o in problem.obstructions]
        self.obs_restore_time = [o.restore.time_s for o in problem.obstructions]
        self.obs_restore_open = [o.restore.openings for o in problem.obstructions]
        self.obs_pos = [-1] * self.n_obs
        blockers: list[list[int]] = [[] for _ in locs]
        for k, o in enumerate(problem.obstructions):
            if o.position is not None:
                self.obs_pos[k] = self._point(o.position)
            for lid in o.blocks:
                if lid not in self.loc_index:
                    raise ProblemError(f"obstruction {o.id}: unknown location {lid!r}")
                blockers[self.loc_index[lid]].append(k)

        # Routines.
        self.routines: list[CompiledRoutine] = []
        item_routines: list[set[int]] = [set() for _ in items]
        for r_idx, routine in enumerate(problem.routines):
            if isinstance(routine.start, Point):
                start = self._point(routine.start)
            else:
                start = self._loc(routine.start, f"routine {routine.id}: start")
            initial = []
            for o in problem.obstructions:
                state = routine.initial_states.get(o.id, o.default_state)
                initial.append(state == o.blocking_state)
            for oid in routine.initial_states:
                if oid not in self.obs_index:
                    raise ProblemError(f"routine {routine.id}: unknown obstruction {oid!r}")
            steps: list[tuple[int, int, int, int]] = []
            for n, step in enumerate(routine.steps):
                where = f"routine {routine.id}, step {n + 1}"
                op = _OPS[step.kind]
                if op == OP_SET_STATE:
                    if step.obstruction not in self.obs_index:
                        raise ProblemError(f"{where}: unknown obstruction {step.obstruction!r}")
                    k = self.obs_index[step.obstruction]
                    blocking = step.state == problem.obstructions[k].blocking_state
                    steps.append((op, k, int(blocking), -1))
                    continue
                item = -1
                if op != OP_GO_TO:
                    if step.item not in self.item_index:
                        raise ProblemError(f"{where}: unknown item {step.item!r}")
                    item = self.item_index[step.item]
                    item_routines[item].add(r_idx)
                loc = -1
                if op in (OP_GO_TO, OP_USE, OP_PUT_DOWN):
                    loc = self._loc(step.location, where)
                steps.append((op, item, loc, -1))
            # next_drop: the next step with a fixed location, used by the extra-trip model.
            nxt = -1
            for n in range(len(steps) - 1, -1, -1):
                op, item, loc, _ = steps[n]
                if op == OP_TAKE:
                    steps[n] = (op, item, loc, nxt)
                elif op in (OP_GO_TO, OP_USE, OP_PUT_DOWN):
                    nxt = loc
            self.routines.append(
                CompiledRoutine(routine.id, routine.frequency, start, steps, tuple(initial))
            )
        self.item_routines = [tuple(sorted(s)) for s in item_routines]
        self.item_frequency = [
            sum(self.routines[r].frequency for r in rs) for rs in self.item_routines
        ]

        # Occasional items: mentioned by no routine, but with an estimated usage.
        origin = p.occasional_origin
        if isinstance(origin, str):
            self.origin = self._loc(origin, "occasional origin")
        elif origin is not None:
            self.origin = self._point(origin)
        else:
            n = max(len(locs), 1)
            self.origin = self._point(
                Point(
                    sum(loc.position.x for loc in locs) / n, sum(loc.position.y for loc in locs) / n
                )
            )
        self.estimated = [it.estimated_frequency for it in items]
        self.is_occasional = [
            p.occasional and not self.item_routines[i] and self.estimated[i] > 0
            for i in range(self.n_items)
        ]
        self.obs_default_blocking = [
            o.default_state == o.blocking_state for o in problem.obstructions
        ]

        # Soft constraints.
        self.softs: list[CompiledSoft] = []
        item_softs: list[list[int]] = [[] for _ in items]
        for sc in problem.soft:
            members = tuple(i for i, it in enumerate(items) if sc.select.matches(it))
            if len(members) < 2:
                continue
            for i in members:
                item_softs[i].append(len(self.softs))
            self.softs.append(
                CompiledSoft(sc.id, members, sc.mode != "nearby", sc.penalty, sc.radius)
            )
        self.item_softs = [tuple(s) for s in item_softs]

        # Points are complete now: pad per-point arrays and build the distance matrix.
        n_points = len(self.points)
        self.blockers: list[tuple[int, ...]] = [tuple(b) for b in blockers]
        self.blockers += [()] * (n_points - self.n_locs)
        pts = self.points
        self.dist = [[_dist(a, b) for b in pts] for a in pts]

        self.speed = p.walking_speed
        self.handling = p.handling_time
        self.max_carry = p.max_carry if p.max_carry and p.max_carry > 0 else 0
        self.movable = [
            i for i in range(self.n_items) if self.pinned[i] < 0 and len(self.domains[i]) > 1
        ]

    def _point(self, point: Point) -> int:
        self.points.append(point)
        return len(self.points) - 1

    def _loc(self, loc_id: str | None, where: str) -> int:
        if loc_id not in self.loc_index:
            raise ProblemError(f"{where}: unknown location {loc_id!r}")
        return self.loc_index[loc_id]

    # Conversions between id-keyed assignments and index lists.

    def encode(self, assignment: Assignment) -> list[int]:
        out = []
        for item_id in self.item_ids:
            loc_id = assignment.get(item_id)
            if loc_id is None:
                raise ProblemError(f"item {item_id!r} has no location")
            if loc_id not in self.loc_index:
                raise ProblemError(f"item {item_id}: unknown location {loc_id!r}")
            out.append(self.loc_index[loc_id])
        return out

    def decode(self, assign: list[int]) -> Assignment:
        return {self.item_ids[i]: self.loc_ids[j] for i, j in enumerate(assign)}

    def contents(self, assign: list[int]) -> list[list[int]]:
        out: list[list[int]] = [[] for _ in range(self.n_locs)]
        for i, j in enumerate(assign):
            out[j].append(i)
        return out

    # Per-location feasibility.

    def volume(self, members: list[int]) -> float:
        total = 0.0
        g_total: dict[int, float] = {}
        g_max: dict[int, float] = {}
        group_of = self.group_of
        for i in members:
            g = group_of[i]
            if g < 0:
                total += self.total_volume[i]
            else:
                g_total[g] = g_total.get(g, 0.0) + self.total_volume[i]
                if self.unit_volume[i] > g_max.get(g, 0.0):
                    g_max[g] = self.unit_volume[i]
        for g, t in g_total.items():
            vmax = g_max.get(g, 0.0)
            total += vmax + self.group_alpha[g] * (t - vmax)
        return total

    def weight(self, members: list[int]) -> float:
        tw = self.total_weight
        return sum(tw[i] for i in members)

    def separation_violation(self, members: list[int]) -> str | None:
        for cid, side_a, side_b in self.separations:
            a = [i for i in members if side_a[i]]
            if not a:
                continue
            for j in members:
                if side_b[j] and any(i != j for i in a):
                    return cid
        return None

    def location_ok(self, loc: int, members: list[int]) -> bool:
        """Capacity and separation for ``members`` stored together in ``loc``."""
        if self.separations and self.separation_violation(members) is not None:
            return False
        if not self.assignable[loc]:
            return True
        cap = self.cap_volume[loc]
        if cap is not None and self.volume(members) > cap + 1e-9:
            return False
        capw = self.cap_weight[loc]
        return not (capw is not None and self.weight(members) > capw + 1e-9)
