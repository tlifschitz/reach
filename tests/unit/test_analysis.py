import re
from pathlib import Path

import pytest

from reach.core.coverage import analyze_coverage, inspect_routine
from reach.core.model import Item, Problem
from reach.core.sensitivity import perturbed_frequencies, run_sensitivity
from tests.helpers import PARAMS, locations, random_problem, routine


def test_coverage_finds_unused_items_and_compares_frequencies():
    items = {
        "mug": Item("mug", estimated_frequency=7),
        "kettle": Item("kettle", estimated_frequency=1),
        "whisk": Item("whisk", estimated_frequency=20),
        "fondue": Item("fondue", estimated_frequency=0.1),
    }
    routines = (
        routine(
            "take mug; take kettle; use kettle at counter; store kettle; put_down mug at counter",
            frequency=7,
            rid="tea",
        ),
        routine("take whisk; use whisk at counter; store whisk", frequency=2, rid="bake"),
    )
    report = analyze_coverage(Problem(locations(), items, routines, PARAMS))
    assert report.unused_items == ["fondue"]
    assert (report.covered, report.total) == (3, 4)
    flags = {f.item: f.flag for f in report.frequencies}
    assert flags == {
        "mug": "ok",
        "kettle": "underestimated",  # used 7x/week, estimated 1
        "whisk": "overestimated",  # used 2x/week, estimated 20
        "fondue": "unused",
    }
    assert not report.findings


def test_suspicious_routines_are_flagged():
    taken_never_used = routine("take mug; go_to counter", rid="odd")
    messages = [f.message for f in inspect_routine(taken_never_used)]
    assert any("never used" in m for m in messages)

    too_short = routine("take mug", rid="short")
    assert any("only 1 step" in f.message for f in inspect_routine(too_short))

    used_without_take = routine("use mug at counter; store mug", rid="implicit")
    findings = inspect_routine(used_without_take)
    assert [f.severity for f in findings] == ["info"]

    fine = routine("take mug; use mug at counter; store mug", rid="fine")
    assert inspect_routine(fine) == []


def test_perturbation_stays_in_range_and_is_reproducible():
    p = random_problem(0)
    a = perturbed_frequencies(p, 0.3, seed=5)
    assert a == perturbed_frequencies(p, 0.3, seed=5)
    assert a != perturbed_frequencies(p, 0.3, seed=6)
    for r in p.routines:
        assert r.frequency * 0.7 <= a[r.id] <= r.frequency * 1.3


def test_sensitivity_report_separates_noise_from_perturbation():
    p = random_problem(1)
    seen = []
    report = run_sensitivity(
        p,
        runs=3,
        perturbation=0.3,
        seed=3,
        iterations=3_000,
        workers=1,
        progress=lambda done, total: seen.append((done, total)),
    )
    # 3 noise runs (same frequencies, new seeds) and 3 perturbed runs.
    assert seen[-1] == (6, 6)
    assert report.noise.runs == 3 and report.perturbed.runs == 3
    assert len(report.noise.costs) == 3 and len(report.baseline_costs) == 3
    for rs in (report.noise, report.perturbed):
        assert rs.summary["min"] <= rs.summary["median"] <= rs.summary["max"]
        assert 0 <= rs.exact_stability <= rs.area_stability <= 1
    by_item = {s.item: s for s in report.items}
    assert by_item["i39"].label == "pinned" and by_item["i39"].noise.exact == 1.0
    assert set(report.counts) == {"robust", "noisy", "sensitive", "unused", "pinned"}
    assert sum(report.counts.values()) == len(report.items)
    for s in report.items:
        for shares in (s.noise, s.perturbed):
            assert 0 <= shares.exact <= shares.area <= 1
            assert shares.exact <= shares.modal_share
            assert sum(shares.locations.values()) == 3
    again = run_sensitivity(p, runs=3, perturbation=0.3, seed=3, iterations=3_000, workers=1)
    assert again.noise.costs == report.noise.costs
    assert again.perturbed.costs == report.perturbed.costs


