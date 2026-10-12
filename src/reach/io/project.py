"""Loads a set of input files into a validated project and a core ``Problem``."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from reach.core.compile import unary_violation
from reach.core.model import (
    Assignment,
    CostParams,
    HardConstraint,
    HardKind,
    Item,
    Location,
    Obstruction,
    OpeningProfile,
    Point,
    Problem,
    Routine,
    Selector,
    SoftConstraint,
    Step,
    StepKind,
    TransitionCost,
)
from reach.io.csv_loader import load_assignment, load_items
from reach.io.errors import ProjectError, ValidationReport
from reach.io.schemas import (
    ConstraintsFile,
    ItemRow,
    ObstructionModel,
    RoutineModel,
    RoutinesFile,
    SelectorModel,
    SpaceFile,
    derive_level,
)
from reach.io.yaml_loader import LineMap, load_yaml, report_validation_error

SPACE_FILES = ("kitchen.yaml", "space.yaml")
ITEMS_FILE = "items.csv"
CONSTRAINTS_FILE = "constraints.yaml"
ROUTINES_FILE = "routines.yaml"
CURRENT_FILE = "current_assignment.csv"
KNOWN_FILES = (*SPACE_FILES, ITEMS_FILE, CONSTRAINTS_FILE, ROUTINES_FILE, CURRENT_FILE)


@dataclass(slots=True)
class Project:
    texts: dict[str, str]
    report: ValidationReport
    space: SpaceFile | None = None
    items: list[ItemRow] = field(default_factory=list)
    constraints: ConstraintsFile | None = None
    routines: RoutinesFile | None = None
    problem: Problem | None = None
    current_assignment: Assignment | None = None
    space_file: str = SPACE_FILES[0]
    levels: dict[str, int] = field(default_factory=dict)
    """Resolved level of every compartment."""
    blocks: dict[str, list[str]] = field(default_factory=dict)
    """Resolved location ids blocked by every obstruction."""

    @property
    def ok(self) -> bool:
        return self.report.ok and self.problem is not None

    def require_problem(self) -> Problem:
        if self.problem is None:
            raise ProjectError(self.report)
        return self.problem

    def layout(self) -> dict[str, Any]:
        """Geometry for drawing the floor plan and the wall elevations."""
        s = self.space
        if s is None:
            return {}
        walls = [
            {
                "id": w.id,
                "name": w.name or w.id,
                "start": list(w.start),
                "end": list(w.end),
                "length": math.dist(w.start, w.end),
            }
            for w in s.walls
        ]

        def placement(obj) -> dict[str, Any] | None:
            if obj.wall is None:
                return None
            return {
                "wall": obj.wall,
                "offset_cm": obj.offset_cm,
                "width_cm": obj.width_cm,
                "bottom_cm": obj.bottom_cm,
                "top_cm": obj.top_cm,
            }

        return {
            "name": s.name,
            "outline": [list(p) for p in s.room.outline],
            "walls": walls,
            "pois": [
                {
                    "id": p.id,
                    "type": p.type,
                    "name": p.name or p.id,
                    "position": list(p.position),
                    "placement": placement(p),
                }
                for p in s.pois
            ],
            "zones": [
                {
                    "id": z.id,
                    "type": z.type,
                    "name": z.name or z.id,
                    "position": list(z.position),
                    "size": list(z.size) if z.size else None,
                    "placement": placement(z),
                }
                for z in s.zones
            ],
            "compartments": [
                {
                    "id": c.id,
                    "type": c.type,
                    "name": c.name or c.id,
                    "access_point": list(c.access_point),
                    "placement": placement(c),
                    "level": self.levels.get(c.id, 1),
                    "level_override": c.level is not None,
                    "volume_l": c.volume_l,
                    "capacity_l": c.volume_l * c.max_fill_factor,
                    "max_weight_kg": c.max_weight_kg,
                    "tags": c.tags,
                }
                for c in s.compartments
            ],
            "obstructions": [
                {
                    "id": o.id,
                    "name": o.name or o.id,
                    "default_state": o.default_state,
                    "blocks_when": o.blocks_when,
                    "blocks": self.blocks.get(o.id, []),
                    "wall_range": o.wall_range.model_dump() if o.wall_range else None,
                    "position": list(o.position) if o.position else None,
                    "clear": o.clear.model_dump(),
                    "restore": o.restore.model_dump(),
                }
                for o in s.obstructions
            ],
        }


def read_project_files(path: Path) -> dict[str, str]:
    """Read the known input files that exist in ``path``."""
    texts = {}
    for name in KNOWN_FILES:
        f = path / name
        if f.is_file():
            texts[name] = f.read_text(encoding="utf-8")
    return texts


def load_project(path: str | Path) -> Project:
    path = Path(path)
    if not path.is_dir():
        report = ValidationReport()
        report.error(str(path), "not a directory")
        return Project({}, report)
    return load_project_from_texts(read_project_files(path))


def load_project_from_texts(
    texts: Mapping[str, str], extra_pins: Mapping[str, str | None] | None = None
) -> Project:
    """Validate the files and build the problem.

    ``extra_pins`` overrides ``fixed_location`` per item; ``None`` removes a pin.
    """
    report = ValidationReport()
    project = Project(dict(texts), report)

    space_file = next((n for n in SPACE_FILES if n in texts), None)
    if space_file is None:
        report.error(SPACE_FILES[0], "file is missing")
    else:
        project.space_file = space_file
    for required in (ITEMS_FILE, ROUTINES_FILE):
        if required not in texts:
            report.error(required, "file is missing")

    space_lines = LineMap()
    if space_file is not None:
        project.space, space_lines = _load_model(texts[space_file], space_file, SpaceFile, report)
    item_lines: dict[str, int] = {}
    if ITEMS_FILE in texts:
        project.items, item_lines = load_items(texts[ITEMS_FILE], ITEMS_FILE, report)
    constraint_lines = LineMap()
    if CONSTRAINTS_FILE in texts:
        project.constraints, constraint_lines = _load_model(
            texts[CONSTRAINTS_FILE], CONSTRAINTS_FILE, ConstraintsFile, report
        )
    else:
        project.constraints = ConstraintsFile()
    routine_lines = LineMap()
    if ROUTINES_FILE in texts:
        project.routines, routine_lines = _load_model(
            texts[ROUTINES_FILE], ROUTINES_FILE, RoutinesFile, report
        )

    space = project.space
    if space is None:
        return project

    # --- the space ---
    f = project.space_file
    walls = {}
    for n, w in enumerate(space.walls):
        if w.id in walls:
            report.error(f, f"duplicate wall id '{w.id}'", space_lines.find(("walls", n)), "id")
        walls[w.id] = w
        if math.dist(w.start, w.end) <= 0:
            report.error(f, "start and end are the same point", space_lines.find(("walls", n)))

    locations: dict[str, Location] = {}
    spans: dict[str, tuple[str, float, float]] = {}  # location id -> (wall, from, to)

    def add_location(section: str, n: int, obj, loc: Location) -> None:
        line = space_lines.find((section, n))
        if loc.id in locations:
            report.error(
                f,
                f"duplicate id '{loc.id}' (ids are shared by pois, zones and compartments)",
                line,
                "id",
            )
            return
        locations[loc.id] = loc
        if obj.wall is None:
            return
        wall = walls.get(obj.wall)
        if wall is None:
            report.error(
                f,
                f"unknown wall '{obj.wall}'",
                space_lines.find((section, n, "wall")),
                f"{section}[{n}].wall",
            )
            return
        length = math.dist(wall.start, wall.end)
        if obj.offset_cm + obj.width_cm > length + 1e-6:
            report.error(
                f,
                f"extends to {obj.offset_cm + obj.width_cm:g} cm but wall '{wall.id}' is "
                f"{length:g} cm long",
                space_lines.find((section, n, "offset_cm")),
                f"{section}[{n}].offset_cm",
            )
        spans[loc.id] = (obj.wall, obj.offset_cm, obj.offset_cm + obj.width_cm)

    def geometry(obj) -> dict[str, Any] | None:
        if obj.wall is None:
            return None
        return {
            "wall": obj.wall,
            "offset_cm": obj.offset_cm,
            "width_cm": obj.width_cm,
            "bottom_cm": obj.bottom_cm,
            "top_cm": obj.top_cm,
        }

    for n, p in enumerate(space.pois):
        add_location(
            "pois",
            n,
            p,
            Location(
                p.id, Point(*p.position), kind=p.type, name=p.name or p.id, geometry=geometry(p)
            ),
        )
    for n, z in enumerate(space.zones):
        add_location(
            "zones",
            n,
            z,
            Location(
                z.id, Point(*z.position), kind=z.type, name=z.name or z.id, geometry=geometry(z)
            ),
        )
    rects: dict[str, list[tuple[str, float, float, float, float]]] = {}
    for n, c in enumerate(space.compartments):
        level = (
            c.level
            if c.level is not None
            else derive_level(c.bottom_cm, c.top_cm, space.level_ranges)
        )
        project.levels[c.id] = level
        add_location(
            "compartments",
            n,
            c,
            Location(
                c.id,
                Point(*c.access_point),
                kind=c.type,
                level=level,
                assignable=True,
                capacity_volume=c.volume_l * c.max_fill_factor,
                capacity_weight=c.max_weight_kg,
                tags=frozenset(c.tags),
                name=c.name or c.id,
                geometry=geometry(c),
                area=c.wall,
            ),
        )
        for other, o0, o1, b0, b1 in rects.get(c.wall, []):
            if (
                c.offset_cm < o1 - 1e-6
                and o0 < c.offset_cm + c.width_cm - 1e-6
                and c.bottom_cm < b1 - 1e-6
                and b0 < c.top_cm - 1e-6
            ):
                report.warning(
                    f,
                    f"'{c.id}' overlaps '{other}' on wall '{c.wall}'",
                    space_lines.find(("compartments", n)),
                )
        rects.setdefault(c.wall, []).append(
            (c.id, c.offset_cm, c.offset_cm + c.width_cm, c.bottom_cm, c.top_cm)
        )
    if not space.compartments:
        report.error(
            f, "no compartments defined", space_lines.find(("compartments",)), "compartments"
        )

    obstructions: list[Obstruction] = []
    seen_obs: set[str] = set()
    for n, o in enumerate(space.obstructions):
        line = space_lines.find(("obstructions", n))
        if o.id in seen_obs:
            report.error(f, f"duplicate obstruction id '{o.id}'", line, "id")
        seen_obs.add(o.id)
        blocked = resolve_blocks(o, spans)
        for k, lid in enumerate(o.blocks):
            if lid not in locations:
                report.error(
                    f,
                    f"unknown compartment, POI or zone '{lid}'",
                    space_lines.find(("obstructions", n, "blocks", k)),
                    f"obstructions[{n}].blocks[{k}]",
                )
        if o.wall_range is not None:
            if o.wall_range.wall not in walls:
                report.error(
                    f,
                    f"unknown wall '{o.wall_range.wall}'",
                    space_lines.find(("obstructions", n, "wall_range", "wall")),
                    f"obstructions[{n}].wall_range.wall",
                )
            elif not any(spans[lid][0] == o.wall_range.wall for lid in blocked if lid in spans):
                report.warning(
                    f,
                    "wall_range covers no compartment, POI or zone",
                    space_lines.find(("obstructions", n, "wall_range")),
                    f"obstructions[{n}].wall_range",
                )
        blocked = [lid for lid in blocked if lid in locations]
        project.blocks[o.id] = blocked
        obstructions.append(
            Obstruction(
                o.id,
                o.default_state,
                o.blocks_when,
                frozenset(blocked),
                TransitionCost(o.clear.time_s, o.clear.openings),
                TransitionCost(o.restore.time_s, o.restore.openings),
                Point(*o.position) if o.position else None,
                o.name or o.id,
            )
        )

    cost = space.cost
    openings: dict[str, dict[int | None, OpeningProfile]] = {}
    for kind, by in cost.openings.items():
        table: dict[int | None, OpeningProfile] = {
            None: OpeningProfile(by.default.time_s, by.default.cost)
        }
        for level, oc in by.levels.items():
            table[level] = OpeningProfile(oc.time_s, oc.cost)
        openings[kind] = table
    for kind in sorted({c.type for c in space.compartments} - set(openings)):
        report.warning(
            f,
            f"no opening cost for type '{kind}'; opening it is free",
            space_lines.find(("cost", "openings")),
            "cost.openings",
        )
    origin = cost.occasional_items.origin
    if isinstance(origin, str) and origin not in locations:
        report.error(
            f,
            f"unknown POI or zone '{origin}'",
            space_lines.find(("cost", "occasional_items", "origin")),
            "cost.occasional_items.origin",
        )
    params = CostParams(
        walking_speed=cost.walking_speed_cm_s,
        handling_time=cost.handling_time_s,
        openings=openings,
        level_penalty=dict(cost.level_penalty),
        weight_factor=cost.weight_factor,
        max_carry=cost.max_carry,
        nest_factor=cost.nesting.default_factor,
        nest_factors=dict(cost.nesting.groups),
        weights=cost.weights.model_dump(),
        normalizers=dict(cost.normalizers) if cost.normalizers else None,
        occasional=cost.occasional_items.enabled,
        occasional_origin=(origin if isinstance(origin, str) or origin is None else Point(*origin)),
    )
    if sum(params.weights.values()) <= 0:
        report.error(f, "all weights are 0", space_lines.find(("cost", "weights")), "cost.weights")

    # --- items ---
    items: dict[str, Item] = {}
    pins: dict[str, str] = {}
    for row in project.items:
        items[row.id] = Item(
            row.id,
            row.name or row.id,
            row.category,
            row.volume_l,
            row.quantity,
            row.weight_kg,
            row.estimated_frequency_per_week,
            row.nest_group,
            frozenset(row.tags),
        )
        if row.fixed_location:
            if row.fixed_location not in locations:
                report.error(
                    ITEMS_FILE,
                    f"unknown location '{row.fixed_location}'",
                    item_lines.get(row.id),
                    "fixed_location",
                )
            else:
                pins[row.id] = row.fixed_location
    for item_id, loc_id in (extra_pins or {}).items():
        if item_id not in items:
            continue
        if loc_id is None:
            pins.pop(item_id, None)
        elif loc_id in locations:
            pins[item_id] = loc_id
    if ITEMS_FILE in texts and not items and not any(i.file == ITEMS_FILE for i in report.errors):
        report.error(ITEMS_FILE, "no items")

    # --- constraints ---
    categories = {i.category for i in items.values()}
    item_tags = {t for i in items.values() for t in i.tags}
    location_tags = {t for loc in locations.values() for t in loc.tags}

    def selector(model: SelectorModel, loc: tuple) -> Selector:
        for k, item_id in enumerate(model.items):
            if item_id not in items:
                report.error(
                    CONSTRAINTS_FILE,
                    f"unknown item '{item_id}'",
                    constraint_lines.find((*loc, "items", k)),
                    _path(loc, "items", k),
                )
        for k, cat in enumerate(model.categories):
            if cat not in categories:
                report.warning(
                    CONSTRAINTS_FILE,
                    f"no item has category '{cat}'",
                    constraint_lines.find((*loc, "categories", k)),
                    _path(loc, "categories", k),
                )
        for k, tag in enumerate(model.tags):
            if tag not in item_tags:
                report.warning(
                    CONSTRAINTS_FILE,
                    f"no item has tag '{tag}'",
                    constraint_lines.find((*loc, "tags", k)),
                    _path(loc, "tags", k),
                )
        return Selector(frozenset(model.items), frozenset(model.categories), frozenset(model.tags))

    hard: list[HardConstraint] = []
    soft: list[SoftConstraint] = []
    seen_ids: set[str] = set()
    cons = project.constraints or ConstraintsFile()
    for n, h in enumerate(cons.hard):
        loc = ("hard", n)
        if h.id in seen_ids:
            report.error(
                CONSTRAINTS_FILE,
                f"duplicate constraint id '{h.id}'",
                constraint_lines.find(loc),
                "id",
            )
        seen_ids.add(h.id)
        sel = selector(h.select, (*loc, "select"))
        kind = HardKind(h.type)
        kw: dict[str, Any] = {}
        if kind in (HardKind.REQUIRE_TAG, HardKind.FORBID_TAG):
            kw["tag"] = h.tag
            if h.tag not in location_tags:
                report.warning(
                    CONSTRAINTS_FILE,
                    f"no compartment has tag '{h.tag}'",
                    constraint_lines.find((*loc, "tag")),
                    _path(loc, "tag"),
                )
        elif kind in (HardKind.MIN_DISTANCE, HardKind.MAX_DISTANCE):
            kw["reference"] = h.poi
            kw["distance"] = h.distance_cm
            if h.poi not in locations:
                report.error(
                    CONSTRAINTS_FILE,
                    f"unknown POI or zone '{h.poi}'",
                    constraint_lines.find((*loc, "poi")),
                    _path(loc, "poi"),
                )
        elif kind in (HardKind.MIN_LEVEL, HardKind.MAX_LEVEL):
            kw["level"] = h.level
        else:
            kw["other"] = selector(h.other, (*loc, "from"))
        hard.append(HardConstraint(h.id, kind, sel, **kw))
    for n, s in enumerate(cons.soft):
        loc = ("soft", n)
        if s.id in seen_ids:
            report.error(
                CONSTRAINTS_FILE,
                f"duplicate constraint id '{s.id}'",
                constraint_lines.find(loc),
                "id",
            )
        seen_ids.add(s.id)
        sel = selector(s.select, (*loc, "select"))
        if sum(1 for i in items.values() if sel.matches(i)) < 2:
            report.warning(
                CONSTRAINTS_FILE,
                "selects fewer than two items, so it has no effect",
                constraint_lines.find(loc),
                _path(loc, "select"),
            )
        mode = "nearby" if s.mode == "nearby" else "same_location"
        soft.append(SoftConstraint(s.id, sel, mode, s.penalty, s.radius_cm))

    # --- routines ---
    routines: list[Routine] = []
    seen_routines: set[str] = set()
    obstruction_ids = {o.id for o in obstructions}
    for n, r in enumerate(project.routines.routines if project.routines else []):
        routine = build_routine(
            r, ("routines", n), items, locations, obstruction_ids, report, routine_lines
        )
        if r.id in seen_routines:
            report.error(
                ROUTINES_FILE,
                f"duplicate routine id '{r.id}'",
                routine_lines.find(("routines", n)),
                "id",
            )
        seen_routines.add(r.id)
        routines.append(routine)
    if project.routines is not None and not routines:
        report.warning(ROUTINES_FILE, "no routines: every assignment costs the same")

    if not report.ok:
        return project

    problem = Problem(
        locations=locations,
        items=items,
        routines=tuple(routines),
        params=params,
        obstructions=tuple(obstructions),
        hard=tuple(hard),
        soft=tuple(soft),
        pins=pins,
    )
    for item_id, loc_id in pins.items():
        loc = locations[loc_id]
        if loc.assignable:
            hc = unary_violation(problem, items[item_id], loc)
            if hc is not None:
                report.warning(
                    ITEMS_FILE,
                    f"pinned to '{loc_id}', which violates constraint '{hc.id}'; the pin wins",
                    item_lines.get(item_id),
                    "fixed_location",
                )
    project.problem = problem

    # --- current assignment (optional) ---
    if CURRENT_FILE in texts:
        current, lines = load_assignment(texts[CURRENT_FILE], CURRENT_FILE, report)
        usable = True
        for item_id, loc_id in current.items():
            if item_id not in items:
                report.warning(
                    CURRENT_FILE,
                    f"unknown item '{item_id}' is ignored",
                    lines.get(item_id),
                    "item_id",
                )
            elif loc_id not in locations:
                report.warning(
                    CURRENT_FILE, f"unknown location '{loc_id}'", lines.get(item_id), "location_id"
                )
                usable = False
        complete = {i: loc for i, loc in current.items() if i in items}
        for item_id, loc_id in pins.items():
            complete.setdefault(item_id, loc_id)
        missing = [i for i in items if i not in complete]
        if missing:
            shown = ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else "")
            report.warning(
                CURRENT_FILE,
                f"{len(missing)} item(s) have no current location ({shown}); "
                "the comparison is disabled",
            )
            usable = False
        if usable:
            project.current_assignment = complete
    return project


def build_routine(
    r: RoutineModel,
    loc: tuple,
    items: Mapping[str, Any],
    locations: Mapping[str, Any],
    obstruction_ids: set[str],
    report: ValidationReport,
    lines: LineMap | None = None,
    file: str = ROUTINES_FILE,
) -> Routine:
    """Check the references of one routine and convert it to the core type."""
    lines = lines or LineMap()

    def err(message: str, *sub) -> None:
        report.error(file, message, lines.find((*loc, *sub)), _path(loc, *sub))

    if isinstance(r.start, str):
        if r.start not in locations:
            err(f"unknown POI or zone '{r.start}'", "start")
        start: str | Point = r.start
    else:
        start = Point(*r.start)
    for oid in r.initial_states:
        if oid not in obstruction_ids:
            err(f"unknown obstruction '{oid}'", "initial_states", oid)
    steps = []
    for k, s in enumerate(r.steps):
        if s.item is not None and s.item not in items:
            err(f"unknown item '{s.item}'", "steps", k)
        if s.at is not None and s.at not in locations:
            err(f"unknown POI or zone '{s.at}'", "steps", k)
        if s.obstruction is not None and s.obstruction not in obstruction_ids:
            err(f"unknown obstruction '{s.obstruction}'", "steps", k)
        steps.append(Step(StepKind(s.kind), s.item, s.at, s.obstruction, s.state))
    return Routine(
        r.id, r.name or r.id, r.frequency_per_week, start, tuple(steps), dict(r.initial_states)
    )


def resolve_blocks(o: ObstructionModel, spans: Mapping[str, tuple[str, float, float]]) -> list[str]:
    """Explicit ``blocks`` plus everything whose span on the wall overlaps ``wall_range``."""
    out = list(dict.fromkeys(o.blocks))
    if o.wall_range is not None:
        wr = o.wall_range
        for lid, (wall, a, b) in spans.items():
            if wall == wr.wall and a < wr.to_cm - 1e-6 and wr.from_cm < b - 1e-6 and lid not in out:
                out.append(lid)
    return out


def _path(loc: tuple, *sub) -> str:
    from reach.io.yaml_loader import field_path

    return field_path((*loc, *sub))


def _load_model(text: str, file: str, model, report: ValidationReport):
    data, lines = load_yaml(text, file, report)
    if data is None:
        if not any(i.file == file for i in report.errors):
            report.error(file, "file is empty")
        return None, lines
    if not isinstance(data, dict):
        report.error(file, "expected a mapping at the top level", 1)
        return None, lines
    try:
        return model.model_validate(data), lines
    except ValidationError as exc:
        report_validation_error(exc, file, lines, report)
        return None, lines
