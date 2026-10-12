# Reach

**Optimize where things live, starting with your kitchen.**

> 🚧 **Early development — building in public.** Nothing works yet. Follow along: the roadmap below
> gets updated as features land.

## The problem

Where you store things shapes every routine. If the espresso tamper lives two drawers away from
the grinder, or the pot you use daily sits behind a door that needs a step stool, you pay for it
in steps, openings, and bending down — every single day.

Most people organize their kitchen by intuition. That works, but it can't answer questions like:

- How much walking does my morning coffee actually involve?
- If I move the pans to this drawer, which routines get better and which get worse?
- Is my layout robust, or does it only work if my guesses about how often I cook are right?

## The idea

Reach treats storage as an optimization problem:

1. **Describe the space.** A 2D floor plan of your kitchen: drawers, cabinet doors, pantry
   shelves, the stove, sinks, fridge, and countertop zones, with their positions, heights, and
   capacities.
2. **List your items.** Each one with its volume, weight, and whether it nests with others (pots,
   plates, food containers).
3. **Describe your routines.** Everyday activities — making espresso, evening tea, washing dishes,
   a specific recipe — as sequences of steps, each with a weekly frequency.
4. **Optimize.** Reach simulates every routine under a given storage layout, measures the cost,
   and searches for the assignment of items to compartments that minimizes it.

### What it measures

- **Distance walked** between compartments and work areas.
- **Openings** of drawers and doors (opening a drawer once to take three things counts once).
- **Estimated time**, combining walking and openings.
- **Ergonomic effort**: bending down, reaching up, and climbing a step stool, weighted by how
  heavy the item is.

Each metric is reported per routine and as a weekly total, so you can see the trade-offs instead
of trusting a single score.

### What it respects

Real kitchens have rules. Reach supports **hard constraints** (cleaning products never next to
food, knives out of children's reach, heavy things low, items you want pinned in place) and
**soft constraints** (keep all the espresso gear together).

### Writing routines without the tedium

Describing routines step by step is the hardest part, so Reach helps:

- **From natural language:** describe an activity or paste a recipe, and an LLM drafts the
  routine using your actual item catalog.
- **From a usage diary:** jot down what you did in the kitchen each day in a phone note, import
  the file, and get routines with *measured* frequencies instead of guesses.
- **Coverage analysis:** see which items no routine uses and where your estimates disagree.

LLM features are optional and only ever *propose*: everything is reviewed by you before it
affects the optimization. Reach works fully without an API key.

### Trusting the result

Usage frequencies are estimates. Reach's sensitivity analysis re-optimizes under perturbed
frequencies and shows which item locations are robust and which depend on your guesses.

## Beyond the kitchen

The engine is domain-agnostic: locations, items, tasks, and a cost model. The kitchen is the
first use case, but the same approach applies to workshops, labs, supply rooms, or small
warehouses. In logistics this family of problems is known as *slotting optimization*.

## Roadmap

- [ ] Data model and validation (floor plan, items, constraints, routines)
- [ ] Routine simulation and metrics
- [ ] Optimizer (greedy initial solution + simulated annealing)
- [ ] CLI
- [ ] Web interface with floor plan view
- [ ] Sample kitchen and data collection guide
- [ ] Sensitivity analysis
- [ ] LLM-assisted routine generation and constraint suggestions
- [ ] Usage diary import
- [ ] Animated routine paths on the floor plan
- [ ] My own kitchen, measured: real before/after results

## Building in public

This project is being built with AI-assisted development. The original specification is in
[`docs/ORIGINAL_PROMPT.md`](docs/ORIGINAL_PROMPT.md), and design decisions will be logged in
`docs/DECISIONS.md` as they're made.


## License

MIT