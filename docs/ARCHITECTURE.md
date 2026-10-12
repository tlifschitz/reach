# Architecture

Reach assigns items to storage locations so that a weighted set of routines costs as little as
possible. This document describes the data model, the exact cost function, the optimization
algorithm and the code layout. Choices that were not dictated by the spec are logged in
[`DECISIONS.md`](DECISIONS.md).

## 1. Layers

```
files (YAML / CSV)          kitchen vocabulary: pois, zones, walls, compartments, drawers ...
        │  reach.io         Pydantic schemas, line-tracking loaders, cross-file checks
        ▼
core.model.Problem          generic vocabulary: locations, items, routines, obstructions
        │  reach.core       simulation, cost, constraints, optimizers, sensitivity, coverage
        ▼
results                     assignment, per-routine metrics, reports
        │  reach.cli / reach.api / frontend
        ▼
terminal, HTTP, browser
```

`reach.core` is domain-agnostic. It never imports from `reach.io`, never reads files, and
contains no kitchen terms (a test enforces this). Everything kitchen-specific lives in the file
schemas, the sample data, the templates and the UI copy. Applying Reach to a workshop means
writing different input files, or at most a different `reach.io` front end.

## 2. Data model

### 2.1 Core (`reach.core.model`)

| Type | Fields | Notes |
| --- | --- | --- |
| `Location` | `id`, `position`, `kind`, `level`, `assignable`, `capacity_volume`, `capacity_weight`, `tags`, `geometry` | A place the actor can stand at. `position` is where the actor stands. `kind` selects the opening profile. `geometry` is opaque to the core. |
| `Item` | `id`, `name`, `category`, `volume`, `quantity`, `weight`, `estimated_frequency`, `nest_group`, `tags` | `volume` and `weight` are per unit. |
| `Step` | `kind`, `item`, `location`, `obstruction`, `state` | `kind` is one of `go_to`, `take`, `use`, `put_down`, `store`, `set_state`. |
| `Routine` | `id`, `name`, `frequency`, `start`, `steps`, `initial_states` | `start` is a location id or a raw point. |
| `Obstruction` | `id`, `default_state`, `blocking_state`, `blocks`, `clear`, `restore`, `position` | `blocks` is a set of location ids. |
| `CostParams` | speeds, opening profiles, level penalties, weights, nesting factors | See section 3. |
| `Problem` | locations, items, routines, obstructions, params, hard and soft constraints, pins | The complete optimization input. |

An **assignment** is a mapping `item id → location id`. Items with a pin (from `fixed_location`
or from the UI) always map to their pinned location.

Locations come in two flavours:

- **Assignable** locations have a capacity and can receive any item the constraints allow.
- **Non-assignable** locations (a fridge, a countertop zone, an appliance) only ever hold items
  that are pinned to them. They have no capacity limit.

### 2.2 Files (`reach.io.schemas`)

| File | Content |
| --- | --- |
| `kitchen.yaml` | `walls`, `level_ranges`, `pois`, `zones`, `compartments`, `obstructions`, `cost` |
| `items.csv` | one row per item |
| `constraints.yaml` | `hard` and `soft` constraint lists |
| `routines.yaml` | `routines` list |
| `current_assignment.csv` | optional, `item_id,location_id` |

The loader turns POIs, zones and compartments into `Location` objects. The translation rules:

- A compartment becomes an assignable location with `position = access_point`,
  `kind = type`, `capacity_volume = volume_l × max_fill_factor` and
  `capacity_weight = max_weight_kg`.
- A POI or zone becomes a non-assignable location with `kind = type` and `level = 1`.

### 2.3 Walls, heights and levels

The floor plan (top view, cm) is the only source of walking distances. Elevation geometry is
layered on top of it, because compartments stacked in the same column overlap in the top view.

- A **wall** is a segment `start → end` on the floor plan. Any elevation plane is a wall,
  including the face of an island.
- A compartment references a wall and gives its rectangle on it: `offset_cm` (distance along the
  wall from `start`), `width_cm`, `bottom_cm` and `top_cm` (heights above the floor).
