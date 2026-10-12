"""Step-by-step simulation of one routine under an assignment.

See ``docs/ARCHITECTURE.md`` section 3.1 for the rules. The function is written for speed: it is
the inner loop of the optimizer.
"""

from __future__ import annotations

from reach.core.compile import (
    OP_GO_TO,
    OP_PUT_DOWN,
    OP_SET_STATE,
    OP_STORE,
    OP_TAKE,
    Compiled,
    CompiledRoutine,
)

Metrics = tuple[float, float, float, float, float, float]
"""``(distance, openings, time, effort, extra_trips, clearings)``, in ``METRICS`` order."""

TraceEvent = tuple[int, str, int, float, int]
"""``(step index, kind, point index, time in seconds, reference index)``.

``kind`` is ``start``, ``move``, ``open``, ``handle``, ``clear``, ``restore`` or ``extra_trip``.
The reference is a location index for ``open`` / ``handle`` and an obstruction index for
``clear`` / ``restore``; ``-1`` otherwise.
"""


def simulate(
    c: Compiled,
    routine: CompiledRoutine,
    assign: list[int],
    trace: list[TraceEvent] | None = None,
) -> Metrics:
    """Simulate a single actor performing ``routine``.

    The actor is implicit here. Supporting several actors means running one of these state
    machines per actor on a shared clock; the trace already carries the timestamps needed to
    detect paths crossing.
    """
    dist = c.dist
    speed = c.speed
    handling = c.handling
    max_carry = c.max_carry
    blockers = c.blockers
    has_obs = c.n_obs > 0

    pos = routine.start
    distance = 0.0
    openings = 0.0
    time = 0.0  # everything except walking; walking is added from the distance at the end
    effort = 0.0
    extra_trips = 0
    clearings = 0

    open_loc = -1
    blocking = list(routine.initial_blocking) if has_obs else None
    cleared: list[int] = []
    placed: dict[int, int] = {}
    carried = 0
    in_hand: set[int] = set()
    run_origin = -1
    run_count = 0

    if trace is not None:
        trace.append((-1, "start", pos, 0.0, -1))

    for n, (op, item, loc, next_drop) in enumerate(routine.steps):
        if op == OP_SET_STATE:
            blocking[item] = bool(loc)  # type: ignore[index]
            if item in cleared:
                cleared.remove(item)
            continue

        if op == OP_TAKE:
            target = placed.pop(item, -1) if placed else -1
            if target < 0:
                target = assign[item]
        elif op == OP_STORE:
            target = assign[item]
        else:
            target = loc

        # Extra trips for implicit pick-ups: a run of stores of items that were never taken.
        if max_carry:
            if op == OP_STORE and item not in in_hand:
                if run_count == 0:
                    run_origin = pos
                elif run_count >= max_carry:
                    extra_trips += 1
                    distance += dist[pos][run_origin]
                    pos = run_origin
                    run_count = 0
                    if trace is not None:
                        trace.append((n, "extra_trip", pos, time + distance / speed, -1))
                run_count += 1
            else:
                run_count = 0

        if has_obs:
            target_blockers = blockers[target]
            if cleared:
                for o in cleared[:]:
                    if o not in target_blockers:
                        p = c.obs_pos[o]
                        if p >= 0:
                            distance += dist[pos][p]
                            pos = p
                        time += c.obs_restore_time[o]
                        openings += c.obs_restore_open[o]
                        cleared.remove(o)
                        open_loc = -1
                        if trace is not None:
                            trace.append((n, "restore", pos, time + distance / speed, o))
            for o in target_blockers:
                if blocking[o] and o not in cleared:  # type: ignore[index]
                    p = c.obs_pos[o]
                    if p >= 0:
                        distance += dist[pos][p]
                        pos = p
                    time += c.obs_clear_time[o]
                    openings += c.obs_clear_open[o]
                    clearings += 1
                    cleared.append(o)
                    if trace is not None:
                        trace.append((n, "clear", pos, time + distance / speed, o))

        if pos != target:
            distance += dist[pos][target]
            pos = target
        if trace is not None:
            trace.append((n, "move", pos, time + distance / speed, -1))

        if op == OP_TAKE or op == OP_STORE:  # noqa: SIM109 (hot loop)
            if open_loc != target:
                open_loc = target
                if c.open_cost[target] or c.open_time[target]:
                    openings += c.open_cost[target]
                    time += c.open_time[target]
                    if trace is not None:
                        trace.append((n, "open", pos, time + distance / speed, target))
            time += handling
            effort += c.level_penalty[target] * c.weight_mult[item]
            if max_carry:
                if op == OP_TAKE:
                    if carried >= max_carry:
                        extra_trips += 1
                        drop = next_drop if next_drop >= 0 else routine.start
                        distance += 2.0 * dist[target][drop]
                        carried = 0
                        in_hand.clear()
                    carried += 1
                    in_hand.add(item)
                elif item in in_hand:
                    in_hand.discard(item)
                    carried -= 1
        else:
            open_loc = -1
            if op != OP_GO_TO:
                time += handling
                if op == OP_PUT_DOWN:
                    placed[item] = target
                    if item in in_hand:
                        in_hand.discard(item)
                        carried -= 1
        if trace is not None and op != OP_GO_TO:
            trace.append((n, "handle", pos, time + distance / speed, target))

    for o in cleared:
        p = c.obs_pos[o]
        if p >= 0:
            distance += dist[pos][p]
            pos = p
        time += c.obs_restore_time[o]
        openings += c.obs_restore_open[o]
        if trace is not None:
            trace.append((len(routine.steps), "restore", pos, time + distance / speed, o))

    return (
        distance,
        openings,
        time + distance / speed,
        effort,
        float(extra_trips),
        float(clearings),
    )
