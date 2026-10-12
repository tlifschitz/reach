# items.csv

CSV files cannot hold comments, so the columns are described here.

| Column | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Short unique id without spaces, e.g. `dinner_plate`. Routines refer to it. |
| `name` | no | Display name. |
| `category` | no | Free word such as `cookware`. Constraints can select by category. |
| `volume_l` | yes | Volume of **one unit** in litres: its bounding box, not its capacity. |
| `quantity` | no | Number of units stored together. Default 1. |
| `weight_kg` | no | Weight of one unit. |
| `estimated_frequency_per_week` | no | Your guess of how often you use it. Used as a consistency check against the routines. |
| `nest_group` | no | Items with the same group stack or nest inside each other. |
| `fixed_location` | no | Id of a compartment, POI or zone the item is pinned to (fridge, countertop, or a spot you want locked). |
| `tags` | no | Words separated by `;`, e.g. `sharp;fragile`. Constraints can select by tag. |
| `comments` | no | Free text. `reach llm constraints` can turn these into proposed constraints. |

Example rows:

```csv
dinner_plate,Dinner plates,tableware,1.8,8,0.55,14,plates,,,
chef_knife,Chef's knife,knives,0.5,1,0.25,10,,,sharp,Keep out of the low drawers
milk,Milk,fridge_food,1.2,2,1.0,14,,fridge,,
electric_kettle,Electric kettle,appliances,5.0,1,1.0,17,,coffee_station,,Lives on the counter
```
