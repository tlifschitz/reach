"""End to end on the bundled sample kitchen: load, optimize, compare, analyse, CLI."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reach.cli import app
from reach.core.constraints import check_assignment
from reach.core.cost import evaluate
from reach.core.coverage import analyze_coverage
from reach.core.optim import SimulatedAnnealing, greedy_assignment
from reach.core.sensitivity import run_sensitivity
from reach.io.csv_loader import load_assignment
from reach.io.errors import ValidationReport
from reach.io.export import assignment_csv
from reach.io.project import load_project
from reach.summary import baseline_normalizers, routine_trace

SAMPLE = Path(__file__).parents[2] / "data" / "sample"
runner = CliRunner()


@pytest.fixture(scope="module")
def project():
    p = load_project(SAMPLE)
    assert p.ok
    return p


@pytest.fixture(scope="module")
def result(project):
    return SimulatedAnnealing(seed=0, iterations=60_000).optimize(project.problem)


def test_optimized_assignment_is_feasible_and_beats_greedy(project, result):
    problem = project.problem
    assert check_assignment(problem, result.assignment) == []
    assert check_assignment(problem, greedy_assignment(problem)) == []
    assert result.cost < result.initial_cost * 0.98
    for item, loc in problem.pins.items():
        assert result.assignment[item] == loc


def test_optimized_assignment_beats_the_current_layout(project, result):
    problem = project.problem
    current = evaluate(problem, project.current_assignment, result.normalizers)
    optimized = evaluate(problem, result.assignment, result.normalizers)
    assert optimized.cost < current.cost * 0.7
    for metric in ("distance", "openings", "time", "effort"):
        assert optimized.totals[metric] < current.totals[metric]
    # The current layout keeps things behind the entrance door; the optimizer learns not to.
    assert current.totals["clearings"] > 0
    assert optimized.totals["clearings"] < current.totals["clearings"]


def test_current_layout_breaks_two_of_its_own_rules(project):
    violated = {v.constraint for v in check_assignment(project.problem, project.current_assignment)}
    assert violated == {"fragile_without_step_stool", "textiles_away_from_stove"}


def test_hard_constraints_hold_in_the_result(project, result):
    problem, a = project.problem, result.assignment
    levels = {lid: loc.level for lid, loc in problem.locations.items()}
    tags = {lid: loc.tags for lid, loc in problem.locations.items()}
    assert levels[a["chef_knife"]] >= 1
    assert levels[a["dutch_oven"]] <= 1
    assert levels[a["wine_glass"]] <= 2
    assert "child_reachable" in tags[a["kids_plates"]]
    assert "child_reachable" not in tags[a["bleach"]]
    assert "near_heat" not in tags[a["olive_oil"]]
    food = {
        i
        for i, it in problem.items.items()
        if it.category in {"food_dry", "spices", "oils", "baking", "coffee_tea"}
    }
    chemicals = {i for i, it in problem.items.items() if it.category in {"cleaning", "dishwashing"}}
    assert not {a[i] for i in food} & {a[i] for i in chemicals}


def test_unloading_the_dishwasher_avoids_the_blocked_drawers(project, result):
    trace = routine_trace(project.problem, result.assignment, "unload_dishwasher")
    assert trace["metrics"]["clearings"] == 0
    kinds = {e["kind"] for e in trace["events"]}
    assert {"start", "move", "open", "handle"} <= kinds
    assert trace["events"][-1]["t"] == pytest.approx(trace["metrics"]["time"])


def test_same_seed_is_reproducible(project, result):
    again = SimulatedAnnealing(seed=0, iterations=60_000).optimize(project.problem)
    assert again.assignment == result.assignment and again.cost == result.cost


def test_assignment_csv_round_trips(project, result):
    text = assignment_csv(project.problem, result.assignment)
    parsed, _ = load_assignment(text, "out.csv", ValidationReport())
    assert parsed == result.assignment


def test_coverage_on_the_sample(project):
    report = analyze_coverage(project.problem)
    assert 0 < len(report.unused_items) < report.total
    assert not [f for f in report.findings if f.severity == "warning"]


def test_sensitivity_on_the_sample(project, result):
    report = run_sensitivity(
        project.problem,
        runs=3,
        perturbation=0.3,
        seed=1,
        iterations=10_000,
        workers=1,
        baseline=result.assignment,
        normalizers=baseline_normalizers(project.problem),
    )
    labels = {s.label for s in report.items}
    assert report.runs == 3 and "pinned" in labels and "unused" in labels
    assert labels & {"robust", "sensitive"}


def test_cli_validate():
    out = runner.invoke(app, ["validate", str(SAMPLE)])
    assert out.exit_code == 0 and "OK" in out.output
    as_json = json.loads(runner.invoke(app, ["validate", str(SAMPLE), "--json"]).output)
    assert as_json == {"ok": True, "issues": [], "infeasible": []}


def test_cli_validate_reports_errors(tmp_path):
    for f in SAMPLE.iterdir():
        (tmp_path / f.name).write_text(f.read_text())
    items = (
        (tmp_path / "items.csv")
        .read_text()
        .replace(
            "dinner_plate,Dinner plates,tableware,1.8", "dinner_plate,Dinner plates,tableware,big"
        )
    )
    (tmp_path / "items.csv").write_text(items)
    out = runner.invoke(app, ["validate", str(tmp_path)])
    assert out.exit_code == 1
    assert "items.csv:2: error: volume_l" in out.output.replace("\n", " ")


def test_cli_simulate_optimize_and_coverage(tmp_path):
    sim = json.loads(runner.invoke(app, ["simulate", str(SAMPLE), "--current", "--json"]).output)
    assert len(sim["evaluation"]["routines"]) == 18 and len(sim["violations"]) == 2

    target = tmp_path / "best.csv"
    args = [
        "optimize",
        str(SAMPLE),
        "--iterations",
        "20000",
        "--seed",
        "3",
        "--json",
        "--out",
        str(target),
    ]
    opt = json.loads(runner.invoke(app, args).output)
    assert opt["cost"] < opt["greedy_cost"] and opt["violations"] == []
    assert opt["vs_current"]["cost_change"] < -0.3
    assert target.read_text().startswith("item_id,location_id")

    back = json.loads(
        runner.invoke(app, ["simulate", str(SAMPLE), "--assignment", str(target), "--json"]).output
    )
    assert back["evaluation"]["cost"] == pytest.approx(opt["cost"])

    cov = json.loads(runner.invoke(app, ["coverage", str(SAMPLE), "--json"]).output)
    assert cov["covered"] + len(cov["unused_items"]) == cov["total"]

    human = runner.invoke(app, ["optimize", str(SAMPLE), "--iterations", "5000"])
    assert human.exit_code == 0 and "Compared with current" in human.output


def test_cli_sensitivity():
    args = [
        "sensitivity",
        str(SAMPLE),
        "--runs",
        "2",
        "--iterations",
        "3000",
        "--workers",
        "1",
        "--json",
    ]
    data = json.loads(runner.invoke(app, args).output)
    assert data["runs"] == 2 and sum(data["counts"].values()) == len(data["items"])


def test_cli_reports_infeasibility(tmp_path):
    for f in SAMPLE.iterdir():
        (tmp_path / f.name).write_text(f.read_text())
    constraints = (
        (tmp_path / "constraints.yaml")
        .read_text()
        .replace("tag: child_reachable\n    note: The kids", "tag: nowhere\n    note: The kids")
    )
    (tmp_path / "constraints.yaml").write_text(constraints)
    out = runner.invoke(app, ["optimize", str(tmp_path), "--iterations", "100"])
    assert out.exit_code == 2
    flat = " ".join(out.output.split())
    assert "No feasible assignment" in flat and "kids_tableware_reachable" in flat
