import threading
from dataclasses import replace

import pytest

from reach.core.compile import Compiled
from reach.core.constraints import check_assignment
from reach.core.model import HardConstraint, HardKind, Item, Problem, Selector
from reach.core.optim import (
    GreedyOptimizer,
    InfeasibleError,
    Optimizer,
    SimulatedAnnealing,
    greedy_assignment,
)
from reach.core.optim.feasibility import diagnose
from tests.helpers import PARAMS, locations, random_problem, routine


@pytest.mark.parametrize("seed", range(5))
def test_greedy_is_feasible(seed):
    p = random_problem(seed)
    assert check_assignment(p, greedy_assignment(p)) == []


@pytest.mark.parametrize("seed", range(5))
def test_annealing_is_feasible_and_improves_on_greedy(seed):
    p = random_problem(seed)
    result = SimulatedAnnealing(seed=seed, iterations=20_000).optimize(p)
    assert check_assignment(p, result.assignment) == []
    assert result.cost < result.initial_cost
    greedy = GreedyOptimizer().optimize(p, normalizers=result.normalizers)
    assert result.cost < greedy.cost
    assert result.initial_cost == pytest.approx(greedy.cost)


def test_every_intermediate_best_solution_is_feasible():
    p = random_problem(3)
    seen = []
    SimulatedAnnealing(seed=1, iterations=20_000, progress_interval_s=0).optimize(
        p, progress=lambda e: seen.append(e.best_assignment)
    )
    snapshots = [a for a in seen if a is not None]
    assert len(snapshots) > 3
    assert all(check_assignment(p, a) == [] for a in snapshots)


def test_same_seed_gives_the_same_result():
    p = random_problem(2)
    a = SimulatedAnnealing(seed=7, iterations=5_000).optimize(p)
    b = SimulatedAnnealing(seed=7, iterations=5_000).optimize(p)
    c = SimulatedAnnealing(seed=8, iterations=5_000).optimize(p)
    assert a.assignment == b.assignment and a.cost == b.cost
    assert a.assignment != c.assignment


def test_pins_are_respected():
    p = random_problem(4)
    result = SimulatedAnnealing(seed=0, iterations=5_000).optimize(p)
    assert result.assignment["i39"] == "fridge"
    assert result.assignment["i38"] == "L5"


def test_cancel_returns_the_best_solution_so_far():
    p = random_problem(0)
    cancel = threading.Event()
    events = []

    def progress(e):
        events.append(e)
        if e.iteration >= 2_000:
            cancel.set()

    result = SimulatedAnnealing(seed=0, iterations=5_000_000, progress_interval_s=0).optimize(
        p, progress=progress, cancel=cancel
    )
    assert result.stopped_early
    assert result.iterations < 5_000_000
    assert check_assignment(p, result.assignment) == []
    assert result.cost <= result.initial_cost


def test_time_limit_budget_stops():
    p = random_problem(0)
    result = SimulatedAnnealing(seed=0, time_limit_s=0.3).optimize(p)
    assert 0.25 < result.elapsed_s < 3
    assert result.cost <= result.initial_cost


def test_infeasible_initial_assignment_is_rejected():
    p = random_problem(0)
    bad = dict.fromkeys(p.items, "L0")
    with pytest.raises(ValueError, match="infeasible"):
        SimulatedAnnealing(iterations=10).optimize(p, initial=bad)


def test_optimizers_share_the_interface():
    assert issubclass(SimulatedAnnealing, Optimizer) and issubclass(GreedyOptimizer, Optimizer)


def test_greedy_puts_the_frequent_item_in_the_better_spot():
    items = {"daily": Item("daily", volume=8.0), "rare": Item("rare", volume=8.0)}
    routines = (
        routine("take daily; put_down daily at counter", frequency=14, rid="often"),
        routine("take rare; put_down rare at counter", frequency=1, rid="seldom"),
    )
    locs = {k: v for k, v in locations().items() if k != "C"}
    a = greedy_assignment(Problem(locs, items, routines, PARAMS))
    # A is 400 cm from the counter and at level 1; B is 500 cm away and at level 0.
    assert a == {"daily": "A", "rare": "B"}


def test_greedy_repair_makes_room_for_a_constrained_item():
    # 'low' may only live in B (level 0). 'filler' is used more often and takes B first.
    items = {
        "filler": Item("filler", volume=9.0, weight=5.0),
        "low": Item("low", volume=9.0, category="heavy"),
    }
    hard = (
        HardConstraint("h", HardKind.MAX_LEVEL, Selector(categories=frozenset({"heavy"})), level=0),
    )
    routines = (routine("take filler; store filler", start="B", frequency=10),)
    locs = {k: v for k, v in locations().items() if k != "C"}
    p = Problem(locs, items, routines, PARAMS, hard=hard)
    a = greedy_assignment(p)
    assert a["low"] == "B" and a["filler"] == "A"
    assert check_assignment(p, a) == []


def test_empty_domain_is_explained():
    items = {"knife": Item("knife", category="sharp")}
    hard = (
        HardConstraint(
            "up_high", HardKind.REQUIRE_TAG, Selector(items=frozenset({"knife"})), tag="safe"
        ),
    )
    p = Problem(locations(), items, (), PARAMS, hard=hard)
    with pytest.raises(InfeasibleError) as exc:
        greedy_assignment(p)
    report = exc.value.report
    assert [e.item for e in report.empty_domains] == ["knife"]
    assert report.empty_domains[0].reasons == {"constraint up_high": 3}
    assert "up_high" in str(exc.value)


def test_capacity_shortfall_is_explained():
    # Three 6 l items that may only use A and B (10 l each): 18 l needed, but no two fit together.
    items = {f"pot{n}": Item(f"pot{n}", volume=6.0, category="heavy") for n in range(3)}
    hard = (
        HardConstraint(
            "low", HardKind.MAX_LEVEL, Selector(categories=frozenset({"heavy"})), level=1
        ),
    )
    p = Problem(locations(), items, (), PARAMS, hard=hard)
    with pytest.raises(InfeasibleError) as exc:
        SimulatedAnnealing(iterations=10).optimize(p)
    report = exc.value.report
    assert not report.feasible
    assert report.unplaced and report.unplaced[0].item.startswith("pot")
    assert "found no room" in exc.value.report.summary()


def test_aggregate_shortfall_is_detected_statically():
    items = {f"pot{n}": Item(f"pot{n}", volume=8.0, category="heavy") for n in range(3)}
    hard = (
        HardConstraint(
            "low", HardKind.MAX_LEVEL, Selector(categories=frozenset({"heavy"})), level=1
        ),
    )
    report = diagnose(Problem(locations(), items, (), PARAMS, hard=hard))
    assert len(report.shortfalls) == 1
    s = report.shortfalls[0]
    assert sorted(s.locations) == ["A", "B"] and s.needed == 24 and s.available == 20


def test_overfull_pins_are_reported():
    items = {"a": Item("a", volume=6.0), "b": Item("b", volume=6.0)}
    p = Problem(locations(), items, (), PARAMS, pins={"a": "A", "b": "A"})
    report = diagnose(p)
    assert report.conflicts and "pinned" in report.conflicts[0]


def test_a_problem_without_routines_still_gets_a_feasible_assignment():
    p = replace(random_problem(0), routines=())
    result = SimulatedAnnealing(iterations=500).optimize(p)
    assert check_assignment(p, result.assignment) == []
    assert Compiled(p).movable
