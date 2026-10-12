# Surveying your kitchen

A practical guide to turning a real kitchen into the files Reach needs. Budget an afternoon:
about an hour for the space, two for the items, and one for the routines. You need a tape
measure, a phone for photos, and the templates in [`data/templates`](../data/templates).

Work in this order. Run `reach validate <folder>` after each step; it points at the file, line
and field of every problem.

1. [The floor plan](#1-the-floor-plan)
2. [Walls and compartments](#2-walls-and-compartments)
3. [Obstructions](#3-obstructions)
4. [Items](#4-items)
5. [Routines](#5-routines)
6. [Constraints](#6-constraints)
7. [Your current layout](#7-your-current-layout-optional)

## 1. The floor plan

**Pick the origin.** Stand in the kitchen and choose one corner of the room as `(0, 0)`. Let
`x` grow along one wall and `y` along the wall at a right angle to it, so that every coordinate
in the room is positive. Everything is in centimetres. Sketch the room on paper first and write
the corner coordinates on it; you will refer to that sketch constantly.

**Measure where you stand, not where things are.** Reach computes walking distance between
standing points. For the stove, the standing point is where your feet are when you cook: about
35 cm in front of the cabinet face. If the counters are 60 cm deep along the wall `y = 0`, the
standing points along that wall have `y ≈ 95`.

**List the points of interest** (`pois`): stove, oven, sinks, dishwasher, fridge, freezer,
fixed appliances, the dish rack, the trash, outlets, the entrance. Each needs an `id`, a `type`
and a standing `position`. The fridge and freezer need the types `fridge` and `freezer` to pick
up their opening cost.

**List the zones** (`zones`): the countertop areas where you work or leave things, and the
table. Split a long counter into the stretches you actually use differently ("prep counter",
"coffee station"). Give each its standing `position` and its `size`.

## 2. Walls and compartments

Stacked compartments overlap in a top view, so each one is also placed on a wall elevation.

**Walls.** Add every wall that has storage on it, and each face of an island, to `walls`.
A wall goes from `start` to `end`. Face the wall from inside the room: `start` is its **left**
end and `end` its right end. That way offsets grow left to right in the elevation view, the same
way you see the wall.

**For each compartment** (every drawer, every shelf behind a door, every pantry shelf) measure:

| Field | How to measure |
| --- | --- |
| `offset_cm` | From the left end of the wall to the left edge of the compartment. |
| `width_cm` | Outer width of the front. |
| `bottom_cm`, `top_cm` | Height of the bottom and top edge above the floor. For a shelf, the shelf surface and the next shelf above. |
| `access_point` | Where you stand to use it. Usually the centre of its column, 35 cm in front. Compartments in one column share it. |
| `volume_l` | **Usable interior** volume: width × depth × height of the inside, in cm, divided by 1000. Subtract pipes, hinges and anything in the way. |
| `max_fill_factor` | How much of that you are willing to fill. 0.7 to 0.8 for drawers, 0.5 to 0.7 for deep cabinets where things at the back get lost. |
| `max_weight_kg` | Only if it matters: wall cabinets and drawer runners have limits (often 15 to 30 kg). |
| `tags` | Words your constraints will use: `child_reachable`, `near_heat`, `damp`, `near_main_sink`. |

A cabinet with two shelves behind one door is two compartments with the same `access_point` and
different heights. Each shelf counts its own opening, which slightly overstates the cost of
taking from both; it is the simplest honest model.

**Levels.** You do not enter the level. It is derived from the centre height of the compartment:

| Centre height | Level |
| --- | --- |
| below 60 cm | 0, bend down |
| 60 to 160 cm | 1, comfortable |
| 160 to 195 cm | 2, reach up |
| 195 cm and above | 3, step stool |

Adjust `level_ranges` to your own height: stand at the counter and note the highest shelf you
reach without stretching, and the highest you reach at all. If a particular compartment is
harder than its height suggests (the shelf above the sink, a deep corner), set `level` on it.

**Tip.** Photograph each wall straight on before you start. Measuring from the photo afterwards
is unreliable, but the photo settles every "was that two shelves or three" question.

## 3. Obstructions

An obstruction is anything that is in the way of compartments or zones while it is in one of its
states. Walk through the kitchen and ask, for every door: *with this open, what can I not get
to?*

- A room or balcony door that swings against cabinets: usually open, blocks while open.
  `default_state: open`, `blocks_when: open`.
- A dishwasher or oven door: usually closed, blocks the neighbouring drawers while open.
  `default_state: closed`, `blocks_when: open`. It only matters in routines that open it, which
  they do with a `set_state` step.

Say what is blocked with `wall_range` (everything on that stretch of the wall, floor to ceiling)
or with an explicit list in `blocks`. Time how long it takes to move it out of the way and to
put it back; 2 to 3 seconds each is typical.

## 4. Items

One row per kind of item in `items.csv`. Go compartment by compartment, photograph the contents,
and fill the table from the photos at a desk.

**What is an item.** Things you take as a unit: "dinner plates" (quantity 8), not eight rows.
Cutlery is best as one row per type ("forks"). Skip what never leaves its place and is never
handled (the microwave), unless you want it in the capacity count.

**Volume.** `volume_l` is the bounding box of **one unit**: width × depth × height in cm,
divided by 1000. It is the space the thing takes on a shelf, not how much liquid it holds. A
24 cm pot that is 14 cm tall with handles is about 34 × 26 × 14 / 1000 ≈ 12 litres. Round
generously; two significant figures are plenty.

**Nesting.** Give things that stack or nest the same `nest_group` (`plates`, `pots`,
`containers`). A stack then takes the largest unit plus a fraction of each further one. To
calibrate the fraction for a group, measure one stack: for 8 plates, 2.5 cm for the first and
1.5 cm for each further one gives 1.5 / 2.5 = 0.6. Set it under `cost.nesting.groups`.

**Weight.** `weight_kg` of one unit. A kitchen scale handles most things; it only needs to be
right for heavy items.

**Frequency.** `estimated_frequency_per_week` is a guess, and it is only used as a cross-check
against your routines. Useful anchors: daily = 7, twice a day = 14, weekdays = 5, monthly = 0.25,
twice a year = 0.04. People overestimate rare items, so when in doubt go lower.

**Fixed things.** Put the location id in `fixed_location` for anything that will not move:
fridge and freezer contents, appliances that live on the counter, or an item you have decided
on.

**Tags and comments.** Tag what your constraints will need (`sharp`, `heavy`, `fragile`). Write
anything else in `comments` in plain words; `reach llm constraints` can turn those into
proposed constraints.

## 5. Routines

Routines decide the result more than anything else, so spend the time here.

**One routine, one activity.** "Espresso", "toast", "pack lunch", each with its own weekly
frequency. Not "breakfast": the parts happen at different rates and in different combinations.
Cleaning up is separate too: "unload the dishwasher", "unload the dish rack".

**Write what you do, in order.** Do the activity once, slowly, and narrate it:

```yaml
- id: evening_tea
  name: Evening tea
  frequency_per_week: 7
  start: table
  steps:
    - take: electric_kettle
    - use: electric_kettle at main_sink
    - put_down: electric_kettle at coffee_station
    - take: mug
    - take: tea_tin_herbal
    - use: tea_tin_herbal at coffee_station
    - store: tea_tin_herbal
    - put_down: mug at table
```

- `take` walks to wherever the item is stored. Do not put a `go_to` before it.
- Group the takes the way you really do. Taking three things in a row from one drawer is one
  opening; coming back for the third is two.
- Everything you take should later be used, put down or stored. Things that go back end in
  `store`; things that leave (a plate to the table) end in `put_down`.
- `start` is where you are when you begin: the entrance in the morning, the table after dinner.

**Frequencies from a diary.** Estimates are unreliable. For two weeks, note in your phone what
you did in the kitchen each day under a `DD/MM` header, then run
`reach llm diary my_diary.txt --project <folder>`. It groups the entries into routines and
computes weekly frequencies from the days you recorded. See
[`data/sample/diary.txt`](../data/sample/diary.txt) for the format.

**Check coverage.** `reach coverage <folder>` lists items no routine uses, routines that look
incomplete, and items whose estimated frequency disagrees with the routines. Items no routine
mentions cannot be placed well, because the optimizer has no idea when you need them. Aim to
cover everything you use at least monthly.

## 6. Constraints

Write down rules as you notice them while surveying, in plain words first, then in
`constraints.yaml`. The usual ones:

- Safety: knives and chemicals away from children (`min_level`, `forbid_tag`), heavy things low
  (`max_level`), cleaning products never with food (`separate`).
- Physics: spices, oils and chocolate away from heat, dry goods away from damp cabinets
  (`forbid_tag`).
- Convenience you insist on: cleaning products near the utility sink (`max_distance`).
- Things that belong together: the espresso kit, the cutlery (`together`, a soft constraint).

Keep hard constraints for real rules. Every hard constraint shrinks what the optimizer can do,
and a preference written as a hard rule can make the problem infeasible. If that happens,
`reach validate` tells you which items have nowhere to go and which rule removed their options.

## 7. Your current layout (optional)

To get a before/after comparison, record where things are today in `current_assignment.csv`
(`item_id,location_id`). You already walked through every compartment for step 4, so note the
compartment id next to each item then. Items with a `fixed_location` can be left out.

The current layout may break your own rules; Reach reports which ones and still simulates it.
