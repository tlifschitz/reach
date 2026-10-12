"""Loading and validation: every error names its file, line and field."""

from pathlib import Path

import pytest

from reach.io.project import load_project, load_project_from_texts, read_project_files
from reach.io.schemas import LevelRange, default_level_ranges, derive_level

SAMPLE = Path(__file__).parents[2] / "data" / "sample"

KITCHEN = """\
walls:
  - {id: north, start: [0, 0], end: [300, 0]}
pois:
  - {id: sink, type: sink, position: [50, 60]}
zones:
  - {id: counter, type: countertop, position: [150, 60], size: [100, 60],
     wall: north, offset_cm: 100, width_cm: 100, bottom_cm: 85, top_cm: 90}
compartments:
  - id: low
    type: drawer
    access_point: [50, 60]
    wall: north
    offset_cm: 0
    width_cm: 60
    bottom_cm: 10
    top_cm: 40
    volume_l: 30
  - id: mid
    type: drawer
    access_point: [150, 60]
    wall: north
    offset_cm: 100
    width_cm: 60
    bottom_cm: 60
    top_cm: 85
    volume_l: 30
    tags: [handy]
  - id: high
    type: door
    access_point: [250, 60]
    wall: north
    offset_cm: 200
    width_cm: 60
    bottom_cm: 200
    top_cm: 230
    volume_l: 40
"""
ITEMS = """\
id,name,category,volume_l,quantity,weight_kg,estimated_frequency_per_week,nest_group,fixed_location,tags,comments
mug,Mug,cups,0.5,4,0.3,14,,,fragile;daily,
soap,Soap,cleaning,1.0,1,1.0,7,,,,
"""
ROUTINES = """\
routines:
  - id: tea
    name: Tea
    frequency_per_week: 7
    start: sink
    steps:
      - take: mug
      - put_down: mug at counter
"""
CONSTRAINTS = """\
hard:
  - id: soap_low
    type: max_level
    select: {categories: [cleaning]}
    level: 1
"""


def load(**overrides):
    texts = {
        "kitchen.yaml": KITCHEN,
        "items.csv": ITEMS,
        "routines.yaml": ROUTINES,
        "constraints.yaml": CONSTRAINTS,
    }
    texts.update(overrides)
    return load_project_from_texts({k: v for k, v in texts.items() if v is not None})


def errors(project):
    return [(i.file, i.line, i.field, i.message) for i in project.report.errors]


def test_minimal_project_loads():
    p = load()
    assert p.ok, errors(p)
    assert set(p.problem.locations) == {"sink", "counter", "low", "mid", "high"}
    mug = p.problem.items["mug"]
    assert mug.tags == {"fragile", "daily"} and mug.quantity == 4
    assert p.problem.locations["mid"].capacity_volume == pytest.approx(30 * 0.8)


def test_levels_are_derived_from_heights():
    p = load()
    assert p.levels == {"low": 0, "mid": 1, "high": 3}
    assert [p.problem.locations[c].level for c in ("low", "mid", "high")] == [0, 1, 3]


def test_level_override_wins():
    p = load(
        **{"kitchen.yaml": KITCHEN.replace("    volume_l: 40", "    volume_l: 40\n    level: 2")}
    )
    assert p.levels["high"] == 2


def test_derive_level_boundaries():
    ranges = default_level_ranges()
    assert derive_level(10, 38, ranges) == 0  # centre 24
    assert derive_level(50, 70, ranges) == 1  # centre 60: the bound is exclusive
    assert derive_level(140, 172, ranges) == 1  # centre 156
    assert derive_level(172, 205, ranges) == 2  # centre 188.5
    assert derive_level(200, 235, ranges) == 3
    custom = [LevelRange(level=0, max_cm=100), LevelRange(level=3)]
    assert derive_level(90, 120, custom) == 3


def test_custom_level_ranges_in_the_file():
    text = "level_ranges:\n  - {level: 0, max_cm: 100}\n  - {level: 2}\n" + KITCHEN
    assert load(**{"kitchen.yaml": text}).levels == {"low": 0, "mid": 0, "high": 2}


def test_yaml_error_reports_file_line_and_field():
    bad = KITCHEN.replace(
        "    volume_l: 30\n    tags: [handy]", "    volume_l: -5\n    tags: [handy]"
    )
    p = load(**{"kitchen.yaml": bad})
    assert errors(p) == [
        ("kitchen.yaml", 26, "compartments[1].volume_l", "Input should be greater than 0")
    ]
    assert str(p.report.errors[0]).startswith("kitchen.yaml:26: error: compartments[1].volume_l")


