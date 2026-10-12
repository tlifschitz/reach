"""Validation of a complete assignment against capacity and hard constraints."""

from __future__ import annotations

from dataclasses import dataclass

from reach.core.capacity import used_volume, used_weight
from reach.core.compile import Compiled, unary_violation
from reach.core.model import Assignment, Problem


@dataclass(frozen=True, slots=True)
class Violation:
    kind: str
    """``unassigned``, ``unknown_location``, ``not_assignable``, ``pin``, ``constraint``,
    ``volume``, ``weight`` or ``separate``."""
    message: str
    item: str | None = None
    location: str | None = None
    constraint: str | None = None


def check_assignment(
    problem: Problem, assignment: Assignment, compiled: Compiled | None = None
) -> list[Violation]:
    """Every way in which ``assignment`` is infeasible. An empty list means feasible."""
    c = compiled or Compiled(problem)
    out: list[Violation] = []
    contents: dict[str, list[str]] = {}
    for item_id, item in problem.items.items():
        loc_id = assignment.get(item_id)
        if loc_id is None:
            out.append(Violation("unassigned", f"{item_id} has no location", item=item_id))
            continue
        loc = problem.locations.get(loc_id)
        if loc is None:
            out.append(
                Violation(
                    "unknown_location",
                    f"{item_id} is assigned to unknown location {loc_id}",
                    item=item_id,
                    location=loc_id,
                )
            )
            continue
        contents.setdefault(loc_id, []).append(item_id)
        pin = problem.pins.get(item_id)
        if pin is not None:
            if pin != loc_id:
                out.append(
                    Violation(
                        "pin",
                        f"{item_id} is pinned to {pin} but assigned to {loc_id}",
                        item=item_id,
                        location=loc_id,
                    )
                )
            continue
        if not loc.assignable:
            out.append(
                Violation(
                    "not_assignable",
                    f"{item_id} is in {loc_id}, which only accepts pinned items",
                    item=item_id,
                    location=loc_id,
                )
            )
            continue
        hc = unary_violation(problem, item, loc)
        if hc is not None:
            out.append(
                Violation(
                    "constraint",
                    f"{item_id} in {loc_id} violates {hc.id} ({hc.kind.value})",
                    item=item_id,
                    location=loc_id,
                    constraint=hc.id,
                )
            )

    for loc_id, item_ids in contents.items():
        loc = problem.locations[loc_id]
        items = [problem.items[i] for i in item_ids]
        if loc.assignable:
            vol = used_volume(items, problem.params)
            if loc.capacity_volume is not None and vol > loc.capacity_volume + 1e-9:
                out.append(
                    Violation(
                        "volume",
                        f"{loc_id} holds {vol:.1f} l but fits {loc.capacity_volume:.1f} l",
                        location=loc_id,
                    )
                )
            wt = used_weight(items)
            if loc.capacity_weight is not None and wt > loc.capacity_weight + 1e-9:
                out.append(
                    Violation(
                        "weight",
                        f"{loc_id} holds {wt:.1f} kg but carries {loc.capacity_weight:.1f} kg",
                        location=loc_id,
                    )
                )
        cid = c.separation_violation([c.item_index[i] for i in item_ids])
        if cid is not None:
            out.append(
                Violation(
                    "separate",
                    f"{loc_id} mixes items that {cid} keeps apart",
                    location=loc_id,
                    constraint=cid,
                )
            )
    return out


def is_feasible(problem: Problem, assignment: Assignment) -> bool:
    return not check_assignment(problem, assignment)


@dataclass(frozen=True, slots=True)
class Fill:
    location: str
    items: tuple[str, ...]
    volume: float
    capacity_volume: float | None
    weight: float
    capacity_weight: float | None

    @property
    def fraction(self) -> float | None:
        if not self.capacity_volume:
            return None
        return self.volume / self.capacity_volume


def fills(problem: Problem, assignment: Assignment) -> dict[str, Fill]:
    """What every location holds and how full it is."""
    contents: dict[str, list[str]] = {lid: [] for lid in problem.locations}
    for item_id, loc_id in assignment.items():
        if loc_id in contents and item_id in problem.items:
            contents[loc_id].append(item_id)
    out = {}
    for loc_id, item_ids in contents.items():
        loc = problem.locations[loc_id]
        items = [problem.items[i] for i in item_ids]
        out[loc_id] = Fill(
            loc_id,
            tuple(item_ids),
            used_volume(items, problem.params),
            loc.capacity_volume,
            used_weight(items),
            loc.capacity_weight,
        )
    return out
