"""Each hard-constraint type: the domain it produces and the violation it reports."""

from dataclasses import replace

import pytest

from reach.core.compile import Compiled
from reach.core.constraints import check_assignment
from reach.core.model import HardConstraint, HardKind, Item, Problem, Selector
from tests.helpers import PARAMS, locations


def setup(constraint: HardConstraint, items=None, **tags):
    locs = locations()
    for lid, t in tags.items():
        locs[lid] = replace(locs[lid], tags=frozenset(t))
    items = items or [Item("knife", category="sharp"), Item("spoon", category="cutlery")]
    p = Problem(locs, {i.id: i for i in items}, (), PARAMS, hard=(constraint,))
    return p, Compiled(p)


def domain(c: Compiled, item: str) -> set[str]:
    return {c.loc_ids[j] for j in c.domains[c.item_index[item]]}


SHARP = Selector(categories=frozenset({"sharp"}))


def test_require_tag():
    p, c = setup(HardConstraint("h", HardKind.REQUIRE_TAG, SHARP, tag="safe"), C={"safe"})
    assert domain(c, "knife") == {"C"}
    assert domain(c, "spoon") == {"A", "B", "C"}
    bad = check_assignment(p, {"knife": "A", "spoon": "A"})
    assert [(v.kind, v.item, v.constraint) for v in bad] == [("constraint", "knife", "h")]
    assert check_assignment(p, {"knife": "C", "spoon": "A"}) == []


def test_forbid_tag():
    p, c = setup(
        HardConstraint("h", HardKind.FORBID_TAG, SHARP, tag="kids"), A={"kids"}, B={"kids"}
    )
    assert domain(c, "knife") == {"C"}
    assert [v.constraint for v in check_assignment(p, {"knife": "B", "spoon": "A"})] == ["h"]


def test_min_level():
    p, c = setup(HardConstraint("h", HardKind.MIN_LEVEL, SHARP, level=1))
    assert domain(c, "knife") == {"A", "C"}  # B is level 0
    assert [v.constraint for v in check_assignment(p, {"knife": "B", "spoon": "A"})] == ["h"]


def test_max_level():
    p, c = setup(HardConstraint("h", HardKind.MAX_LEVEL, SHARP, level=1))
    assert domain(c, "knife") == {"A", "B"}  # C is level 3
    assert [v.constraint for v in check_assignment(p, {"knife": "C", "spoon": "A"})] == ["h"]


def test_min_distance():
    # distances to the counter (0,400): A 400, B 500, C 300
    p, c = setup(
        HardConstraint("h", HardKind.MIN_DISTANCE, SHARP, reference="counter", distance=350)
    )
    assert domain(c, "knife") == {"A", "B"}
    assert [v.constraint for v in check_assignment(p, {"knife": "C", "spoon": "A"})] == ["h"]


def test_max_distance():
    p, c = setup(
        HardConstraint("h", HardKind.MAX_DISTANCE, SHARP, reference="counter", distance=450)
    )
    assert domain(c, "knife") == {"A", "C"}
    assert [v.constraint for v in check_assignment(p, {"knife": "B", "spoon": "A"})] == ["h"]


def test_selector_matches_by_item_id_and_tag():
    by_id = Selector(items=frozenset({"spoon"}))
    by_tag = Selector(tags=frozenset({"fragile"}))
    items = [Item("knife"), Item("spoon"), Item("glass", tags=frozenset({"fragile"}))]
    _, c = setup(HardConstraint("h", HardKind.MAX_LEVEL, by_id, level=0), items)
    assert domain(c, "spoon") == {"B"} and domain(c, "knife") == {"A", "B", "C"}
    _, c = setup(HardConstraint("h", HardKind.MAX_LEVEL, by_tag, level=0), items)
    assert domain(c, "glass") == {"B"} and domain(c, "spoon") == {"A", "B", "C"}


def test_separate_categories_never_share_a_location():
    items = [Item("bleach", category="cleaning"), Item("rice", category="food"), Item("pan")]
    apart = HardConstraint(
        "apart",
        HardKind.SEPARATE,
        Selector(categories=frozenset({"cleaning"})),
        other=Selector(categories=frozenset({"food"})),
    )
    p, c = setup(apart, items)
    bad = check_assignment(p, {"bleach": "A", "rice": "A", "pan": "B"})
    assert [(v.kind, v.location, v.constraint) for v in bad] == [("separate", "A", "apart")]
    assert check_assignment(p, {"bleach": "A", "rice": "B", "pan": "A"}) == []
    i = c.item_index
    assert not c.location_ok(0, [i["bleach"], i["rice"]])
    assert c.location_ok(0, [i["bleach"], i["pan"]])


def test_pins_override_unary_constraints_and_fix_the_domain():
    p, _ = setup(HardConstraint("h", HardKind.MIN_LEVEL, SHARP, level=1))
    p = replace(p, pins={"knife": "B"})
    c = Compiled(p)
    assert domain(c, "knife") == {"B"}
    assert check_assignment(p, {"knife": "B", "spoon": "A"}) == []


def test_item_too_large_for_a_location_is_out_of_its_domain():
    locs = locations()
    locs["A"] = replace(locs["A"], capacity_volume=2.0)
    p = Problem(locs, {"pot": Item("pot", volume=5.0)}, (), PARAMS)
    assert domain(Compiled(p), "pot") == {"B", "C"}


def test_unknown_distance_reference_is_an_error():
    from reach.core.compile import ProblemError

    with pytest.raises(ProblemError):
        setup(HardConstraint("h", HardKind.MIN_DISTANCE, SHARP, reference="nope", distance=1))
