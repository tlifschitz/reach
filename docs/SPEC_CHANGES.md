# Spec changes

Changes to the original specification ([ORIGINAL_PROMPT.md](ORIGINAL_PROMPT.md)), recorded
verbatim as they were sent to Claude Code.

## 2026-10-12 — Obstructions and elevation views

Sent during planning, before the first implementation.

**Why:** in my kitchen, the entrance door covers a full column of cabinets when open, and the 2D
floor plan couldn't show compartments stacked at different heights.

```
Before approving, I have two more requirement to the plan:

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

Include an obstruction in the sample kitchen and a hand-computed unit test for it. Update the
plan, including how obstructions enter the cost function.

## Heights and visualization
The floor plan is 2D (top view) and is used for walking distances, but compartments stacked in
the same column overlap there. Add elevation views:

- Define walls in kitchen.yaml (id, start and end points on the floor plan).
- Each compartment references a wall and has its real geometry on it: horizontal position along
  the wall, width, bottom height, and top height (cm). Its access_point stays in the floor plan.
- Derive `level` from the compartment's height using configurable height ranges, with an
  optional manual override (e.g. to mark that a compartment requires a step stool).
- UI: the top view shows POIs, zones, walls, and routine paths. Clicking a wall opens its
  elevation view, with compartments drawn at real size and position, colored by level, showing
  their assigned items and capacity used.
- Obstructions can reference a wall range instead of listing compartments one by one.

Update the data model and the plan accordingly and show it to me again.

```