def test_unknown_key_is_rejected_with_its_line():
    p = load(
        **{"kitchen.yaml": KITCHEN.replace("    type: door", "    type: door\n    colour: red")}
    )
    assert errors(p) == [
        ("kitchen.yaml", 30, "compartments[2].colour", "Extra inputs are not permitted")
    ]


def test_yaml_syntax_error_has_a_line():
    p = load(**{"routines.yaml": "routines:\n  - id: tea\n   name: [unclosed\n"})
    (issue,) = p.report.errors
    assert issue.file == "routines.yaml" and issue.line is not None
    assert "invalid YAML" in issue.message


def test_csv_error_reports_row_and_column():
    bad = ITEMS.replace("soap,Soap,cleaning,1.0,1,", "soap,Soap,cleaning,lots,0,")
    p = load(**{"items.csv": bad})
    assert sorted(errors(p)) == [
        ("items.csv", 3, "quantity", "Input should be greater than or equal to 1"),
        (
            "items.csv",
            3,
            "volume_l",
            "Input should be a valid number, unable to parse string as a number",
        ),
    ]


def test_duplicate_item_and_unknown_column():
    p = load(**{"items.csv": ITEMS + "mug,Again,cups,1,1,0,0,,,,\n"})
    assert errors(p) == [("items.csv", 4, "id", "duplicate item id 'mug' (first at line 2)")]
    p = load(**{"items.csv": ITEMS.replace("comments", "notes")})
    assert errors(p)[0][:3] == ("items.csv", 1, "notes")


def test_unknown_references_are_reported_per_file():
    p = load(
        **{
            "items.csv": ITEMS.replace("1.0,7,,,,", "1.0,7,,cellar,,"),
            "routines.yaml": ROUTINES.replace("take: mug", "take: cup").replace(
                "mug at counter", "mug at table"
            ),
            "constraints.yaml": CONSTRAINTS.replace(
                "select: {categories: [cleaning]}", "select: {items: [bleach]}"
            ),
        }
    )
    assert sorted(errors(p)) == [
        ("constraints.yaml", 4, "hard[0].select.items[0]", "unknown item 'bleach'"),
        ("items.csv", 3, "fixed_location", "unknown location 'cellar'"),
        ("routines.yaml", 7, "routines[0].steps[0]", "unknown item 'cup'"),
        ("routines.yaml", 8, "routines[0].steps[1]", "unknown POI or zone 'table'"),
    ]
    assert p.problem is None


def test_bad_step_syntax_is_explained():
    p = load(**{"routines.yaml": ROUTINES.replace("put_down: mug at counter", "put_down: mug")})
    assert errors(p) == [
        (
            "routines.yaml",
            8,
            "routines[0].steps[1]",
            "put_down is written '<item_id> at <poi_or_zone>'",
        )
    ]
    p = load(**{"routines.yaml": ROUTINES.replace("take: mug", "grab: mug")})
    assert "unknown step 'grab'" in errors(p)[0][3]


def test_geometry_checks():
    p = load(**{"kitchen.yaml": KITCHEN.replace("offset_cm: 200", "offset_cm: 280")})
    assert errors(p) == [
        (
            "kitchen.yaml",
            32,
            "compartments[2].offset_cm",
            "extends to 340 cm but wall 'north' is 300 cm long",
        )
    ]
    p = load(
        **{
            "kitchen.yaml": KITCHEN.replace(
                "    wall: north\n    offset_cm: 200", "    wall: south\n    offset_cm: 200"
            )
        }
    )
    assert errors(p) == [("kitchen.yaml", 31, "compartments[2].wall", "unknown wall 'south'")]
    p = load(**{"kitchen.yaml": KITCHEN.replace("bottom_cm: 200", "bottom_cm: 240")})
    assert "bottom_cm must be below top_cm" in errors(p)[0][3]


def test_overlapping_compartments_warn():
    p = load(
        **{
            "kitchen.yaml": KITCHEN.replace(
                "offset_cm: 100\n    width_cm: 60\n    bottom_cm: 60",
                "offset_cm: 30\n    width_cm: 60\n    bottom_cm: 30",
            )
        }
    )
    assert p.ok
    assert any("overlaps" in w.message for w in p.report.warnings)


