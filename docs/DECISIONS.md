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
24. **Sensitivity is measured against optimizer noise.** See the Sensitivity section below.

25. **Two extra moves: routine cluster and location exchange.** The spec lists move, swap and
    nest-group move. On the sample kitchen those alone left the search about 4% short and
    noticeably seed-dependent, because items that share an opening need to move together.
26. **A cool start.** The initial temperature accepts an average uphill move 10% of the time.
    Higher values threw away the greedy solution and did not recover it within small budgets.
27. **Greedy falls back to a packing order** (most constrained, largest first) when the
    frequency order cannot place everything. Usage order gives the best start; packing order
    gives the best chance of a feasible one.
28. **Library default of 200,000 iterations.** That is a few seconds on the sample (about 200
    items, 44 compartments, 18 routines). The CLI and the app use a larger default; see 38.

## Occasional items

29. **Items that no routine mentions get a generic access cost.** Without it their location is
    free, so the optimizer scatters them arbitrarily and they compete for nothing. Each one is
    charged a round trip from a central point to its compartment (walk, opening, effort, any
    door that is normally in the way), times its `estimated_frequency_per_week`. This is the
    one place where the catalog estimate enters the cost; for items that do appear in routines
    the routines still decide.
30. **One access is one take.** Walk there, open, take, walk back. Putting the item away again
    would be a second access and is assumed to be part of the estimate.
31. **The origin defaults to the centroid of all locations** and is configurable as a point or a
    POI/zone id (`cost.occasional_items.origin`). It stands for "wherever I happen to be".
32. **It is reported as its own line and kept out of the normalizers.** The normalizers stay
    defined by the routines, so "1.0" still means "the routines under the greedy layout", and
    the occasional share of the total is visible (about 22% on the sample).
    `cost.occasional_items.enabled: false` removes the term.

## Sensitivity

33. **Noise runs and perturbed runs.** The first version only perturbed frequencies, and most
    items looked unstable. Re-optimizing with the *same* frequencies and a new seed shows how
    much of that is the optimizer. Measured on the sample kitchen (20 runs of each kind,
    ±30%, items that affect the cost; "wall+level" counts a move to another shelf on the same
    wall and level as staying):

    | Iterations × restarts | Noise: exact | Noise: wall+level | Perturbed: exact | Perturbed: wall+level | Noise cost min / median / max |
    | --- | --- | --- | --- | --- | --- |
    | 50,000 × 1 | 23% | 63% | 24% | 64% | 1.2218 / 1.2386 / 1.2618 |
    | 200,000 × 1 | 38% | 69% | 36% | 69% | 1.2112 / 1.2226 / 1.2351 |
    | 1,000,000 × 1 | 42% | 74% | 43% | 75% | 1.2060 / 1.2148 / 1.2229 |
    | 200,000 × 5 | 48% | 71% | 46% | 72% | 1.2112 / 1.2141 / 1.2201 |
    | 200,000 × 10 | 50% | 72% | 46% | 74% | 1.2072 / 1.2130 / 1.2185 |
    | 1,000,000 × 5 | 57% | 77% | 54% | 76% | 1.2060 / 1.2099 / 1.2132 |

    At every budget the perturbed runs are no less stable than the noise runs. On this sample,
    essentially all of the instability is optimizer noise, and a ±30% error in the frequencies
    adds nothing that can be measured on top of it.
34. **More budget helps, and does not finish the job.** Going from 50,000 × 1 to 1,000,000 × 5
    raises exact stability from 23% to 57%, narrows the cost spread from 0.040 to 0.007, and
    lowers the median cost by 2.3%. But at the top budget the runs differ in cost by 0.6% while
    43% of the items still change compartment: the problem has many layouts that are equally
    good. That residue is a property of the kitchen (plenty of comfortable space close to
    everything), not something more search removes.
35. **Stability by area and level.** Locations carry a generic `area` (the wall, for a kitchen).
    Labels use "same area and level", because the exact shelf among interchangeable neighbours
    is not a meaningful difference. The exact-location share is still reported.
36. **Labels: robust, noisy, sensitive.** `noisy` items move with unchanged frequencies;
    `sensitive` ones are stable under noise and move only under perturbation. Only the second
    group says something about the estimates.
37. **Restarts instead of one long run.** `restarts = k` keeps the best of `k` independent
    searches. For the same total work, 200,000 × 5 and 1,000,000 × 1 are about equal in median
    cost; restarts give the narrower spread, a single long run the better best case. They are
    combined.
38. **Defaults of the CLI and the app: 1,000,000 iterations × 5 restarts**, about a minute for
    one optimization of the sample and about ten for a 20 + 20 run sensitivity analysis on an
    8-core laptop. That is the budget the spec allowed for (15 to 20 minutes), spent where it
    buys stability. The library defaults stay at 200,000 × 1 so tests and scripts are fast. A
    `--time-limit` is shared between the restarts.

## Application

39. **One in-memory project per server process.** Reach is a local single-user tool. There is no
    database and no authentication.
40. **Jobs run in a child process.** Simulated annealing is CPU-bound Python; a separate process
    keeps the API responsive and makes stopping reliable.
41. **Uploaded and edited files are never written back to disk by the server.** The UI offers
    downloads instead, so the bundled sample and your own files cannot be overwritten by mistake.
42. **LLM output is validated with the same schemas as hand-written files** and is only ever a
    proposal. The default model is set with `REACH_LLM_MODEL`.
