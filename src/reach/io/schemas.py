"""Pydantic models of the input files. This is where the kitchen vocabulary lives."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

XY = tuple[float, float]
State = Literal["open", "closed"]
STEP_KINDS = ("go_to", "take", "use", "put_down", "store", "set_state")
COMPARTMENT_TYPES = ("drawer", "door", "pantry_shelf")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- kitchen.yaml ---------------------------------------------------------------------------


class Wall(Strict):
    id: str
    name: str = ""
    start: XY
    end: XY


class LevelRange(Strict):
    level: int = Field(ge=0, le=3)
    max_cm: float | None = None


class WallPlacement(Strict):
    """Rectangle on a wall elevation. Optional for POIs and zones."""

    wall: str | None = None
    offset_cm: float | None = Field(default=None, ge=0)
    width_cm: float | None = Field(default=None, gt=0)
    bottom_cm: float | None = Field(default=None, ge=0)
    top_cm: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _complete(self):
        given = [
            v is not None
            for v in (self.wall, self.offset_cm, self.width_cm, self.bottom_cm, self.top_cm)
        ]
        if any(given) and not all(given):
            raise ValueError(
                "wall, offset_cm, width_cm, bottom_cm and top_cm must be given together"
            )
        if all(given) and self.bottom_cm >= self.top_cm:  # type: ignore[operator]
            raise ValueError("bottom_cm must be below top_cm")
        return self


class Poi(WallPlacement):
    id: str
    type: str
    name: str = ""
    position: XY


class Zone(WallPlacement):
    id: str
    type: str = "countertop"
    name: str = ""
    position: XY
    size: XY | None = None


class Compartment(Strict):
    id: str
    type: Literal["drawer", "door", "pantry_shelf"]
    name: str = ""
    access_point: XY
    wall: str
    offset_cm: float = Field(ge=0)
    width_cm: float = Field(gt=0)
    bottom_cm: float = Field(ge=0)
    top_cm: float = Field(gt=0)
    level: int | None = Field(default=None, ge=0, le=3)
    volume_l: float = Field(gt=0)
    max_fill_factor: float = Field(default=0.8, gt=0, le=1)
    max_weight_kg: float | None = Field(default=None, gt=0)
    tags: list[str] = []

    @model_validator(mode="after")
    def _heights(self):
        if self.bottom_cm >= self.top_cm:
            raise ValueError("bottom_cm must be below top_cm")
        return self


class Transition(Strict):
    time_s: float = Field(default=0.0, ge=0)
    openings: float = Field(default=0.0, ge=0)


class WallRange(Strict):
    wall: str
    from_cm: float = Field(ge=0)
    to_cm: float = Field(gt=0)

    @model_validator(mode="after")
    def _order(self):
        if self.from_cm >= self.to_cm:
            raise ValueError("from_cm must be less than to_cm")
        return self


class ObstructionModel(Strict):
    id: str
    name: str = ""
    default_state: State
    blocks_when: State
    blocks: list[str] = []
    wall_range: WallRange | None = None
    clear: Transition = Transition()
    restore: Transition = Transition()
    position: XY | None = None

    @model_validator(mode="after")
    def _blocks_something(self):
        if not self.blocks and self.wall_range is None:
            raise ValueError("give 'blocks', 'wall_range' or both")
        return self


class OpeningCost(Strict):
    time_s: float = Field(default=0.0, ge=0)
    cost: float = Field(default=1.0, ge=0)


class OpeningByType(Strict):
    default: OpeningCost = OpeningCost()
    levels: dict[int, OpeningCost] = {}


def _default_openings() -> dict[str, OpeningByType]:
    return {
        "drawer": OpeningByType(default=OpeningCost(time_s=2.0, cost=1.0)),
        "door": OpeningByType(
            default=OpeningCost(time_s=2.5, cost=1.0),
            levels={3: OpeningCost(time_s=12.0, cost=2.0)},
        ),
        "pantry_shelf": OpeningByType(
            default=OpeningCost(time_s=2.5, cost=1.0),
            levels={3: OpeningCost(time_s=12.0, cost=2.0)},
        ),
        "fridge": OpeningByType(default=OpeningCost(time_s=3.0, cost=1.0)),
        "freezer": OpeningByType(default=OpeningCost(time_s=3.0, cost=1.0)),
    }


class Nesting(Strict):
    default_factor: float = Field(default=0.25, ge=0, le=1)
    groups: dict[str, float] = {}


class Weights(Strict):
    distance: float = Field(default=0.2, ge=0)
    openings: float = Field(default=0.2, ge=0)
    time: float = Field(default=0.35, ge=0)
    effort: float = Field(default=0.25, ge=0)
    extra_trips: float = Field(default=0.0, ge=0)
    clearings: float = Field(default=0.0, ge=0)


class OccasionalItems(Strict):
    """Generic access cost for items that no routine mentions."""

    enabled: bool = True
    origin: str | XY | None = None
    """A POI or zone id, or a point. Defaults to the centroid of every location."""


class CostModel(Strict):
    walking_speed_cm_s: float = Field(default=100.0, gt=0)
    handling_time_s: float = Field(default=1.5, ge=0)
    openings: dict[str, OpeningByType] = Field(default_factory=_default_openings)
    level_penalty: dict[int, float] = {0: 1.0, 1: 0.1, 2: 1.5, 3: 4.0}
    weight_factor: float = Field(default=0.5, ge=0)
    max_carry: int | None = Field(default=None, ge=1)
    nesting: Nesting = Nesting()
    weights: Weights = Weights()
    normalizers: dict[str, float] | None = None
    occasional_items: OccasionalItems = OccasionalItems()


def default_level_ranges() -> list[LevelRange]:
    return [
        LevelRange(level=0, max_cm=60),
        LevelRange(level=1, max_cm=160),
        LevelRange(level=2, max_cm=195),
        LevelRange(level=3),
    ]


class Room(Strict):
    outline: list[XY] = []
    """Floor polygon, for drawing only."""


class SpaceFile(Strict):
    name: str = "Space"
    room: Room = Room()
    walls: list[Wall] = []
    level_ranges: list[LevelRange] = Field(default_factory=default_level_ranges)
    pois: list[Poi] = []
    zones: list[Zone] = []
    compartments: list[Compartment] = []
    obstructions: list[ObstructionModel] = []
    cost: CostModel = CostModel()

    @model_validator(mode="after")
    def _ranges(self):
        bounds = [r.max_cm for r in self.level_ranges]
        if not self.level_ranges:
            raise ValueError("level_ranges must not be empty")
        if any(b is None for b in bounds[:-1]):
            raise ValueError("level_ranges: only the last entry may omit max_cm")
        finite = [b for b in bounds if b is not None]
        if finite != sorted(finite):
            raise ValueError("level_ranges must be ordered by max_cm")
        return self


def derive_level(bottom_cm: float, top_cm: float, ranges: list[LevelRange]) -> int:
    """Level of a compartment from its centre height."""
    centre = (bottom_cm + top_cm) / 2
    for r in ranges:
        if r.max_cm is None or centre < r.max_cm:
            return r.level
    return ranges[-1].level


# --- items.csv ------------------------------------------------------------------------------

ITEM_COLUMNS = (
    "id",
    "name",
    "category",
    "volume_l",
    "quantity",
    "weight_kg",
    "estimated_frequency_per_week",
    "nest_group",
    "fixed_location",
    "tags",
    "comments",
)


class ItemRow(Strict):
    id: str = Field(min_length=1)
    name: str = ""
    category: str = ""
    volume_l: float = Field(default=0.0, ge=0)
    quantity: int = Field(default=1, ge=1)
    weight_kg: float = Field(default=0.0, ge=0)
    estimated_frequency_per_week: float = Field(default=0.0, ge=0)
    nest_group: str | None = None
    fixed_location: str | None = None
    tags: list[str] = []
    comments: str = ""


# --- constraints.yaml -----------------------------------------------------------------------


class SelectorModel(Strict):
    items: list[str] = []
    categories: list[str] = []
    tags: list[str] = []

    @model_validator(mode="after")
    def _non_empty(self):
        if not (self.items or self.categories or self.tags):
            raise ValueError("select at least one of items, categories or tags")
        return self


class _Hard(Strict):
    id: str
    select: SelectorModel
    note: str = ""


class RequireTag(_Hard):
    type: Literal["require_tag"]
    tag: str


class ForbidTag(_Hard):
    type: Literal["forbid_tag"]
    tag: str


class MinDistance(_Hard):
    type: Literal["min_distance"]
    poi: str
    distance_cm: float = Field(ge=0)


class MaxDistance(_Hard):
    type: Literal["max_distance"]
    poi: str
    distance_cm: float = Field(ge=0)


class MinLevel(_Hard):
    type: Literal["min_level"]
    level: int = Field(ge=0, le=3)


class MaxLevel(_Hard):
    type: Literal["max_level"]
    level: int = Field(ge=0, le=3)


class Separate(_Hard):
    type: Literal["separate"]
    other: SelectorModel = Field(alias="from")


HardModel = Annotated[
    RequireTag | ForbidTag | MinDistance | MaxDistance | MinLevel | MaxLevel | Separate,
    Field(discriminator="type"),
]


class Together(Strict):
    id: str
    type: Literal["together"] = "together"
    select: SelectorModel
    mode: Literal["same_compartment", "nearby"] = "same_compartment"
    penalty: float = Field(default=0.01, ge=0)
    radius_cm: float = Field(default=0.0, ge=0)
    note: str = ""


class ConstraintsFile(Strict):
    hard: list[HardModel] = []
    soft: list[Together] = []


# --- routines.yaml --------------------------------------------------------------------------


class StepModel(Strict):
    """One step, written as a single-key mapping such as ``take: mug``."""

    kind: Literal["go_to", "take", "use", "put_down", "store", "set_state"]
    item: str | None = None
    at: str | None = None
    obstruction: str | None = None
    state: State | None = None

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, value: Any) -> Any:
        if isinstance(value, dict) and "kind" in value:
            return value
        if not isinstance(value, dict) or len(value) != 1:
            raise ValueError(
                "a step is a single 'kind: value' pair, with kind one of " + ", ".join(STEP_KINDS)
            )
        kind, arg = next(iter(value.items()))
        if kind not in STEP_KINDS:
            raise ValueError(f"unknown step '{kind}'; expected one of " + ", ".join(STEP_KINDS))
        if kind == "go_to":
            if not isinstance(arg, str) or not arg.strip():
                raise ValueError("go_to needs a POI or zone id")
            return {"kind": kind, "at": arg.strip()}
        if kind in ("take", "store"):
            if isinstance(arg, dict):
                arg = arg.get("item")
            if not isinstance(arg, str) or not arg.strip() or " " in arg.strip():
                raise ValueError(f"{kind} needs a single item id")
            return {"kind": kind, "item": arg.strip()}
        if kind in ("use", "put_down"):
            if isinstance(arg, dict):
                item, at = arg.get("item"), arg.get("at")
            elif isinstance(arg, str):
                parts = arg.split()
                if len(parts) != 3 or parts[1] != "at":
                    raise ValueError(f"{kind} is written '<item_id> at <poi_or_zone>'")
                item, at = parts[0], parts[2]
            else:
                item = at = None
            if not item or not at:
                raise ValueError(f"{kind} is written '<item_id> at <poi_or_zone>'")
            return {"kind": kind, "item": item, "at": at}
        if isinstance(arg, dict):
            obstruction, state = arg.get("obstruction"), arg.get("state")
        elif isinstance(arg, str) and len(arg.split()) == 2:
            obstruction, state = arg.split()
        else:
            obstruction = state = None
        if not obstruction or not state:
            raise ValueError("set_state is written '<obstruction_id> open' or '... closed'")
        return {"kind": kind, "obstruction": obstruction, "state": state}

    def to_yaml(self) -> dict[str, str]:
        if self.kind == "go_to":
            return {"go_to": self.at or ""}
        if self.kind in ("take", "store"):
            return {self.kind: self.item or ""}
        if self.kind == "set_state":
            return {"set_state": f"{self.obstruction} {self.state}"}
        return {self.kind: f"{self.item} at {self.at}"}


class RoutineModel(Strict):
    id: str = Field(min_length=1)
    name: str = ""
    description: str = ""
    frequency_per_week: float = Field(ge=0)
    start: str | XY
    initial_states: dict[str, State] = {}
    steps: list[StepModel] = Field(min_length=1)

    def to_yaml(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "name": self.name}
        if self.description:
            out["description"] = self.description
        out["frequency_per_week"] = round(self.frequency_per_week, 3)
        out["start"] = self.start if isinstance(self.start, str) else list(self.start)
        if self.initial_states:
            out["initial_states"] = dict(self.initial_states)
        out["steps"] = [s.to_yaml() for s in self.steps]
        return out


class RoutinesFile(Strict):
    routines: list[RoutineModel] = []