- POIs and zones may give the same geometry. It is optional for them.

The level of a compartment is derived from its centre height
`h = (bottom_cm + top_cm) / 2` using `level_ranges`, an ordered list of `(level, max_cm)`: the
level is the first entry with `h < max_cm`; the last entry has no bound. Defaults:

| centre height | level | meaning |
| --- | --- | --- |
| below 60 cm | 0 | low, bending down |
| 60 to 160 cm | 1 | comfortable |
| 160 to 195 cm | 2 | high, reaching up |
| 195 cm and above | 3 | needs a step stool |

A compartment may set `level` explicitly, which overrides the derived value.

### 2.4 Obstructions

An obstruction is anything that, in one of its two states, blocks access to a set of locations:
an entrance door that covers a column of cabinets while it is open, or a dishwasher door that
covers the neighbouring drawers while it is open.

```yaml
obstructions:
  - id: entrance_door
    default_state: open        # state at the start of every routine
    blocks_when: open          # state in which it blocks
    wall_range: {wall: west, from_cm: 0, to_cm: 60}
    blocks: []                 # extra location ids, optional
    clear:   {time_s: 3, openings: 1}
    restore: {time_s: 3, openings: 1}
```

`wall_range` is resolved by the loader to every compartment, POI and zone on that wall whose
horizontal span `[offset, offset + width]` overlaps `[from_cm, to_cm]`. The core only sees the
resulting set of location ids.

A routine can start with a different state (`initial_states`) and can change a state mid-way
with a `set_state` step. This is how a door that is closed by default (the dishwasher) becomes
blocking only in the routines that open it.

## 3. Simulation and cost

### 3.1 Simulating one routine

The simulator walks one actor through the steps of a routine under an assignment `a`. Its state
is the actor's position, the location that is currently open, the items that were put down
somewhere, and the set of obstructions that are currently cleared.

Every step except `set_state` has a **target** location:

| Step | Target |
| --- | --- |
| `go_to X` | `X` |
| `take i` | where `i` was last put down in this routine, otherwise `a(i)` |
| `store i` | `a(i)` |
| `use i at X`, `put_down i at X` | `X` |

For each step, in order:

1. **Obstructions.** Every cleared obstruction that does not block the target is restored
   (`time += restore.time_s`, `openings += restore.openings`). Every obstruction that blocks the
   target, is in its blocking state and is not yet cleared, is cleared
   (`clearings += 1`, `time += clear.time_s`, `openings += clear.openings`). If the obstruction
   has a `position`, the actor walks there first in both cases.
2. **Walk.** `distance += ‖position − target‖` (Euclidean), then the actor is at the target.
3. **Act.**
   - `take` / `store`: if the target is not the currently open location, it is opened:
     `openings += open_cost(kind, level)`, `time += open_time(kind, level)`. Then
     `time += handling_time` and
     `effort += level_penalty[level] × (1 + weight_factor × weight_kg(i))`.
   - `use` / `put_down`: `time += handling_time`. The open location is closed.
   - `go_to`: the open location is closed.

At the end of the routine every obstruction that is still cleared is restored.

Consequences of these rules:

- Consecutive `take` / `store` steps in one compartment share a single opening.
- Consecutive steps targeting locations behind the same obstruction share a single clearing, even
  when they open different compartments.
- A `set_state` step costs nothing. It only changes whether the obstruction blocks from then on.

**Extra trips** (off unless `max_carry` is set). The actor can carry `max_carry` items. A `take`
that would exceed it adds one extra trip: a round trip between the current target and the next
step with a fixed location (`use`, `put_down` or `go_to`), after which only the new item is
carried. A run of consecutive `store` steps for items that were not taken earlier is treated as
carrying from the position where the run started; after every `max_carry` of them the actor
walks back to that position, which also counts as one extra trip.

Finally `time += distance / walking_speed`.

