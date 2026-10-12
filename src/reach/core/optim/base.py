"""The optimizer interface and the types shared by all optimizers."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from reach.core.model import Assignment, Problem


class CancelToken(Protocol):
    """Anything with ``is_set()``: a ``threading.Event`` or a ``multiprocessing.Event``."""

    def is_set(self) -> bool: ...


@dataclass(slots=True)
class ProgressEvent:
    iteration: int
    total_iterations: int | None
    elapsed_s: float
    temperature: float
    current_cost: float
    best_cost: float
    initial_cost: float
    fraction: float
    best_assignment: Assignment | None = None
    """Included now and then, so a listener can show the best solution found so far."""


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(slots=True)
class OptimizationResult:
    assignment: Assignment
    cost: float
    initial_cost: float
    normalizers: dict[str, float]
    iterations: int = 0
    elapsed_s: float = 0.0
    stopped_early: bool = False
    seed: int | None = None
    history: list[tuple[int, float]] = field(default_factory=list)
    """``(iteration, best cost)`` samples."""
    optimizer: str = ""


class InfeasibleError(Exception):
    """No assignment satisfies the hard constraints and capacities."""

    def __init__(self, report) -> None:  # report: feasibility.InfeasibilityReport
        super().__init__(report.summary())
        self.report = report


class Optimizer(ABC):
    """Finds an assignment of items to locations.

    Implementations must return feasible assignments only, honour ``cancel`` by returning the
    best solution found so far, and be deterministic for a given seed where they use randomness.
    """

    name: str = "optimizer"

    @abstractmethod
    def optimize(
        self,
        problem: Problem,
        *,
        initial: Assignment | None = None,
        normalizers: Mapping[str, float] | None = None,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> OptimizationResult: ...


def never_cancel() -> threading.Event:
    return threading.Event()
