# Decisions

The spec asks for ambiguities to be resolved with the most reasonable choice and logged here.
Each entry says what was decided and why.

## Model

1. **Volumes and weights are per unit.** `volume_l` and `weight_kg` in `items.csv` describe one
   unit; `quantity` multiplies them. A row "dinner plate, quantity 8" is easier to measure than
   the volume of the stack, and nesting then handles the stack.
2. **Nesting model.** Units of one `nest_group` in the same compartment occupy the largest unit
   plus a factor (default 0.25) of every other unit. One number per group is enough to express
   "plates stack tightly, pots less so", and it is easy to calibrate by measuring one stack.
3. **Fridge, freezer and countertop zones are ordinary non-assignable locations.** Items are
   pinned to them with `fixed_location`. The fridge and freezer have an opening profile, zones
   do not. This keeps one code path for "where is this item".
4. **A pin beats the unary hard constraints.** If you pin the knives to a low drawer although a
   rule says "knives at level 2 or higher", the pin is respected and validation prints a
   warning. A pin is a deliberate manual decision.
5. **`volume_l` is not derived from the elevation rectangle.** Depth and usable space vary too
   much (hinges, pipes, shelves), so the usable volume stays an explicit number.
6. **Level is derived from the centre height** of the compartment. The centre is a better proxy
   for where the hands go than either edge. An explicit `level` wins.
7. **POIs and zones have level 1.** They are things you use at working height.

## Simulation

8. **Euclidean walking.** Distances are straight lines between access points and ignore
   furniture. In a kitchen the walkable area is close to convex, so the error is small, and it
   keeps the distance matrix trivial. An island would need a path planner; the simulator only
   consumes a distance matrix, so that can be added without touching it.
9. **"Openings" is a weighted count.** Each opening contributes its configurable `cost`
   (1 by default, more for a level-3 door). The spec asks for a cost that varies by type and
   level, and a single weighted number keeps the metric table readable.
10. **Only consecutive `take`/`store` steps share an opening.** Any other step closes the
    compartment. People do close a drawer before walking away.
11. **A `take` after a `put_down` picks the item up where it was left**, without an opening.
    Otherwise "put the cup under the machine, then take it to the table" would send the person
    back to the cupboard.
12. **`store` does not require a previous `take`.** "Unload the dish rack" is a `go_to` followed
    by `store` steps; the items are implicitly picked up where the person stands.
13. **Extra trips are off by default** (`max_carry: null`). When enabled, an overflow adds a round
    trip to the next fixed step. It is a coarse model, which is why it is optional.
14. **Handled weight is one unit.** Effort uses the unit weight of the item, not the whole stack.

## Obstructions

15. **Two fields, `default_state` and `blocks_when`**, plus `initial_states` on a routine and a
    `set_state` step. This covers a door that blocks while open and is usually open (entrance
    door) and one that blocks while open and is usually closed (dishwasher) with one model.
16. **Restore is always charged**, as soon as the person targets a location the obstruction does
    not block, and at the end of the routine. The alternative ("leave the door closed until
    someone needs it open") would make the cost depend on what happens between routines.
17. **`set_state` is free.** Opening the dishwasher takes the same time wherever the items live,
    so it cannot change the optimum.
18. **The `clearings` weight defaults to 0.** Clearings already count through time and openings.
    The weight exists for people who find closing a door more annoying than its seconds suggest.
19. **A wall range is resolved by horizontal overlap only.** A door covers a full-height column.
    Partial-height obstructions can list their locations explicitly with `blocks`.

## Cost and optimization

20. **Normalizers come from the greedy solution** and are frozen for the run. The metrics have
    different units; dividing by a plausible weekly total makes the weights comparable and makes
    the total readable as a fraction of the greedy cost.
21. **Soft penalties are in units of total cost.** See `ARCHITECTURE.md`, section 3.3.
22. **Iteration budgets are reproducible, wall-clock budgets are not.** The default is an
    iteration budget.
23. **Hard constraints are never relaxed inside the search.** Moves are drawn from precomputed
    domains and rejected if capacity or separation would break, so every intermediate state is
    feasible and stopping early always yields a usable result.
24. **Sensitivity runs include optimizer noise.** Each run uses a different seed as well as
    different frequencies, so "stability" measures what a user would experience when re-running
    with slightly different estimates. Items used by no routine are labelled `unused`.

25. **Two extra moves: routine cluster and location exchange.** The spec lists move, swap and
    nest-group move. On the sample kitchen those alone left the search about 4% short and
    noticeably seed-dependent, because items that share an opening need to move together.
26. **A cool start.** The initial temperature accepts an average uphill move 10% of the time.
    Higher values threw away the greedy solution and did not recover it within small budgets.
27. **Greedy falls back to a packing order** (most constrained, largest first) when the
    frequency order cannot place everything. Usage order gives the best start; packing order
    gives the best chance of a feasible one.
28. **Default budget of 200,000 iterations.** That is a few seconds on the sample (about 200
    items, 44 compartments, 18 routines) and the cost has stopped improving by then. The spec
    allowed 15 to 20 minutes; `--iterations` or `--time-limit` raise the budget for bigger
    problems.

## Application

29. **One in-memory project per server process.** Reach is a local single-user tool. There is no
    database and no authentication.
30. **Jobs run in a child process.** Simulated annealing is CPU-bound Python; a separate process
    keeps the API responsive and makes stopping reliable.
31. **Uploaded and edited files are never written back to disk by the server.** The UI offers
    downloads instead, so the bundled sample and your own files cannot be overwritten by mistake.
32. **LLM output is validated with the same schemas as hand-written files** and is only ever a
    proposal. The default model is set with `REACH_LLM_MODEL`.