The simulator returns six metrics per routine:
`distance` (cm), `openings` (weighted count), `time` (s), `effort` (dimensionless),
`extra_trips` (count) and `clearings` (count). Clearings are included in `time` and `openings`
and are also reported on their own. With `trace=True` it also returns the timestamped path used
by the UI animation.

Walking is Euclidean between points and ignores furniture. See `DECISIONS.md`.

### 3.2 Capacity and nesting

For a location holding a set of items, units of the same `nest_group` nest:

```
V_group = v_max + α_g · (Σ_units v − v_max)        α_g = nesting factor of the group (default 0.25)
V_used  = Σ_groups V_group + Σ_{items without group} v_i · q_i
W_used  = Σ_items w_i · q_i
```

where the units of an item are its `quantity` copies and `v_max` is the largest unit volume in
the group. A location is within capacity when `V_used ≤ volume_l × max_fill_factor` and, when
`max_weight_kg` is given, `W_used ≤ max_weight_kg`.

### 3.3 Total cost

Let `M_m(r, a)` be metric `m` of routine `r` under assignment `a`, `f_r` the weekly frequency of
the routine, `w_m` the metric weights and `N_m` the normalizers.

```
cost(a) = Σ_r f_r · Σ_m  w_m · M_m(r, a) / N_m
        + Σ_i e_i · Σ_m  w_m · A_m(i, a) / N_m        (occasional items)
        + Σ_s penalty_s(a)

m ∈ {distance, openings, time, effort, extra_trips, clearings}
```

**Occasional items.** An item that no routine mentions would otherwise cost nothing wherever it
is, and its location would be arbitrary. Each such item `i` with an estimated frequency
`e_i > 0` is charged one generic access `A(i, a)` per estimated use: a round trip between a
central point (the *origin*) and its location.

```
distance  = 2 · ‖origin − a(i)‖
openings  = open_cost(a(i))           + clear and restore openings of obstructions that block by default
time      = distance / speed + open_time(a(i)) + handling_time + their clear and restore times
effort    = level_penalty[level] × (1 + weight_factor × weight_kg(i))
clearings = number of obstructions that block a(i) by default
```

The origin is `cost.occasional_items.origin` (a point or a POI/zone id) and defaults to the
centroid of all locations. `cost.occasional_items.enabled: false` turns the term off. The
evaluation reports it as its own line, with its share of the total; the weekly totals include
it. It is not part of the normalizers, which stay defined by the routines.

**Normalizers.** `N_m = Σ_r f_r · M_m(r, a₀)` is the weekly total of metric `m` under the greedy
initial solution `a₀` (1 if that total is 0). They are computed once, frozen, and stored with the
result, so every comparison (current assignment, sensitivity runs) uses the same scale. With
weights that sum to 1 the greedy solution costs exactly 1 before soft penalties, so a cost of
0.80 reads as "20% better than greedy". `cost.normalizers` in `kitchen.yaml` overrides them.

**Soft penalties.** A `together` constraint over items `G` with penalty `p` adds:

- `same_compartment`: `p × (number of distinct locations holding items of G − 1)`
- `nearby`: `p × mean over pairs of max(0, d(a(i), a(j)) − radius_cm) / 100`

Penalties are in units of total cost, so `p = 0.01` means "one extra compartment is worth 1% of
the greedy weekly cost".

### 3.4 Hard constraints

| Type | Meaning |
| --- | --- |
| `require_tag` | selected items may only go in locations carrying the tag |
| `forbid_tag` | selected items may not go in locations carrying the tag |
| `min_distance` / `max_distance` | distance from the location to a POI is at least / at most `distance_cm` |
| `min_level` / `max_level` | bounds on the location level |
| `separate` | items selected by `select` never share a location with items selected by `from` |

A selector matches an item by id, category or tag. The first six are unary: they are compiled
once into a **domain** per item, the list of assignable locations the item may use. A pinned item
has a domain of exactly its pin. `separate` and capacity are checked per location on every move.

## 4. Optimization

All optimizers implement `reach.core.optim.base.Optimizer`:

