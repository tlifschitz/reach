"""Writing results back out: assignment CSV and YAML fragments."""

from __future__ import annotations

import csv
import io
from typing import Any

import yaml

from reach.core.model import Assignment, Problem
from reach.io.schemas import RoutineModel


def assignment_csv(problem: Problem, assignment: Assignment) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["item_id", "location_id", "item_name", "category", "pinned"])
    for item_id, item in problem.items.items():
        writer.writerow(
            [
                item_id,
                assignment.get(item_id, ""),
                item.name,
                item.category,
                "yes" if item_id in problem.pins else "",
            ]
        )
    return out.getvalue()


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def routines_yaml(routines: list[RoutineModel]) -> str:
    return dump_yaml({"routines": [r.to_yaml() for r in routines]})