OBSTRUCTION = """\
obstructions:
  - id: door
    default_state: open
    blocks_when: open
    wall_range: {wall: north, from_cm: 90, to_cm: 170}
    blocks: [sink]
    clear: {time_s: 3, openings: 1}
    restore: {time_s: 3, openings: 1}
"""


def test_wall_range_resolves_to_overlapping_compartments_and_zones():
    p = load(**{"kitchen.yaml": KITCHEN + OBSTRUCTION})
    assert p.ok, errors(p)
    # 'mid' spans 100-160 and the counter 100-200; 'low' (0-60) and 'high' (200-260) do not overlap
    assert sorted(p.blocks["door"]) == ["counter", "mid", "sink"]
    (door,) = p.problem.obstructions
    assert door.blocks == {"counter", "mid", "sink"}
    assert (door.clear.time_s, door.restore.openings) == (3, 1)


def test_wall_range_touching_an_edge_does_not_block():
    text = KITCHEN + OBSTRUCTION.replace(
        "from_cm: 90, to_cm: 170", "from_cm: 160, to_cm: 200"
    ).replace("    blocks: [sink]\n", "")
    p = load(**{"kitchen.yaml": text})
    assert p.blocks["door"] == ["counter"]


def test_obstruction_reference_errors():
    p = load(**{"kitchen.yaml": KITCHEN + OBSTRUCTION.replace("blocks: [sink]", "blocks: [oven]")})
    assert errors(p) == [
        ("kitchen.yaml", 42, "obstructions[0].blocks[0]", "unknown compartment, POI or zone 'oven'")
    ]
    p = load(
        **{
            "kitchen.yaml": KITCHEN
            + "obstructions:\n  - {id: d, default_state: open, blocks_when: open}\n"
        }
    )
    assert "give 'blocks', 'wall_range' or both" in errors(p)[0][3]
    steps = ROUTINES + "      - set_state: hatch open\n"
    p = load(**{"kitchen.yaml": KITCHEN + OBSTRUCTION, "routines.yaml": steps})
    assert errors(p) == [
        ("routines.yaml", 9, "routines[0].steps[2]", "unknown obstruction 'hatch'")
    ]


def test_pin_that_breaks_a_rule_warns_but_wins():
    p = load(**{"items.csv": ITEMS.replace("1.0,7,,,,", "1.0,7,,high,,")})
    assert p.ok
    assert p.problem.pins == {"soap": "high"}
    assert any("the pin wins" in w.message for w in p.report.warnings)


def test_extra_pins_override_the_file():
    texts = {"kitchen.yaml": KITCHEN, "items.csv": ITEMS, "routines.yaml": ROUTINES}
    p = load_project_from_texts(texts, extra_pins={"mug": "mid"})
    assert p.problem.pins == {"mug": "mid"}


def test_missing_files_are_reported():
    p = load(**{"routines.yaml": None, "kitchen.yaml": None})
    assert sorted(e[:1] + e[3:] for e in errors(p)) == [
        ("kitchen.yaml", "file is missing"),
        ("routines.yaml", "file is missing"),
    ]


def test_current_assignment_is_completed_with_pins_or_disabled():
    current = "item_id,location_id\nmug,mid\nsoap,low\n"
    p = load(**{"current_assignment.csv": current})
    assert p.current_assignment == {"mug": "mid", "soap": "low"}
    p = load(**{"current_assignment.csv": "item_id,location_id\nmug,mid\n"})
    assert p.ok and p.current_assignment is None
    assert any("comparison is disabled" in w.message for w in p.report.warnings)


def test_sample_project_is_valid():
    p = load_project(SAMPLE)
    assert p.ok, [str(i) for i in p.report.errors]
    assert p.report.issues == []
    problem = p.problem
    assert sum(loc.assignable for loc in problem.locations.values()) == 44
    assert len(problem.items) >= 150 and len(problem.routines) >= 12
    assert p.current_assignment is not None
    assert sorted(p.blocks["entrance_door"]) == [
        "counter_corner",
        "n1_base",
        "n1_up_high",
        "n1_up_low",
    ]
    assert p.levels["n3_up_high"] == 3  # manual override; the height alone gives 2
    assert set(read_project_files(SAMPLE)) >= {"kitchen.yaml", "items.csv", "routines.yaml"}


def test_templates_are_valid_yaml_and_csv():
    templates = SAMPLE.parent / "templates"
    p = load_project(templates)
    structural = [
        i
        for i in p.report.errors
        if "no compartments" not in i.message and "no items" not in i.message
    ]
    assert structural == [], [str(i) for i in structural]