def test_noise_runs_keep_the_frequencies_and_change_only_the_seed():
    p = random_problem(1)
    report = run_sensitivity(p, runs=0, noise_runs=3, seed=3, iterations=3_000, workers=1)
    assert report.perturbed is None and report.noise.runs == 3 and report.baseline_costs == []
    assert all(s.perturbed is None for s in report.items)
    assert report.counts["sensitive"] == 0  # without perturbed runs nothing can be called that
    # Different seeds really are different searches.
    assert len(set(report.noise.costs)) > 1
    only_perturbed = run_sensitivity(p, runs=2, noise_runs=0, seed=3, iterations=3_000, workers=1)
    assert only_perturbed.noise is None and only_perturbed.counts["noisy"] == 0


def test_area_stability_ignores_moves_between_equivalent_locations():
    from dataclasses import replace

    from reach.core.model import Location, Point
    from reach.core.sensitivity import _shares, area_key

    locs = {
        "a1": Location("a1", Point(0, 0), assignable=True, level=1, area="north"),
        "a2": Location("a2", Point(10, 0), assignable=True, level=1, area="north"),
        "a3": Location("a3", Point(20, 0), assignable=True, level=2, area="north"),
        "b1": Location("b1", Point(0, 9), assignable=True, level=1, area="south"),
        "free": Location("free", Point(5, 5), assignable=True, level=1),
    }
    p = Problem(locs, {"x": Item("x")}, (), PARAMS)
    runs = [{"x": "a1"}, {"x": "a2"}, {"x": "a3"}, {"x": "b1"}]
    shares = _shares(p, "x", "a1", runs)
    assert shares.exact == 0.25  # only the first run
    assert shares.area == 0.5  # a2 is the same area and level; a3 and b1 are not
    assert area_key(p, "free") == ("free", 1)  # a location without an area is its own area
    assert replace(shares, locations={}).modal_share == 0.25


def test_restarts_keep_the_best_of_several_runs():
    from reach.core.constraints import check_assignment
    from reach.core.optim import SimulatedAnnealing

    p = random_problem(2)
    singles = [
        SimulatedAnnealing(seed=5 + 7919 * n, iterations=3_000).optimize(p) for n in range(3)
    ]
    events = []
    multi = SimulatedAnnealing(
        seed=5, iterations=3_000, restarts=3, progress_interval_s=0
    ).optimize(p, progress=events.append)
    assert multi.cost == pytest.approx(min(r.cost for r in singles))
    assert multi.iterations == sum(r.iterations for r in singles)
    assert check_assignment(p, multi.assignment) == []
    fractions = [e.fraction for e in events]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    bests = [e.best_cost for e in events]
    assert bests == sorted(bests, reverse=True)  # the reported best never gets worse
    again = SimulatedAnnealing(seed=5, iterations=3_000, restarts=3).optimize(p)
    assert again.assignment == multi.assignment


def test_sensitivity_runs_in_a_process_pool():
    report = run_sensitivity(random_problem(1), runs=1, seed=3, iterations=500, workers=2)
    assert report.noise.runs == 1 and report.perturbed.runs == 1


KITCHEN_WORDS = re.compile(
    r"\b(kitchen|drawer|pantry|fridge|freezer|stove|sink|countertop|cabinet|compartment|poi|pois|"
    r"cook|cooking|recipe|dish|spice|knife|knives)\b",
    re.IGNORECASE,
)


def test_core_is_domain_agnostic():
    core = Path(__file__).parents[2] / "src" / "reach" / "core"
    offenders = []
    for path in core.rglob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), start=1):
            if KITCHEN_WORDS.search(line):
                offenders.append(f"{path.relative_to(core)}:{n}: {line.strip()}")
        imports = re.findall(r"^\s*(?:from|import)\s+(reach\.\w+)", path.read_text(), re.MULTILINE)
        assert set(imports) <= {"reach.core"}, f"{path.name} imports {set(imports)}"
    assert offenders == []


def test_frequency_zero_routine_warns():
    r = routine("take mug; store mug", frequency=0)
    assert any("frequency is 0" in f.message for f in inspect_routine(r))
