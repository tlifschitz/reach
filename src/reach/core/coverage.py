"""Coverage analysis: do the routines exercise the catalog, and do the estimates agree?"""

from __future__ import annotations

from dataclasses import dataclass, field

from reach.core.model import Problem, Routine, StepKind


@dataclass(slots=True)
class RoutineFinding:
    routine: str
    severity: str
    """``warning`` or ``info``."""
    message: str
    item: str | None = None


@dataclass(slots=True)
class FrequencyComparison:
    item: str
    estimated: float
    derived: float
    flag: str
    """``ok``, ``unused``, ``underestimated`` (routines use it more than estimated) or
    ``overestimated``."""

    @property
    def ratio(self) -> float | None:
        return self.derived / self.estimated if self.estimated > 0 else None


@dataclass(slots=True)
class CoverageReport:
    unused_items: list[str] = field(default_factory=list)
    findings: list[RoutineFinding] = field(default_factory=list)
    frequencies: list[FrequencyComparison] = field(default_factory=list)
    covered: int = 0
    total: int = 0


def inspect_routine(routine: Routine, min_steps: int = 2) -> list[RoutineFinding]:
    """Structural checks on a single routine."""
    out: list[RoutineFinding] = []
    steps = [s for s in routine.steps if s.kind is not StepKind.SET_STATE]
    if len(steps) < min_steps:
        out.append(
            RoutineFinding(routine.id, "warning", f"only {len(steps)} step(s); is it complete?")
        )
    if routine.frequency <= 0:
        out.append(RoutineFinding(routine.id, "warning", "frequency is 0, so it has no effect"))
    if not any(s.item for s in steps):
        out.append(RoutineFinding(routine.id, "info", "handles no items, so it never matters"))

    holding: dict[str, int] = {}  # item -> step number of the take
    for n, step in enumerate(routine.steps, start=1):
        item = step.item
        if item is None:
            continue
        if step.kind is StepKind.TAKE:
            if item in holding:
                out.append(
                    RoutineFinding(
                        routine.id, "warning", f"step {n}: {item} is taken twice in a row", item
                    )
                )
            holding[item] = n
        elif step.kind is StepKind.USE:
            if item not in holding:
                out.append(
                    RoutineFinding(
                        routine.id,
                        "info",
                        f"step {n}: {item} is used without being taken first",
                        item,
                    )
                )
            else:
                holding[item] = -1  # taken and used: fine even if it stays out
        elif step.kind in (StepKind.PUT_DOWN, StepKind.STORE):
            holding.pop(item, None)
    for item, n in holding.items():
        if n > 0:
            out.append(
                RoutineFinding(
                    routine.id,
                    "warning",
                    f"{item} is taken at step {n} and never used, put down or stored",
                    item,
                )
            )
    return out


def analyze_coverage(
    problem: Problem, *, ratio_threshold: float = 2.0, min_steps: int = 2
) -> CoverageReport:
    report = CoverageReport(total=len(problem.items))
    derived = problem.derived_frequencies()
    for item_id, item in problem.items.items():
        d = derived[item_id]
        e = item.estimated_frequency
        if d <= 0:
            report.unused_items.append(item_id)
            flag = "unused"
        elif e <= 0 or d / e >= ratio_threshold:
            flag = "underestimated"
        elif e / d >= ratio_threshold:
            flag = "overestimated"
        else:
            flag = "ok"
        report.frequencies.append(FrequencyComparison(item_id, e, d, flag))
    report.covered = report.total - len(report.unused_items)
    for routine in problem.routines:
        report.findings.extend(inspect_routine(routine, min_steps))
    return report
