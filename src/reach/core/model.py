"""Generic data model: locations, items, routines, obstructions and cost parameters.

Nothing here knows about any particular domain. Front ends (see ``reach.io``) translate their
own vocabulary into these types.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

Assignment = dict[str, str]
"""Maps an item id to the id of the location that holds it."""

METRICS: tuple[str, ...] = ("distance", "openings", "time", "effort", "extra_trips", "clearings")


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Location:
    """A place the actor can stand at, and possibly store items in."""

    id: str
    position: Point
    kind: str = "point"
    level: int = 1
    assignable: bool = False
    capacity_volume: float | None = None
    capacity_weight: float | None = None
    tags: frozenset[str] = frozenset()
    name: str = ""
    geometry: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    name: str = ""
    category: str = ""
    volume: float = 0.0
    quantity: int = 1
    weight: float = 0.0
    estimated_frequency: float = 0.0
    nest_group: str | None = None
    tags: frozenset[str] = frozenset()


class StepKind(StrEnum):
    GO_TO = "go_to"
    TAKE = "take"
    USE = "use"
    PUT_DOWN = "put_down"
    STORE = "store"
    SET_STATE = "set_state"


@dataclass(frozen=True, slots=True)
class Step:
    kind: StepKind
    item: str | None = None
    location: str | None = None
    obstruction: str | None = None
    state: str | None = None

    def describe(self) -> str:
        if self.kind is StepKind.GO_TO:
            return f"go_to {self.location}"
        if self.kind is StepKind.SET_STATE:
            return f"set_state {self.obstruction} {self.state}"
        if self.kind in (StepKind.USE, StepKind.PUT_DOWN):
            return f"{self.kind.value} {self.item} at {self.location}"
        return f"{self.kind.value} {self.item}"


@dataclass(frozen=True, slots=True)
class Routine:
    id: str
    name: str
    frequency: float
    start: str | Point
    steps: tuple[Step, ...]
    initial_states: Mapping[str, str] = field(default_factory=dict)

    def item_ids(self) -> set[str]:
        return {s.item for s in self.steps if s.item is not None}


@dataclass(frozen=True, slots=True)
class TransitionCost:
    time_s: float = 0.0
    openings: float = 0.0


@dataclass(frozen=True, slots=True)
class Obstruction:
    """Something that blocks a set of locations while it is in ``blocking_state``."""

    id: str
    default_state: str
    blocking_state: str
    blocks: frozenset[str]
    clear: TransitionCost = TransitionCost()
    restore: TransitionCost = TransitionCost()
    position: Point | None = None
    name: str = ""


@dataclass(frozen=True, slots=True)
class OpeningProfile:
    time_s: float = 0.0
    cost: float = 1.0


@dataclass(frozen=True, slots=True)
class CostParams:
    walking_speed: float = 100.0
    """Distance units per second."""
    handling_time: float = 1.5
    """Seconds per item handled."""
    openings: Mapping[str, Mapping[int | None, OpeningProfile]] = field(default_factory=dict)
    """``kind -> level -> profile``; the ``None`` level is the default for the kind."""
    level_penalty: Mapping[int, float] = field(
        default_factory=lambda: {0: 1.0, 1: 0.1, 2: 1.5, 3: 4.0}
    )
    weight_factor: float = 0.5
    max_carry: int | None = None
    nest_factor: float = 0.25
    nest_factors: Mapping[str, float] = field(default_factory=dict)
    weights: Mapping[str, float] = field(
        default_factory=lambda: {
            "distance": 0.2,
            "openings": 0.2,
            "time": 0.35,
            "effort": 0.25,
            "extra_trips": 0.0,
            "clearings": 0.0,
        }
    )
    normalizers: Mapping[str, float] | None = None

    def opening(self, kind: str, level: int) -> OpeningProfile | None:
        by_level = self.openings.get(kind)
        if by_level is None:
            return None
        return by_level.get(level) or by_level.get(None)

    def penalty(self, level: int) -> float:
        return self.level_penalty.get(level, 0.0)


@dataclass(frozen=True, slots=True)
class Selector:
    """Matches items by id, category or tag. An empty selector matches nothing."""

    items: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()

    def matches(self, item: Item) -> bool:
        return (
            item.id in self.items or item.category in self.categories or bool(self.tags & item.tags)
        )


class HardKind(StrEnum):
    REQUIRE_TAG = "require_tag"
    FORBID_TAG = "forbid_tag"
    MIN_DISTANCE = "min_distance"
    MAX_DISTANCE = "max_distance"
    MIN_LEVEL = "min_level"
    MAX_LEVEL = "max_level"
    SEPARATE = "separate"


@dataclass(frozen=True, slots=True)
class HardConstraint:
    id: str
    kind: HardKind
    select: Selector
    tag: str | None = None
    reference: str | None = None
    """Location id that distances are measured to."""
    distance: float | None = None
    level: int | None = None
    other: Selector | None = None
    """Second selector of a ``separate`` constraint."""


@dataclass(frozen=True, slots=True)
class SoftConstraint:
    """Items that should be kept together."""

    id: str
    select: Selector
    mode: str = "same_location"
    """``same_location`` or ``nearby``."""
    penalty: float = 0.01
    radius: float = 0.0


@dataclass(frozen=True, slots=True)
class Problem:
    locations: Mapping[str, Location]
    items: Mapping[str, Item]
    routines: tuple[Routine, ...]
    params: CostParams = CostParams()
    obstructions: tuple[Obstruction, ...] = ()
    hard: tuple[HardConstraint, ...] = ()
    soft: tuple[SoftConstraint, ...] = ()
    pins: Mapping[str, str] = field(default_factory=dict)

    def with_frequencies(self, frequencies: Mapping[str, float]) -> Problem:
        routines = tuple(
            replace(r, frequency=frequencies.get(r.id, r.frequency)) for r in self.routines
        )
        return replace(self, routines=routines)

    def with_pins(self, pins: Mapping[str, str]) -> Problem:
        return replace(self, pins=dict(pins))

    def derived_frequencies(self) -> dict[str, float]:
        """Weekly usage of each item: the summed frequency of the routines that mention it."""
        freq = dict.fromkeys(self.items, 0.0)
        for routine in self.routines:
            for item_id in routine.item_ids():
                if item_id in freq:
                    freq[item_id] += routine.frequency
        return freq
