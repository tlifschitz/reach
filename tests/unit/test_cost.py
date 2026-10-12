import random

import pytest

from reach.core.compile import Compiled
from reach.core.cost import Evaluator, compute_normalizers, evaluate, soft_penalty
from reach.core.model import METRICS, Problem, Selector, SoftConstraint
from reach.core.optim import greedy_assignment
from reach.core.optim.annealing import _Search
from tests.helpers import PARAMS, items, locations, random_problem, routine


def test_cost_is_one_at_the_assignment_the_normalizers_come_from():
    p = random_problem(1)
    start = greedy_assignment(p)
    # No soft constraints -> exactly the sum of the weights of metrics that are not all zero.
    p_no_soft = Problem(
        p.locations, p.items, p.routines, p.params, p.obstructions, p.hard, (), p.pins
    )
    ev = evaluate(p_no_soft, start)
    active = sum(w for m, w in p.params.weights.items() if ev.totals[m] > 0)
    assert ev.cost == pytest.approx(active)


def test_weekly_totals_multiply_by_frequency():
    p = Problem(
        locations(),
        items("x"),
        (routine("take x; put_down x at counter", frequency=7.0),),
        PARAMS,
    )
    ev = evaluate(p, {"x": "A"})
    assert ev.routines[0].metrics["distance"] == pytest.approx(800)
    assert ev.routines[0].weekly["distance"] == pytest.approx(5600)
    assert ev.totals["distance"] == pytest.approx(5600)
    assert set(ev.totals) == set(METRICS)


def test_explicit_normalizers_change_the_scale_not_the_ranking():
    p = Problem(locations(), items("x"), (routine("take x; put_down x at counter"),), PARAMS)
    norm = dict.fromkeys(METRICS, 1.0)
    near = evaluate(p, {"x": "A"}, norm).cost
    far = evaluate(p, {"x": "B"}, norm).cost
    assert near < far
    # distance 800 x 0.2 + openings 1 x 0.2 + time 12 x 0.35 + effort 0
    assert near == pytest.approx(800 * 0.2 + 0.2 + 12 * 0.35)


def test_soft_same_location_counts_extra_locations():
    soft = SoftConstraint("kit", Selector(items=frozenset({"x", "y", "z"})), "same_location", 0.5)
    p = Problem(locations(), items("x", "y", "z"), (), PARAMS, soft=(soft,))
    c = Compiled(p)
    assert soft_penalty(c, c.softs[0], c.encode({"x": "A", "y": "A", "z": "A"})) == 0
    assert soft_penalty(c, c.softs[0], c.encode({"x": "A", "y": "B", "z": "A"})) == 0.5
    assert soft_penalty(c, c.softs[0], c.encode({"x": "A", "y": "B", "z": "C"})) == 1.0


def test_soft_nearby_uses_mean_pairwise_distance_beyond_the_radius():
    soft = SoftConstraint(
        "near", Selector(items=frozenset({"x", "y"})), "nearby", 0.1, radius=100.0
    )
    p = Problem(locations(), items("x", "y"), (), PARAMS, soft=(soft,))
    c = Compiled(p)
    assert soft_penalty(c, c.softs[0], c.encode({"x": "A", "y": "A"})) == 0
    # A-B are 300 cm apart: 0.1 x (300 - 100) / 100
    assert soft_penalty(c, c.softs[0], c.encode({"x": "A", "y": "B"})) == pytest.approx(0.2)


def test_soft_penalty_is_part_of_the_total():
    soft = SoftConstraint("kit", Selector(items=frozenset({"x", "y"})), "same_location", 0.5)
    p = Problem(locations(), items("x", "y"), (routine("take x; store x"),), PARAMS, soft=(soft,))
    norm = dict.fromkeys(METRICS, 1.0)
    together = evaluate(p, {"x": "A", "y": "A"}, norm)
    split = evaluate(p, {"x": "A", "y": "B"}, norm)
    assert split.cost - together.cost == pytest.approx(0.5)
    assert split.soft == {"kit": 0.5}


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_incremental_cost_matches_full_recomputation(seed):
    p = random_problem(seed)
    c = Compiled(p)
    assign = c.encode(greedy_assignment(p, c))
    ev = Evaluator(c, compute_normalizers(c, assign))
    ev.reset(assign)
    search = _Search(c, ev, assign, random.Random(seed), (0.4, 0.3, 0.1, 0.1, 0.1))
    applied = 0
    for _ in range(3000):
        change = search.propose()
        if change is None:
            continue
        undo = search.apply(change)
        delta = ev.delta(assign, [i for i, _ in change])
        if search.rng.random() < 0.6:
            before = ev.total
            ev.commit()
            assert ev.total == pytest.approx(before + delta)
            applied += 1
        else:
            search.apply(undo)
        if applied % 50 == 0:
            fresh = Evaluator(c, compute_normalizers(c, c.encode(greedy_assignment(p, c))))
            fresh.coef = ev.coef
            assert ev.total == pytest.approx(fresh.reset(assign), rel=1e-9, abs=1e-9)
    assert applied > 100
    assert search.contents == [sorted(x) for x in map(sorted, c.contents(assign))] or all(
        sorted(a) == sorted(b) for a, b in zip(search.contents, c.contents(assign), strict=True)
    )
