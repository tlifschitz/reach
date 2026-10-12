"""Hand-computed simulation cases. The numbers in the comments are the derivation."""

from dataclasses import replace

import pytest

from reach.core.model import Obstruction, Problem, TransitionCost
from tests.helpers import PARAMS, items, locations, routine, run


def problem(steps: str, item_specs=("x", "y", "z"), **kw) -> Problem:
    params = kw.pop("params", PARAMS)
    obstructions = kw.pop("obstructions", ())
    return Problem(locations(), items(*item_specs), (routine(steps, **kw),), params, obstructions)


def test_distance_time_and_single_opening():
    p = problem("take x; put_down x at counter")
    distance, openings, time, effort, extra, clearings = run(p, {"x": "A", "y": "A", "z": "A"})
    # counter (0,400) -> A (0,0) -> counter: 400 + 400
    assert distance == pytest.approx(800)
    assert openings == 1
    # walking 800/100 = 8, open drawer 2, handle take 1, handle put_down 1
    assert time == pytest.approx(12)
    assert effort == 0  # level 1 has penalty 0
    assert extra == 0 and clearings == 0


def test_diagonal_distance_is_euclidean():
    p = problem("take x; put_down x at counter")
    distance, *_ = run(p, {"x": "B", "y": "A", "z": "A"})
    # counter (0,400) -> B (300,0) is a 3-4-5 triangle: 500 each way
    assert distance == pytest.approx(1000)


def test_consecutive_takes_in_one_location_share_an_opening():
    p = problem("take x; take y; put_down x at counter")
    _, openings, time, *_ = run(p, {"x": "A", "y": "A", "z": "A"})
    assert openings == 1
    # walk 8, one opening 2, three handlings 3
    assert time == pytest.approx(13)


def test_takes_in_different_locations_open_twice():
    p = problem("take x; take y; put_down x at counter")
    distance, openings, *_ = run(p, {"x": "A", "y": "B", "z": "A"})
    assert openings == 2
    assert distance == pytest.approx(400 + 300 + 500)


def test_another_step_in_between_closes_the_location():
    p = problem("take x; go_to counter; take y; put_down x at counter")
    _, openings, *_ = run(p, {"x": "A", "y": "A", "z": "A"})
    assert openings == 2


def test_store_and_take_group_too():
    p = problem("store x; take y; store z")
    _, openings, *_ = run(p, {"x": "A", "y": "A", "z": "B"})
    assert openings == 2


def test_opening_cost_depends_on_type_and_level():
    p = problem("take x; put_down x at counter")
    distance, openings, time, effort, *_ = run(p, {"x": "C", "y": "A", "z": "A"})
    # counter (0,400) -> C (300,400) and back
    assert distance == pytest.approx(600)
    assert openings == 2  # level-3 door profile: cost 2
    # walk 6, level-3 door 10, two handlings
    assert time == pytest.approx(18)
    assert effort == pytest.approx(4)  # penalty 4 x (1 + 0.5 x 0 kg)


def test_effort_scales_with_level_and_weight():
    p = problem("take x; store x", item_specs=(("x", 1.0, 2.0),))
    *_, effort, _, _ = run(p, {"x": "B"})
    # level 0 penalty 1, weight multiplier 1 + 0.5 x 2 = 2, handled twice
    assert effort == pytest.approx(4)


def test_take_after_put_down_picks_up_where_it_was_left():
    p = problem("take x; put_down x at counter; take x; store x")
    distance, openings, *_ = run(p, {"x": "A", "y": "A", "z": "A"})
    # 400 to A, 400 back, 0 to pick it up from the counter, 400 to store
    assert distance == pytest.approx(1200)
    assert openings == 2  # A twice; the counter has no opening


def test_fixed_point_location_has_no_opening():
    p = problem("take x; use x at counter")
    _, openings, *_ = run(p, {"x": "counter", "y": "A", "z": "A"})
    assert openings == 0


DOOR = Obstruction(
    "door",
    default_state="open",
    blocking_state="open",
    blocks=frozenset({"A", "B"}),
    clear=TransitionCost(3.0, 1.0),
    restore=TransitionCost(2.0, 1.0),
)


