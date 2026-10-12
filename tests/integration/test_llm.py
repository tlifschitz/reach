"""The LLM helpers, driven by a scripted client. No network."""

from pathlib import Path

import pytest

from reach.io.project import load_project
from reach.llm import (
    LlmUnavailableError,
    import_diary,
    llm_status,
    parse_diary,
    propose_constraints,
    propose_coverage_routines,
    propose_routines,
    validate_routine,
)
from reach.llm.client import AnthropicClient
from reach.llm.proposals import LlmError, catalog_context, html_to_text
from reach.llm.schemas import (
    ConstraintDraft,
    DiaryActivity,
    DiaryDraft,
    LlmHard,
    LlmRoutine,
    LlmSoft,
    LlmStep,
    MissingItem,
    RoutineDraft,
    UnclearEntry,
)

SAMPLE = Path(__file__).parents[2] / "data" / "sample"


class FakeClient:
    """Returns a prepared answer and records what it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def complete(self, *, system, prompt, schema):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        answer = self.answer(prompt) if callable(self.answer) else self.answer
        assert isinstance(answer, schema)
        return answer


@pytest.fixture(scope="module")
def project():
    p = load_project(SAMPLE)
    assert p.ok
    return p


def steps(*specs):
    out = []
    for kind, item, at in specs:
        out.append(LlmStep(kind=kind, item=item, at=at))
    return out


SMOOTHIE = LlmRoutine(
    id="smoothie",
    name="Make a smoothie",
    frequency_per_week=3,
    start="entrance",
    steps=steps(
        ("take", "blender", None),
        ("put_down", "blender", "counter_prep"),
        ("take", "milk", None),
        ("use", "milk", "counter_prep"),
        ("store", "milk", None),
        ("take", "water_glass", None),
        ("put_down", "water_glass", "table"),
        ("take", "blender", None),
        ("use", "blender", "main_sink"),
        ("store", "blender", None),
    ),
)


def test_catalog_context_lists_every_id(project):
    text = catalog_context(project)
    for needle in (
        "dinner_plate | Dinner plates",
        "main_sink | sink",
        "coffee_station",
        "entrance_door",
        "espresso | Make an espresso",
    ):
        assert needle in text
    assert "espresso | Make an espresso" not in catalog_context(project, routines=False)


def test_routine_from_text_is_validated_and_keeps_missing_items(project):
    draft = RoutineDraft(
        routines=[SMOOTHIE],
        missing_items=[
            MissingItem(name="frozen berries", reason="mentioned but not in the catalog")
        ],
        notes="Frequency estimated.",
    )
    client = FakeClient(draft)
    result = propose_routines(project, "I make a berry smoothie three times a week", client)
    (proposal,) = result.routines
    assert proposal.issues == []
    assert proposal.routine["id"] == "smoothie"
    assert proposal.routine["steps"][1] == {"put_down": "blender at counter_prep"}
    assert "put_down: blender at counter_prep" in proposal.yaml
    assert result.missing_items == [
        {"name": "frozen berries", "reason": "mentioned but not in the catalog"}
    ]
    call = client.calls[0]
    assert "berry smoothie" in call["prompt"] and "blender | Blender" in call["prompt"]
    assert "Never invent" in call["system"] and "atomic" in call["system"]


def test_invented_ids_are_flagged_not_accepted(project):
    bad = SMOOTHIE.model_copy(
        update={
            "steps": steps(("take", "frozen_berries", None), ("use", "frozen_berries", "island"))
        }
    )
    result = propose_routines(
        project, "smoothie", FakeClient(RoutineDraft(routines=[bad], missing_items=[], notes=""))
    )
    assert sorted(result.routines[0].issues) == [
        "steps[0]: unknown item 'frozen_berries'",
        "steps[1]: unknown POI or zone 'island'",
        "steps[1]: unknown item 'frozen_berries'",
    ]


def test_structural_warnings_reach_the_reviewer(project):
    dangling = SMOOTHIE.model_copy(
        update={"steps": steps(("take", "blender", None), ("go_to", None, "table"))}
    )
    result = propose_routines(
        project, "x", FakeClient(RoutineDraft(routines=[dangling], missing_items=[], notes=""))
    )
    assert any("never used" in i for i in result.routines[0].issues)


def test_validate_routine_accepts_edited_yaml_and_rejects_bad_syntax(project):
    model, issues = validate_routine(
        project,
        {
            "id": "t",
            "name": "T",
            "frequency_per_week": 1,
            "start": "table",
            "steps": [{"take": "mug"}, {"put_down": "mug at table"}],
        },
    )
    assert model is not None and issues == []
    model, issues = validate_routine(
        project,
        {"id": "t", "frequency_per_week": 1, "start": "table", "steps": [{"take": "mug at table"}]},
    )
    assert model is None and "take needs a single item id" in issues[0]


def test_empty_request_and_oversized_input_are_refused(project):
    with pytest.raises(LlmError, match="describe the activity"):
        propose_routines(project, "  ", FakeClient(None))
    with pytest.raises(LlmError, match="limit"):
        propose_routines(project, "x" * 70_000, FakeClient(None))


def test_html_is_reduced_to_text():
    page = (
        "<html><head><title>t</title><style>p{}</style></head><body><nav>menu</nav>"
        "<h1>Pancakes</h1><p>Mix   flour<br>and milk.</p><script>x()</script></body></html>"
    )
    assert html_to_text(page) == "Pancakes\nMix flour\nand milk."


def test_coverage_proposals_only_list_unused_items(project):
    client = FakeClient(RoutineDraft(routines=[SMOOTHIE], missing_items=[], notes=""))
    result = propose_coverage_routines(project, client)
    section = client.calls[0]["prompt"].split("## Items that no routine uses")[1]
    assert "blender | Blender" in section and "portafilter" not in section
    assert result.routines[0].issues == []


DIARY = """\
stray line before any date
12/10
espresso
- toast and coffee
* pasta with tomato sauce
13/10
espresso
fixed the thing
40/10
espresso
14/10:
mate
"""


def test_parse_diary_is_deterministic():
    parsed = parse_diary(DIARY)
    assert parsed.days == ["12/10", "13/10", "14/10"]
    assert [(e.id, e.date, e.text) for e in parsed.entries] == [
        ("e1", None, "stray line before any date"),
        ("e2", "12/10", "espresso"),
        ("e3", "12/10", "toast and coffee"),
        ("e4", "12/10", "pasta with tomato sauce"),
        ("e5", "13/10", "espresso"),
        ("e6", "13/10", "fixed the thing"),
        ("e7", None, "espresso"),
        ("e8", "14/10", "mate"),
    ]
    reasons = [(p["line"], p["reason"]) for p in parsed.problems]
    assert reasons == [(1, "no clear date"), (9, "not a valid DD/MM date"), (10, "no clear date")]


def test_diary_import_groups_entries_and_measures_frequencies(project):
    draft = DiaryDraft(
        activities=[
            DiaryActivity(existing_routine_id="espresso", entry_ids=["e2", "e3", "e5"]),
            DiaryActivity(existing_routine_id="toast_breakfast", entry_ids=["e3"]),
            DiaryActivity(existing_routine_id="pasta_with_tomato_sauce", entry_ids=["e4"]),
            DiaryActivity(new_routine=SMOOTHIE, entry_ids=["e8"]),
        ],
        unclear=[UnclearEntry(entry_id="e6", reason="cannot tell which activity this is")],
        missing_items=[],
    )
    client = FakeClient(draft)
    result = import_diary(project, DIARY, client)
    assert result.recorded_days == 3
    by_id = {a.routine_id: a for a in result.activities}
    # 3 occurrences over 3 recorded days -> 7 per week
    assert by_id["espresso"].occurrences == 3 and by_id["espresso"].frequency_per_week == 7.0
    assert by_id["espresso"].existing and by_id["espresso"].previous_frequency == 14
    assert by_id["espresso"].days == ["12/10", "13/10"]
    assert by_id["toast_breakfast"].frequency_per_week == pytest.approx(2.33)
    new = by_id["smoothie"]
    assert not new.existing and new.proposal is not None and new.proposal.issues == []
    assert new.proposal.routine["frequency_per_week"] == pytest.approx(2.33)
    texts = {u["text"]: u["reason"] for u in result.unclear}
    assert texts["fixed the thing"] == "cannot tell which activity this is"
    assert texts["stray line before any date"] == "no clear date"
    assert "40/10" in texts
    # Only dated entries are sent to the model.
    listing = client.calls[0]["prompt"].split("## Diary entries")[1]
    assert "e2 | 12/10 | espresso" in listing and "stray line" not in listing


def test_diary_entries_the_model_skips_are_reported(project):
    draft = DiaryDraft(
        activities=[DiaryActivity(existing_routine_id="espresso", entry_ids=["e2"])],
        unclear=[],
        missing_items=[],
    )
    result = import_diary(project, DIARY, FakeClient(draft))
    skipped = [u["text"] for u in result.unclear if u["reason"] == "not interpreted"]
    assert skipped == [
        "toast and coffee",
        "pasta with tomato sauce",
        "espresso",
        "fixed the thing",
        "mate",
    ]


def test_diary_without_dates_never_calls_the_model(project):
    client = FakeClient(None)
    result = import_diary(project, "espresso\ntoast\n", client)
    assert result.activities == [] and len(result.unclear) == 2 and client.calls == []


def test_sample_diary_covers_two_weeks():
    parsed = parse_diary((SAMPLE / "diary.txt").read_text())
    assert len(parsed.days) == 14
    reasons = {p["reason"] for p in parsed.problems}
    assert reasons == {"no clear date", "not a valid DD/MM date"}


def hard(**kw):
    base = {
        "items": [],
        "categories": [],
        "item_tags": [],
        "from_items": [],
        "from_categories": [],
        "rationale": "from a note",
    }
    return LlmHard(**{**base, **kw})


def test_constraints_from_comments_are_validated_and_never_applied(project):
    draft = ConstraintDraft(
        hard=[
            hard(id="wine_glasses_reachable", type="max_level", items=["wine_glass"], level=1),
            hard(
                id="mitts_far",
                type="min_distance",
                items=["oven_mitts"],
                poi="stove",
                distance_cm=60,
            ),
            hard(
                id="bleach_apart", type="separate", items=["bleach"], from_categories=["food_dry"]
            ),
            hard(id="bad_tag", type="require_tag", items=["kids_cups"], tag="eye_level"),
            hard(id="bad_item", type="max_level", items=["unicorn"], level=1),
            hard(id="heavy_items_low", type="max_level", item_tags=["heavy"], level=1),
            hard(id="no_level", type="max_level", items=["mug"]),
        ],
        soft=[
            LlmSoft(
                id="tea_things",
                items=["teapot", "tea_cup"],
                categories=[],
                mode="nearby",
                rationale="note",
            ),
            LlmSoft(id="empty", items=[], categories=[], mode="nearby", rationale="note"),
        ],
        ignored_comments=["slow_cooker: Almost never used"],
    )
    client = FakeClient(draft)
    before = project.constraints.model_dump()
    result = propose_constraints(project, client)

    assert [c["id"] for c in result.constraints["hard"]] == [
        "wine_glasses_reachable",
        "mitts_far",
        "bleach_apart",
    ]
    assert result.constraints["hard"][2]["from"] == {"categories": ["food_dry"]}
    assert [c["id"] for c in result.constraints["soft"]] == ["tea_things"]
    rejected = {r["constraint"]["id"]: r["problems"] for r in result.rejected}
    assert rejected["bad_tag"] == ["no compartment has tag 'eye_level'"]
    assert rejected["bad_item"] == ["unknown item 'unicorn'"]
    assert rejected["heavy_items_low"] == ["id 'heavy_items_low' is already used"]
    assert "level" in rejected["no_level"][0]
    assert "empty" in rejected
    assert result.yaml.startswith("# Proposed by") and "wine_glasses_reachable" in result.yaml
    assert result.ignored_comments == ["slow_cooker: Almost never used"]
    assert project.constraints.model_dump() == before  # nothing was applied

    prompt = client.calls[0]["prompt"]
    assert "chef_knife | knives | Must stay out of the low drawers" in prompt
    assert "child_reachable" in prompt and "heavy_items_low" in prompt


def test_the_app_reports_when_the_llm_is_unavailable(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    ok, reason = llm_status()
    assert not ok and "ANTHROPIC_API_KEY" in reason
    with pytest.raises(LlmUnavailableError):
        AnthropicClient()


def test_real_client_sends_a_structured_request(monkeypatch):
    """The SDK call itself, with the transport replaced."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("REACH_LLM_MODEL", "claude-test-model")
    client = AnthropicClient()
    seen = {}

    class Response:
        stop_reason = "end_turn"
        parsed_output = RoutineDraft(routines=[], missing_items=[], notes="ok")

    def parse(**kwargs):
        seen.update(kwargs)
        return Response()

    monkeypatch.setattr(client._client.messages, "parse", parse)
    out = client.complete(system="sys", prompt="hello", schema=RoutineDraft)
    assert out.notes == "ok"
    assert seen["model"] == "claude-test-model" and seen["output_format"] is RoutineDraft
    assert seen["system"] == "sys" and seen["messages"] == [{"role": "user", "content": "hello"}]

    Response.stop_reason = "refusal"
    with pytest.raises(LlmError, match="declined"):
        client.complete(system="sys", prompt="hello", schema=RoutineDraft)
