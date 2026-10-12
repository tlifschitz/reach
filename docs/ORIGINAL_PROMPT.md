# Project: Reach — optimize where things live, starting with your kitchen

## Context
I'm remodeling my kitchen and want to decide where to store each item so that everyday activities
take as little effort as possible. The physical layout (drawers, cabinets, pantry, stove, main
sink, secondary/utility sink, fridge, outlets, countertop zones) is fixed. What gets optimized is
the assignment of items to compartments.

The tool will be public, open source, and part of my portfolio: the code, README, tests, and user
experience must meet that standard.

## Before writing code
1. Read this entire document.
2. Write `docs/ARCHITECTURE.md` covering: the data model, the exact cost function (with formulas),
   the optimization algorithm, and the folder structure.
3. Then implement everything in one go. If you hit an ambiguity, make the most reasonable decision,
   document it in `docs/DECISIONS.md`, and keep going.
4. Keep the core engine domain-agnostic: the simulation and optimization modules use generic
   terms (locations, items, routines, cost model) and contain nothing kitchen-specific. The kitchen
   is the flagship use case and lives in the sample data, templates, and UI copy, so the same
   engine could later be applied to other spaces (workshops, labs, small warehouses).

## Stack
- Backend: Python 3.12, FastAPI, and Pydantic v2 for validation. Managed with `uv`.
- Frontend: simple. React + Vite + TypeScript, with the floor plan drawn in SVG.
- Runs locally with a single command (`make dev` or similar). Add an optional Dockerfile.
- Tests with pytest. Linting with ruff. GitHub Actions CI running tests and lint. MIT license.

## Input data
Use YAML for the space and the routines, and CSV for the items. Everything is validated with
Pydantic and produces clear errors (file, line, field) when something is wrong.

### `kitchen.yaml`: the space
- 2D floor plan with coordinates in cm.
- **Points of interest** (POIs): stove, sinks, fridge, freezer, countertop zones (each with its
  size), outlets. Each has an id, a type, and a position (x, y).
- **Assignable compartments**: drawers, cabinet doors, and pantry shelves, around 40. Each has:
  - `id`, `type` (drawer | door | pantry_shelf)
  - `access_point` (x, y): where the person stands to use it
  - `level`: 0 = low/bending down, 1 = comfortable height, 2 = high/reaching up,
    3 = requires a step stool
  - `volume_l` and `max_fill_factor` (default 0.8)
  - `max_weight_kg` (optional)
  - free-form `tags` (e.g. `near_heat`, `child_reachable`, `near_secondary_sink`)
- **Fixed, non-assignable locations**: the fridge and the freezer. Items that live there are
  declared with a fixed location; the optimizer never moves them and never assigns other items
  there. Items that stay on the countertop (electric kettle, toaster, etc.) are modeled the same
  way, fixed to a countertop zone.
- **Cost parameters**, all configurable: walking speed, time and cost per opening by type and
  level (a level-3 door costs more because of the step stool), ergonomic penalty per level, weight
  factor, and the weights of the metric combination.

### `items.csv`: around 300 items
Columns: `id, name, category, volume_l, quantity, weight_kg, estimated_frequency_per_week,
nest_group, fixed_location, tags, comments`.
- `nest_group`: items in the same group (pots, plates, food containers) stack or nest. When stored
  together, the group's volume is that of the largest item plus a configurable factor for each
  additional item. Propose a simple model and document it.
- `fixed_location`: optional. Pins the item to a compartment, POI, or zone (fridge, countertop, or
  a spot I want to lock manually).
- `comments`: free text (see "LLM interpretation" below).

