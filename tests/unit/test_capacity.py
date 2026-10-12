from dataclasses import replace

import pytest

from reach.core.capacity import used_volume, used_weight
from reach.core.compile import Compiled
from reach.core.constraints import check_assignment
from reach.core.model import Item, Problem
from tests.helpers import PARAMS, locations


def test_plain_items_add_up_times_quantity():
    items = [Item("a", volume=2.0, quantity=3), Item("b", volume=1.5)]
    assert used_volume(items, PARAMS) == pytest.approx(7.5)


def test_nested_units_take_the_largest_plus_a_fraction_of_the_rest():
    # 4 plates of 1 l: 1 + 0.25 x 3 = 1.75
    plates = [Item("plate", volume=1.0, quantity=4, nest_group="plates")]
    assert used_volume(plates, PARAMS) == pytest.approx(1.75)


def test_nesting_across_items_of_one_group():
    # pots of 6, 4 and 2 l: 6 + 0.25 x (4 + 2) = 7.5
    pots = [
        Item("big", volume=6.0, nest_group="pots"),
        Item("mid", volume=4.0, nest_group="pots"),
        Item("small", volume=2.0, nest_group="pots"),
    ]
    assert used_volume(pots, PARAMS) == pytest.approx(7.5)


def test_groups_nest_separately_and_factor_is_configurable():
    params = replace(PARAMS, nest_factors={"pots": 0.5})
    items = [
        Item("big", volume=6.0, nest_group="pots"),
        Item("small", volume=2.0, nest_group="pots"),
        Item("plate", volume=1.0, quantity=5, nest_group="plates"),
        Item("jar", volume=3.0),
    ]
    # pots 6 + 0.5 x 2 = 7; plates 1 + 0.25 x 4 = 2; jar 3
    assert used_volume(items, params) == pytest.approx(12)


def test_compiled_volume_matches_the_reference_implementation():
    items = {
        "big": Item("big", volume=6.0, nest_group="pots"),
        "small": Item("small", volume=2.0, quantity=2, nest_group="pots"),
        "jar": Item("jar", volume=3.0, quantity=2),
    }
    c = Compiled(Problem(locations(), items, (), PARAMS))
    assert c.volume([0, 1, 2]) == pytest.approx(used_volume(items.values(), PARAMS))


def test_weight_counts_every_unit():
    assert used_weight([Item("a", weight=0.5, quantity=4), Item("b", weight=1.0)]) == 3.0


def _problem(items, **loc_overrides):
    locs = locations()
    for lid, changes in loc_overrides.items():
        locs[lid] = replace(locs[lid], **changes)
    return Problem(locs, {i.id: i for i in items}, (), PARAMS)


def test_volume_capacity_violation_is_reported():
    p = _problem([Item("a", volume=6.0), Item("b", volume=5.0)])
    assert check_assignment(p, {"a": "A", "b": "B"}) == []
    kinds = [v.kind for v in check_assignment(p, {"a": "A", "b": "A"})]
    assert kinds == ["volume"]


def test_nesting_makes_a_stack_fit():
    plates = Item("plate", volume=3.0, quantity=8, nest_group="plates")
    # 3 + 0.25 x 21 = 8.25 <= 10, although 8 x 3 = 24 would not fit
    assert check_assignment(_problem([plates]), {"plate": "A"}) == []


def test_weight_capacity_violation_is_reported():
    p = _problem([Item("a", weight=4.0, quantity=2)], A={"capacity_weight": 5.0})
    assert [v.kind for v in check_assignment(p, {"a": "A"})] == ["weight"]
    assert check_assignment(p, {"a": "B"}) == []


def test_non_assignable_locations_only_take_pinned_items():
    p = _problem([Item("a")])
    assert [v.kind for v in check_assignment(p, {"a": "counter"})] == ["not_assignable"]
    pinned = replace(p, pins={"a": "counter"})
    assert check_assignment(pinned, {"a": "counter"}) == []
    assert [v.kind for v in check_assignment(pinned, {"a": "A"})] == ["pin"]


def test_missing_and_unknown_assignments_are_reported():
    p = _problem([Item("a"), Item("b")])
    kinds = sorted(v.kind for v in check_assignment(p, {"a": "nowhere"}))
    assert kinds == ["unassigned", "unknown_location"]
