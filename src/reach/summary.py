"""JSON-friendly summaries of evaluations and results, shared by the CLI and the API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from reach.core.compile import Compiled
from reach.core.constraints import check_assignment, fills
from reach.core.cost import Evaluation, compute_normalizers, evaluate
from reach.core.coverage import CoverageReport
from reach.core.model import METRICS, Assignment, Problem
from reach.core.optim import greedy_assignment
from reach.core.sensitivity import SensitivityReport
from reach.core.simulate import simulate

METRIC_LABELS = {
    "distance": "Distance (m)",
    "openings": "Openings",
    "time": "Time (min)",
    "effort": "Effort",
    "extra_trips": "Extra trips",
    "clearings": "Obstruction clearings",
}
METRIC_SCALE = {"distance": 0.01, "time": 1 / 60}
"""Display units: metres and minutes instead of centimetres and seconds."""


def sample_dir() -> Path:
    """Folder of the bundled sample project."""
    import os

    override = os.environ.get("REACH_DATA_DIR")
    candidates = [
        Path(override) / "sample" if override else None,
        Path(__file__).resolve().parents[2] / "data" / "sample",
        Path(__file__).resolve().parent / "_data" / "sample",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_dir():
            return candidate
    raise FileNotFoundError("the bundled sample data was not found; set REACH_DATA_DIR")


def baseline_normalizers(problem: Problem, compiled: Compiled | None = None) -> dict[str, float]:
    """Normalizers from the greedy solution: the scale every comparison uses."""
    c = compiled or Compiled(problem)
    return compute_normalizers(c, c.encode(greedy_assignment(problem, c)))


def evaluation_dict(ev: Evaluation) -> dict[str, Any]:
    return {
        "cost": ev.cost,
        "soft_total": ev.soft_total,
        "soft": ev.soft,
        "totals": ev.totals,
        "normalizers": ev.normalizers,
        "routines": [asdict(r) for r in ev.routines],
    }


def locations_table(problem: Problem, assignment: Assignment) -> list[dict[str, Any]]:
    """Contents and fill of every location that holds something or can be assigned to."""
    out = []
    for loc_id, fill in fills(problem, assignment).items():
        loc = problem.locations[loc_id]
        if not loc.assignable and not fill.items:
            continue
        out.append(
            {
                "id": loc_id,
                "name": loc.name,
                "kind": loc.kind,
                "level": loc.level,
                "assignable": loc.assignable,
                "items": [
                    {
                        "id": i,
                        "name": problem.items[i].name,
                        "quantity": problem.items[i].quantity,
                        "pinned": i in problem.pins,
                    }
                    for i in fill.items
                ],
                "volume": fill.volume,
                "capacity_volume": fill.capacity_volume,
                "fill": fill.fraction,
                "weight": fill.weight,
                "capacity_weight": fill.capacity_weight,
            }
        )
    return out


def assignment_report(
    problem: Problem,
    assignment: Assignment,
    normalizers: Mapping[str, float],
    compiled: Compiled | None = None,
) -> dict[str, Any]:
    c = compiled or Compiled(problem)
    return {
        "assignment": assignment,
        "evaluation": evaluation_dict(evaluate(problem, assignment, normalizers, c)),
        "locations": locations_table(problem, assignment),
        "violations": [asdict(v) for v in check_assignment(problem, assignment, c)],
    }


def comparison(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """Metric-by-metric change between two ``assignment_report`` results."""
    b, a = before["evaluation"], after["evaluation"]

    def change(old: float, new: float) -> float | None:
        return (new - old) / old if old else None

    routines = []
    after_by_id = {r["id"]: r for r in a["routines"]}
    for r in b["routines"]:
        other = after_by_id.get(r["id"])
        if other is None:
            continue
        routines.append(
            {
                "id": r["id"],
                "name": r["name"],
                "frequency": r["frequency"],
                "before": r["weekly"],
                "after": other["weekly"],
                "cost_before": r["cost"],
                "cost_after": other["cost"],
                "cost_change": change(r["cost"], other["cost"]),
            }
        )
    moved = [
        {"item": i, "from": loc, "to": after["assignment"].get(i)}
        for i, loc in before["assignment"].items()
        if after["assignment"].get(i) != loc
    ]
    return {
        "cost_before": b["cost"],
        "cost_after": a["cost"],
        "cost_change": change(b["cost"], a["cost"]),
        "totals": {
            m: {
                "before": b["totals"][m],
                "after": a["totals"][m],
                "change": change(b["totals"][m], a["totals"][m]),
            }
            for m in METRICS
        },
        "routines": routines,
        "moved": moved,
    }


def routine_trace(
    problem: Problem, assignment: Assignment, routine_id: str, compiled: Compiled | None = None
) -> dict[str, Any]:
    """The path of one routine, for drawing and animating."""
    c = compiled or Compiled(problem)
    index = next((n for n, r in enumerate(problem.routines) if r.id == routine_id), None)
    if index is None:
        raise KeyError(routine_id)
    routine = problem.routines[index]
    events: list = []
    metrics = simulate(c, c.routines[index], c.encode(assignment), events)
    out = []
    for step, kind, point, t, ref in events:
        p = c.points[point]
        event: dict[str, Any] = {"step": step, "kind": kind, "x": p.x, "y": p.y, "t": t}
        if point < c.n_locs:
            event["location"] = c.loc_ids[point]
        if kind in ("clear", "restore"):
            event["obstruction"] = c.obs_ids[ref]
        if 0 <= step < len(routine.steps):
            event["label"] = routine.steps[step].describe()
        out.append(event)
    return {
        "routine": routine_id,
        "name": routine.name,
        "steps": [s.describe() for s in routine.steps],
        "metrics": dict(zip(METRICS, metrics, strict=True)),
        "events": out,
    }


def coverage_dict(report: CoverageReport, problem: Problem) -> dict[str, Any]:
    return {
        "covered": report.covered,
        "total": report.total,
        "unused_items": [
            {"id": i, "name": problem.items[i].name, "category": problem.items[i].category}
            for i in report.unused_items
        ],
        "findings": [asdict(f) for f in report.findings],
        "frequencies": [
            {**asdict(f), "ratio": f.ratio, "name": problem.items[f.item].name}
            for f in report.frequencies
        ],
    }


def sensitivity_dict(report: SensitivityReport, problem: Problem) -> dict[str, Any]:
    data = asdict(report)
    for row in data["items"]:
        row["name"] = problem.items[row["item"]].name
    data["counts"] = {
        label: sum(1 for s in report.items if s.label == label)
        for label in ("robust", "sensitive", "unused", "pinned")
    }
    return data