### `constraints.yaml`: constraints
- **Hard** (never violated): item or category allowed only in compartments with a given tag, or
  forbidden in compartments with a given tag. Minimum or maximum distance to a POI (e.g. cleaning
  products away from food and near the secondary sink, or spices away from the stove). Minimum or
  maximum level (e.g. knives out of children's reach, or heavy items on low levels). Separation
  between categories (cleaning products never in the same compartment as food).
- **Soft** (configurable penalty): groups of items that should be together, in the same
  compartment or in nearby compartments. For example, all the espresso accessories.

### `routines.yaml`: routines
Each routine has an `id`, a `name`, a `frequency_per_week` (its weight in the total), and an
ordered sequence of steps:
- `go_to: <poi_or_zone>`
- `take: <item_id>`: the person walks to the access point of the compartment holding the item
- `use: <item_id> at <poi_or_zone>`
- `put_down: <item_id> at <zone>`
- `store: <item_id>`: the person carries it to its assigned compartment

Putting things away after washing them is a separate routine (e.g. "unload the dish rack"). The
person starts at a position defined by the routine. In v1 only one person cooks.

## Simulation and metrics
For a given assignment, simulate each routine step by step and compute:
1. **Distance** walked (cm, Euclidean between points; document this simplification).
2. **Openings**: one per visit to a compartment. Several consecutive `take` or `store` steps in
   the same compartment count as one. The cost varies by type and level.
3. **Estimated time**: distance divided by speed, plus the time per opening by type and level,
   plus the time per item handled.
4. **Ergonomic effort**: the level penalty times the weight factor of each item taken or stored.
5. **Extra trips** (optional, configurable): if more than N items are carried at once, an extra
   trip is added.

The total cost is the weighted sum of the normalized metrics, multiplied by each routine's weekly
frequency, plus the soft-constraint penalties. The app always shows each metric separately, per
routine and as a weekly total.

Design the simulation engine so it can later support multiple people and measure their paths
crossing, but don't implement that in v1.

## Optimization
- The cost isn't separable per item (the path is sequential and one opening serves several items),
  so use a local search such as simulated annealing. Moves: move one item, swap two items, and
  move a whole `nest_group`.
- Hard constraints and capacity are always respected. First generate a feasible initial solution,
  for example with a greedy pass by usage frequency.
- Use an abstract `Optimizer` interface so other methods can be added later.
- Compute the cost incrementally: after a move, only re-simulate the routines that use the
  affected items. With 300 items, 40 compartments, and around 30 routines, a full optimization
  may take up to 15-20 minutes on a personal computer. Show progress and the best solution found
  so far, and allow stopping early and keeping it.
- Use a configurable fixed seed so results are reproducible.
- If no feasible solution exists, report which constraints or capacities prevent it.

## Sensitivity analysis
Frequencies are estimates, so the app must be able to:
- Randomly perturb routine frequencies (e.g. ±X%, configurable), re-optimize N times, and report
  the stability of each assignment (in what % of runs each item ended up in the same compartment)
  and the distribution of the cost.
- Show which items have a "robust" location and which depend on the estimates.

## LLM interpretation (optional)
A separate preprocessing step that reads the items' `comments` field and, using the Anthropic API,
proposes structured constraints in the same format as `constraints.yaml`. The proposals are
written to a separate file for me to review. They are never applied without approval. The app
works fully without an API key.

## Assisted routine generation
Writing routines by hand is tedious, and the quality of the results depends on them being
realistic and complete. Implement these mechanisms. The ones that use an LLM are optional: the app
works without an API key, and everything generated goes through review before being used.

1. **From natural language**: I describe an activity in free text or paste a recipe (text or URL).
   The LLM receives the item catalog, the POIs, and the zones, and generates the routine in the
   `routines.yaml` format, using only existing ids. If I mention something that isn't in the
   catalog, it flags it as a "suggested missing item" instead of making it up. The result goes
   through the validator and is shown in an editor where I can approve, edit, or regenerate it.
2. **Import a usage diary**: I upload a plain-text file written in a phone note, with a header per
   day (in DD/MM format, e.g. "12/10") and, below it, the kitchen activities I did that day in
   free text. The app doesn't need a logging interface, only the import. A batch process
   interprets the file with the LLM, groups entries that describe the same activity, turns them
   into routines (reusing existing routines when they match), and computes real weekly
   frequencies from the recorded days. Ambiguous entries or entries without a clear date are
   reported separately. Everything is a proposal to review before use. Include a sample two-week
   diary file.
3. **Coverage analysis**: a report showing items that don't appear in any routine, routines
   with too few steps or that look suspicious (e.g. an item that is taken and never used), and a
   comparison between each item's `estimated_frequency_per_week` in `items.csv` and the frequency
   derived from the routines. With one button, the LLM proposes routines to cover the unused
   items.

Prefer atomic routines (one activity, such as "espresso" or "toast") with their own frequency
over composite routines such as "breakfast". The LLM follows this rule when generating or merging
routines.

For the cost, each item's usage frequency is derived from the routines. The
`estimated_frequency_per_week` column in `items.csv` is used only as an initial estimate and a
consistency check.

## Web interface
- Upload files (or use the bundled example) and see validation errors.
- 2D SVG floor plan with POIs, zones, and compartments (colored by level).
- Button to run the optimization, with visible progress.
- Result: a list of what goes in each compartment, with the % of capacity used.
- Table of metrics per routine and weekly totals.
- Comparison against an optional current assignment (`current_assignment.csv`).
- Pin or unpin items from the interface and re-optimize.
- Sensitivity results view.
- Export the assignment to CSV.
- Nice to have: animation of a routine's path on the floor plan.

There must also be an equivalent CLI to validate, simulate, optimize, and run sensitivity analysis
without the frontend.

## Sample data
I don't have my real data yet. Include:
- A realistic, complete sample kitchen: around 40 compartments, around 150 items, and around 12
  varied routines (washing dishes, breakfast, espresso, evening tea, a specific recipe, unloading
  the dish rack, cleaning, etc.), with hard and soft constraints of every type.
- Commented empty templates for each file.
- `docs/DATA_COLLECTION.md`: a practical guide for surveying my real kitchen. How to choose the
  coordinate origin, what to measure for each compartment, how to estimate volumes and
  frequencies, and how to write routines.

## Tests
- Simulation unit tests with small hand-computed cases (distance, grouped openings, per-level
  costs, nesting).
- Validation of capacity and of each type of hard constraint.
- That the optimizer never returns infeasible solutions and improves on the greedy solution.
- An end-to-end test with the sample data.

## Deliverables
Working code, README (with description, screenshot or GIF, installation, usage, and data format),
`docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/DATA_COLLECTION.md`, sample data, templates,
passing tests, and configured CI. A `README.md` already exists with the project's pitch and a build-in-public roadmap. Update it
(installation, usage, data format, screenshots) instead of replacing it, and tick off roadmap
items as they're completed.
---

# Addendum

Two requirements were added after the first version of this spec, before implementation started.

## Obstructions

Obstacles that, in some state, block access to a set of compartments or zones.
For example, the kitchen entrance door, when open, covers an entire column of upper cabinets,
countertop, and lower cabinets, and must be closed to use them. Each obstruction has an `id`, a
default state (`open` | `closed`), the list of compartments and zones it blocks, and a
configurable cost (time and openings) to clear it and restore it. Consecutive accesses to
blocked compartments clear the obstruction only once, like grouped openings. Model this
generically so it also covers cases like an open dishwasher door blocking adjacent drawers.
Obstruction clearings are reported as their own metric, in addition to being included in time
and openings.

Include an obstruction in the sample kitchen and a hand-computed unit test for it.

## Heights and visualization

The floor plan is 2D (top view) and is used for walking distances, but compartments stacked in
the same column overlap there. Add elevation views:

- Define walls in `kitchen.yaml` (id, start and end points on the floor plan).
- Each compartment references a wall and has its real geometry on it: horizontal position along
  the wall, width, bottom height, and top height (cm). Its `access_point` stays in the floor plan.
- Derive `level` from the compartment's height using configurable height ranges, with an
  optional manual override (e.g. to mark that a compartment requires a step stool).
- UI: the top view shows POIs, zones, walls, and routine paths. Clicking a wall opens its
  elevation view, with compartments drawn at real size and position, colored by level, showing
  their assigned items and capacity used.
- Obstructions can reference a wall range instead of listing compartments one by one.
