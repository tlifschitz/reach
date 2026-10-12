"""Small hand-built problems for the unit tests."""

from __future__ import annotations

from reach.core.compile import Compiled
from reach.core.model import (
    CostParams,
    Item,
    Location,
    OpeningProfile,
    Point,
    Problem,
    Routine,
    Step,
    StepKind,
)
from reach.core.simulate import simulate

PARAMS = CostParams(
    walking_speed=100.0,
    handling_time=1.0,
    openings={
        "drawer": {None: OpeningProfile(2.0, 1.0)},
        "door": {None: OpeningProfile(3.0, 1.0), 3: OpeningProfile(10.0, 2.0)},
    },
    level_penalty={0: 1.0, 1: 0.0, 2: 2.0, 3: 4.0},
    weight_factor=0.5,
)


def locations() -> dict[str, Location]:
    """A 300 x 400 room: storage along the bottom and right, a counter top-left.

    ::

        counter (0,400) ............ C (300,400)  door, level 3
           .                           .
        A (0,0) drawer, level 1 ..... B (300,0)   drawer, level 0
    """
    locs = [
        Location("A", Point(0, 0), "drawer", 1, True, 10.0),
        Location("B", Point(300, 0), "drawer", 0, True, 10.0),
        Location("C", Point(300, 400), "door", 3, True, 10.0),
        Location("counter", Point(0, 400), "zone"),
    ]
    return {loc.id: loc for loc in locs}


def items(*specs: tuple) -> dict[str, Item]:
    """``(id, volume, weight)`` tuples; volume and weight default to 1 and 0."""
    out = {}
    for spec in specs:
        item_id, volume, weight = (
            (*spec, 1.0, 0.0)[:3] if isinstance(spec, tuple) else (spec, 1.0, 0.0)
        )
        out[item_id] = Item(item_id, volume=volume, weight=weight)
    return out


def parse_steps(text: str) -> tuple[Step, ...]:
    """``"take x; use x at counter; set_state door closed"`` -> steps."""
    steps = []
    for part in text.split(";"):
        words = part.split()
        kind = StepKind(words[0])
        if kind is StepKind.GO_TO:
            steps.append(Step(kind, location=words[1]))
        elif kind is StepKind.SET_STATE:
            steps.append(Step(kind, obstruction=words[1], state=words[2]))
        elif kind in (StepKind.USE, StepKind.PUT_DOWN):
            steps.append(Step(kind, item=words[1], location=words[3]))
        else:
            steps.append(Step(kind, item=words[1]))
    return tuple(steps)


def routine(steps: str, start: str = "counter", frequency: float = 1.0, rid: str = "r", **kw):
    return Routine(rid, rid, frequency, start, parse_steps(steps), **kw)


def run(problem: Problem, assignment: dict[str, str], index: int = 0, trace=None):
    c = Compiled(problem)
    return simulate(c, c.routines[index], c.encode(assignment), trace)


def random_problem(
    seed: int = 0, n_items: int = 40, n_locs: int = 12, n_routines: int = 8
) -> Problem:
    """A reproducible mid-sized problem with every kind of constraint and an obstruction."""
    import random

    from reach.core.model import (
        HardConstraint,
        HardKind,
        Obstruction,
        Selector,
        SoftConstraint,
        TransitionCost,
    )

    rng = random.Random(seed)
    locs: dict[str, Location] = {}
    for n in range(n_locs):
        kind = "drawer" if n % 2 == 0 else "door"
        tags = frozenset({"wet"} if n < 2 else {"dry"})
        locs[f"L{n}"] = Location(
            f"L{n}",
            Point(rng.uniform(0, 400), rng.uniform(0, 300)),
            kind,
            level=n % 4,
            assignable=True,
            capacity_volume=20.0,
            capacity_weight=30.0,
            tags=tags,
        )
    for name, xy in {"stove": (200, 0), "sink": (50, 300), "counter": (300, 150)}.items():
        locs[name] = Location(name, Point(*xy), "zone")
    locs["fridge"] = Location("fridge", Point(400, 300), "fridge")

    its: dict[str, Item] = {}
    for n in range(n_items):
        category = "cleaning" if n < 3 else ("heavy" if n < 6 else "food" if n < 20 else "tool")
        its[f"i{n}"] = Item(
            f"i{n}",
            category=category,
            volume=rng.uniform(0.5, 4.0),
            quantity=rng.choice([1, 1, 2, 4]),
            weight=rng.uniform(0.1, 2.0),
            estimated_frequency=rng.uniform(0, 10),
            nest_group="stack" if 20 <= n < 26 else None,
        )
    pins = {"i39": "fridge", "i38": "L5"}

    fixed = ["stove", "sink", "counter", "fridge"]
    routines = []
    for r in range(n_routines):
        used = rng.sample(sorted(its), rng.randint(2, 6))
        steps = []
        for item in used:
            steps.append(Step(StepKind.TAKE, item=item))
            if rng.random() < 0.3:
                steps.append(Step(StepKind.GO_TO, location=rng.choice(fixed)))
        place = rng.choice(fixed)
        for item in used:
            steps.append(Step(StepKind.USE, item=item, location=place))
        for item in used[: len(used) // 2]:
            steps.append(Step(StepKind.STORE, item=item))
        routines.append(
            Routine(f"r{r}", f"routine {r}", rng.uniform(1, 14), rng.choice(fixed), tuple(steps))
        )

    hard = (
        HardConstraint(
            "clean_wet",
            HardKind.REQUIRE_TAG,
            Selector(categories=frozenset({"cleaning"})),
            tag="wet",
        ),
        HardConstraint(
            "food_dry", HardKind.FORBID_TAG, Selector(categories=frozenset({"food"})), tag="wet"
        ),
        HardConstraint(
            "heavy_low", HardKind.MAX_LEVEL, Selector(categories=frozenset({"heavy"})), level=1
        ),
        HardConstraint("i10_high", HardKind.MIN_LEVEL, Selector(items=frozenset({"i10"})), level=2),
        HardConstraint(
            "i11_far",
            HardKind.MIN_DISTANCE,
            Selector(items=frozenset({"i11"})),
            reference="stove",
            distance=80.0,
        ),
        HardConstraint(
            "i12_near",
            HardKind.MAX_DISTANCE,
            Selector(items=frozenset({"i12"})),
            reference="sink",
            distance=350.0,
        ),
        HardConstraint(
            "apart",
            HardKind.SEPARATE,
            Selector(categories=frozenset({"cleaning"})),
            other=Selector(categories=frozenset({"food", "tool"})),
        ),
    )
    soft = (
        SoftConstraint(
            "kit", Selector(items=frozenset({"i30", "i31", "i32"})), "same_location", 0.02
        ),
        SoftConstraint("near", Selector(items=frozenset({"i33", "i34"})), "nearby", 0.02, 50.0),
    )
    door = Obstruction(
        "door", "open", "open", frozenset({"L6", "L7"}), TransitionCost(3, 1), TransitionCost(3, 1)
    )
    return Problem(locs, its, tuple(routines), PARAMS, (door,), hard, soft, pins)