```python
class Optimizer(ABC):
    def optimize(
        self, problem, *, initial=None, normalizers=None, progress=None, cancel=None
    ) -> OptimizationResult: ...
```

### 4.1 Greedy initial solution

1. Place pinned items.
2. Sort the remaining items by routine-derived weekly frequency, descending (ties: estimated
   frequency, then volume, then id).
3. Place each item in the feasible location of its domain with the lowest score. The score
   estimates the weekly cost of living there: walking to the fixed points the item is used with,
   the opening, the ergonomic effort and any obstruction in front of the location. Items that no
   routine uses go to the feasible location with the most free space.
4. Repair: an item that fits nowhere tries to evict lower-priority items from a location of its
   domain into other feasible locations.

If the frequency order leaves items unplaced, the pass is repeated in the classic packing order
(smallest domain first, then largest volume). Annealing recovers the quality this order gives
up. If items still remain unplaced, the problem is reported as infeasible (section 4.3).

### 4.2 Simulated annealing

State: the assignment, plus the item list of every location. Moves, all restricted to domains so
that an infeasible state is never entered:

| Move | Probability | Description |
| --- | --- | --- |
| relocate | 0.40 | move one item to another location of its domain |
| swap | 0.30 | exchange the locations of two items |
| group | 0.10 | move all members of a nest group that share a location to another location |
| cluster | 0.10 | move the items of one routine that share a location, so they keep sharing an opening |
| exchange | 0.10 | swap everything movable between two locations |

The first three are the moves the spec asks for. The last two exist because the cost couples
items that share an opening: moving one of them alone looks bad even when moving all of them is
good. Without them the search stalled about 4% above the solutions it finds with them.

Items that no routine or soft constraint mentions never change the cost by themselves, so the
first item of a move is drawn from the "active" items 90% of the time. Inactive items still move,
mostly as the partner of a swap that frees a good location.

A move is rejected outright if a touched location would exceed capacity or break a `separate`
constraint. Otherwise it is accepted when `Δ ≤ 0` or with probability `exp(−Δ / T)`.

**Schedule.** `T₀` is calibrated from a sample of random feasible moves so that an average
uphill move starts with 10% acceptance (the greedy start is already good, so a hot start mostly
destroys it); the temperature then decays geometrically to `T₀ × 10⁻³`. The budget is an
iteration count or a wall-clock limit. The best solution seen is kept and returned, so the
result is never worse than the greedy start.

**Incremental cost.** The cost is not separable per item, but it is separable per routine. The
evaluator caches the weighted cost of every routine and of every soft constraint, and keeps an
index `item → routines` and `item → soft constraints`. A move re-simulates only the routines that
mention a moved item. The cached total is refreshed with a full recomputation periodically to
remove floating-point drift, and a test checks that the incremental and full costs agree after
random move sequences.

For speed the problem is compiled once to integer indices and a precomputed distance matrix
(`reach.core.compile`), so the inner loop does no dictionary lookups by id.

**Reproducibility.** All randomness comes from one `random.Random(seed)`. With an iteration
budget the result is a pure function of the inputs and the seed. With a wall-clock budget it also
depends on machine speed.

**Progress and stopping.** The optimizer calls `progress(ProgressEvent)` a few times per second
with the iteration, temperature, current and best cost, and can include the best assignment. It
checks a `cancel` event regularly and returns the best solution found so far when it is set.

### 4.3 Infeasibility report

When no feasible start exists, `reach.core.optim.feasibility` explains why:

- items with an **empty domain**, with the constraint that removed each location;
- items that are **too large or too heavy** for every location of their domain;
- **capacity shortfalls**: groups of items sharing the same domain whose combined volume exceeds
  the capacity of that domain;
- the items the greedy pass could not place, with the fullest locations of their domain.

## 5. Sensitivity analysis

Simulated annealing is stochastic, and a layout problem has many near-equivalent solutions. A
naive "perturb and re-optimize" analysis therefore mixes two things: movement caused by the
optimizer, and movement caused by the frequencies. `reach.core.sensitivity` separates them by
comparing two sets of runs with one baseline layout:

