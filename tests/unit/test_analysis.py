import re
from pathlib import Path

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


def test_sensitivity_report():
    p = random_problem(1)
    seen = []
    report = run_sensitivity(
        p,
        runs=4,
        perturbation=0.3,
        seed=3,
        iterations=3_000,
        workers=1,
        progress=lambda done, total: seen.append((done, total)),
    )
    assert report.runs == 4 and seen[-1] == (4, 4)
    assert len(report.costs) == 4 and len(report.baseline_costs) == 4
    assert report.cost_summary["min"] <= report.cost_summary["median"] <= report.cost_summary["max"]
    by_item = {s.item: s for s in report.items}
    assert by_item["i39"].label == "pinned" and by_item["i39"].modal_share == 1.0
    assert {s.label for s in report.items} <= {"robust", "sensitive", "unused", "pinned"}
    for s in report.items:
        assert 0 <= s.baseline_share <= s.modal_share <= 1
        assert sum(s.locations.values()) == 4
    again = run_sensitivity(p, runs=4, perturbation=0.3, seed=3, iterations=3_000, workers=1)
    assert again.costs == report.costs


def test_sensitivity_runs_in_a_process_pool():
    report = run_sensitivity(random_problem(1), runs=2, seed=3, iterations=500, workers=2)
    assert report.runs == 2


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
