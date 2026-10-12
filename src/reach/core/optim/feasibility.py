"""Explains why a problem has no feasible assignment."""

from __future__ import annotations

from dataclasses import dataclass, field

from reach.core.capacity import used_volume
from reach.core.compile import Compiled, domain_exclusions
from reach.core.model import Problem


@dataclass(slots=True)
class EmptyDomain:
    item: str
    reasons: dict[str, int]
    """Reason -> number of locations it removed."""


@dataclass(slots=True)
class Shortfall:
    locations: list[str]
    items: list[str]
    needed: float
    available: float


@dataclass(slots=True)
class Unplaced:
    item: str
    volume: float
    candidates: list[tuple[str, float]]
    """``(location, free volume)`` for the roomiest locations of the item's domain."""


@dataclass(slots=True)
class InfeasibilityReport:
    empty_domains: list[EmptyDomain] = field(default_factory=list)
    shortfalls: list[Shortfall] = field(default_factory=list)
    unplaced: list[Unplaced] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def feasible(self) -> bool:
        return not (self.empty_domains or self.shortfalls or self.unplaced or self.conflicts)

    def lines(self) -> list[str]:
        out = []
        for e in self.empty_domains:
            why = ", ".join(f"{reason} ({n} locations)" for reason, n in sorted(e.reasons.items()))
            out.append(f"{e.item}: no location is allowed. Removed by: {why or 'no locations'}")
        for s in self.shortfalls:
            names = ", ".join(s.items[:6]) + (" ..." if len(s.items) > 6 else "")
            where = ", ".join(s.locations[:6]) + (" ..." if len(s.locations) > 6 else "")
            out.append(
                f"capacity: {len(s.items)} items ({names}) need at least {s.needed:.1f} l "
                f"but their only allowed locations ({where}) hold {s.available:.1f} l"
            )
        for u in self.unplaced:
            room = ", ".join(f"{loc} has {free:.1f} l free" for loc, free in u.candidates)
            out.append(f"{u.item}: needs {u.volume:.1f} l and found no room ({room or 'none'})")
        out.extend(self.conflicts)
        return out

    def summary(self) -> str:
        return "no feasible assignment:\n" + "\n".join(f"  - {line}" for line in self.lines())


def diagnose(problem: Problem, compiled: Compiled | None = None) -> InfeasibilityReport:
    """Static checks that need no search: empty domains and aggregate capacity shortfalls."""
    c = compiled or Compiled(problem)
    report = InfeasibilityReport()

    by_domain: dict[tuple[int, ...], list[int]] = {}
    for i, item_id in enumerate(c.item_ids):
        if c.pinned[i] >= 0:
            continue
        if not c.domains[i]:
            reasons: dict[str, int] = {}
            for reason in domain_exclusions(problem, problem.items[item_id]).values():
                reasons[reason] = reasons.get(reason, 0) + 1
            report.empty_domains.append(EmptyDomain(item_id, reasons))
        else:
            by_domain.setdefault(c.domains[i], []).append(i)

    # Items pinned into assignable locations use up capacity there.
    pinned_in: dict[int, list[int]] = {}
    for i, loc in enumerate(c.pinned):
        if loc >= 0 and c.assignable[loc]:
            pinned_in.setdefault(loc, []).append(i)
    for loc, members in pinned_in.items():
        if not c.location_ok(loc, members):
            report.conflicts.append(
                f"{c.loc_ids[loc]}: the items pinned there "
                f"({', '.join(c.item_ids[i] for i in members)}) exceed its capacity or must be "
                "kept apart"
            )

    # Hall-type check: all items restricted to (a subset of) a domain must fit in that domain.
    # Nested volume of everything together is a lower bound on the space they need.
    domains = list(by_domain)
    for dom in domains:
        dom_set = set(dom)
        members = [i for d, items in by_domain.items() if set(d) <= dom_set for i in items]
        pinned = [i for loc in dom for i in pinned_in.get(loc, [])]
        needed = used_volume(
            [problem.items[c.item_ids[i]] for i in members + pinned], problem.params
        )
        caps = [c.cap_volume[loc] for loc in dom]
        if any(cap is None for cap in caps):
            continue
        available = sum(cap for cap in caps if cap is not None)
        if needed > available + 1e-9:
            report.shortfalls.append(
                Shortfall(
                    [c.loc_ids[loc] for loc in dom],
                    [c.item_ids[i] for i in members],
                    needed,
                    available,
                )
            )
    # Keep only the tightest shortfalls: drop any whose location set contains another's.
    report.shortfalls.sort(key=lambda s: len(s.locations))
    kept: list[Shortfall] = []
    for s in report.shortfalls:
        if not any(set(k.locations) <= set(s.locations) for k in kept):
            kept.append(s)
    report.shortfalls = kept
    return report