- **Noise runs** keep the frequencies and change only the seed.
- **Perturbed runs** also scale every routine frequency by an independent factor drawn from
  `U(1 − x, 1 + x)`.

All runs use the same iterations, restarts and normalizers, and their seeds derive from the base
seed. They execute in a process pool. `runs = 0` is the baseline mode (noise only).

For every item and each set, the report gives the share of runs in which the item stays

- in the **exact location** of the baseline, and
- in the same **area and level** (for the kitchen: the same wall and level), so that swapping
  between interchangeable neighbouring shelves does not count as instability.

Each set also reports the mean of those shares over the items that affect the cost, and its cost
spread. Items are labelled from the area-and-level shares with a threshold (0.8):

| Label | Meaning |
| --- | --- |
| `robust` | stays in both sets |
| `noisy` | moves even with unchanged frequencies: the optimizer sees the alternatives as equal |
| `sensitive` | stable under noise, moves when the frequencies change: depends on the estimates |
| `unused` | no routine and no estimated use; its location does not affect the cost |
| `pinned` | fixed by the user |

**Restarts.** `SimulatedAnnealing(restarts=k)` runs `k` independent searches from the same start
with derived seeds and keeps the best. It is the direct way to spend a larger budget on lower
variance, and it stays reproducible for a seed.

## 6. Coverage analysis

`reach.core.coverage` reports items that appear in no routine, suspicious routines (fewer than
two steps, an item taken and never used, put down or stored, an item used without being taken)
and, per item, the estimated frequency from the catalog next to the frequency derived from the
routines.

The usage frequency that enters the cost is always the one derived from the routines. The
catalog estimate is used only to order the greedy pass when an item appears in no routine, and
for this consistency check.

## 7. LLM helpers

`reach.llm` is optional. Each helper sends the catalog, the locations and a strict JSON schema to
the Anthropic API, validates the answer with the same Pydantic schemas as hand-written files, and
returns a **proposal**. Nothing is applied without an explicit approve step. Without
`ANTHROPIC_API_KEY`, or without the `llm` extra installed, the helpers report that they are
unavailable and everything else works.

| Helper | Input | Output |
| --- | --- | --- |
| constraints from comments | `items.csv` comments | `constraints.proposed.yaml` |
| routine from text | free text, a recipe, or a URL | a routine plus "suggested missing items" |
| diary import | a plain-text diary with `DD/MM` headers | routines with measured weekly frequencies, plus unclear entries |
| cover unused items | the coverage report | routines that use the uncovered items |

## 8. API and frontend

The FastAPI app holds one project in memory. Optimization and sensitivity run as jobs in a child
process, report progress through a queue, and are observable over Server-Sent Events. The
frontend is a React + Vite + TypeScript single-page app that draws the floor plan and the wall
elevations in SVG. In production the API serves the built frontend.

## 9. Folder structure

```
src/reach/
  core/
    model.py          generic data model
    compile.py        integer-indexed form of a Problem, distance matrix
    capacity.py       volume with nesting, weight
    constraints.py    domains, per-location checks, soft penalties, assignment validation
    simulate.py       routine simulation and path trace
    cost.py           normalizers, weighted total, incremental evaluator
    optim/
      base.py         Optimizer interface, progress and result types
      greedy.py       feasible initial solution
      annealing.py    simulated annealing
      feasibility.py  infeasibility report
    sensitivity.py
    coverage.py
  io/
    schemas.py        Pydantic models of the input files
    yaml_loader.py    YAML loader that remembers line numbers
    csv_loader.py     items and assignment CSV
    errors.py         Issue and ValidationReport
    project.py        loads a folder into a Problem, cross-file checks
    export.py         assignment CSV, result summaries
  llm/                optional Anthropic-backed proposals
  api/                FastAPI app, job runner
  cli.py              Typer command line
frontend/             React + Vite + TypeScript
data/sample/          the sample kitchen
data/templates/       commented empty input files
tests/                unit, integration, e2e
```