def test_obstruction_cleared_once_for_consecutive_blocked_locations():
    p = problem("take x; take y; put_down x at counter", obstructions=(DOOR,))
    distance, openings, time, _, _, clearings = run(p, {"x": "A", "y": "B", "z": "C"})
    assert distance == pytest.approx(400 + 300 + 500)
    assert clearings == 1
    # A and B are two openings; clearing and restoring the door add 1 each
    assert openings == 4
    # walk 12, clear 3, (open 2 + handle 1) twice, restore 2, handle put_down 1
    assert time == pytest.approx(12 + 3 + 3 + 3 + 2 + 1)


def test_obstruction_cleared_again_after_leaving_the_blocked_set():
    p = problem("take x; go_to counter; take y", obstructions=(DOOR,))
    _, openings, _, _, _, clearings = run(p, {"x": "A", "y": "B", "z": "C"})
    assert clearings == 2
    assert openings == 2 + 2 * 2  # two locations, two clear/restore pairs


def test_obstruction_is_restored_at_the_end_of_the_routine():
    p = problem("take x", obstructions=(DOOR,))
    _, openings, time, *_ = run(p, {"x": "A", "y": "C", "z": "C"})
    assert openings == 3  # A, clear, restore
    assert time == pytest.approx(4 + 3 + 2 + 1 + 2)


def test_unblocked_locations_do_not_touch_the_obstruction():
    p = problem("take x; put_down x at counter", obstructions=(DOOR,))
    _, openings, _, _, _, clearings = run(p, {"x": "C", "y": "A", "z": "A"})
    assert clearings == 0
    assert openings == 2


def test_set_state_removes_the_block_for_free():
    p = problem(
        "set_state door closed; take x; take y; put_down x at counter", obstructions=(DOOR,)
    )
    _, openings, time, _, _, clearings = run(p, {"x": "A", "y": "B", "z": "C"})
    assert clearings == 0
    assert openings == 2
    assert time == pytest.approx(12 + 3 + 3 + 1)


def test_initial_state_and_set_state_make_a_default_closed_obstruction_block():
    dishwasher = replace(DOOR, default_state="closed")
    p = problem("take x", obstructions=(dishwasher,))
    assert run(p, {"x": "A", "y": "C", "z": "C"})[5] == 0
    p = problem("set_state door open; take x", obstructions=(dishwasher,))
    assert run(p, {"x": "A", "y": "C", "z": "C"})[5] == 1
    p = problem("take x", obstructions=(dishwasher,), initial_states={"door": "open"})
    assert run(p, {"x": "A", "y": "C", "z": "C"})[5] == 1


def test_obstruction_with_a_position_adds_the_walk_to_it():
    from reach.core.model import Point

    door = replace(DOOR, position=Point(0, 200))
    p = problem("take x", obstructions=(door,))
    distance, *_ = run(p, {"x": "A", "y": "C", "z": "C"})
    # counter -> door handle 200, -> A 200, back to the handle to restore 200
    assert distance == pytest.approx(600)


def test_extra_trip_when_carrying_more_than_allowed():
    params = replace(PARAMS, max_carry=2)
    p = problem("take x; take y; take z; put_down x at counter", params=params)
    distance, _, _, _, extra, _ = run(p, {"x": "A", "y": "A", "z": "A"})
    assert extra == 1
    # 400 to A, a round trip A <-> counter for the overflow (800), 400 to the counter
    assert distance == pytest.approx(1600)


def test_no_extra_trip_within_the_carry_limit():
    params = replace(PARAMS, max_carry=3)
    p = problem("take x; take y; take z; put_down x at counter", params=params)
    assert run(p, {"x": "A", "y": "A", "z": "A"})[4] == 0


def test_extra_trip_for_a_long_run_of_stores():
    params = replace(PARAMS, max_carry=2)
    p = problem("store x; store y; store z", params=params)
    distance, _, _, _, extra, _ = run(p, {"x": "A", "y": "A", "z": "A"})
    assert extra == 1
    # counter -> A 400, back to the counter for the third item 400, -> A 400
    assert distance == pytest.approx(1200)


def test_trace_is_timestamped_and_ends_at_the_total_time():
    p = problem("take x; put_down x at counter")
    trace: list = []
    metrics = run(p, {"x": "A", "y": "A", "z": "A"}, trace=trace)
    kinds = [e[1] for e in trace]
    assert kinds[0] == "start" and "open" in kinds and kinds.count("move") == 2
    times = [e[3] for e in trace]
    assert times == sorted(times)
    assert times[-1] == pytest.approx(metrics[2])
