"""The HTTP API, including a real optimization job in a child process."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reach.api.app import app, state
from reach.llm.schemas import LlmRoutine, LlmStep, RoutineDraft

SAMPLE = Path(__file__).parents[2] / "data" / "sample"


@pytest.fixture()
def client():
    state.clear()
    state.llm_client_factory = None
    with TestClient(app) as c:
        yield c
    state.jobs.stop_all()
    state.clear()


@pytest.fixture()
def loaded(client):
    assert client.post("/api/project/sample").json()["ok"]
    return client


def wait(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_nothing_loaded(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/project").json()["loaded"] is False
    assert client.get("/api/evaluate").status_code == 409


def test_sample_project_payload(loaded):
    p = loaded.get("/api/project").json()
    assert p["loaded"] and p["ok"] and p["feasible"] and p["issues"] == []
    assert p["source"] == "sample" and p["has_current"] and not p["has_result"]
    layout = p["layout"]
    assert [w["id"] for w in layout["walls"]] == ["north", "east", "south"]
    assert len(layout["compartments"]) == 44
    n3 = next(c for c in layout["compartments"] if c["id"] == "n3_up_high")
    assert n3["level"] == 3 and n3["level_override"] and n3["placement"]["wall"] == "north"
    door = next(o for o in layout["obstructions"] if o["id"] == "entrance_door")
    assert "n1_base" in door["blocks"] and door["wall_range"]["to_cm"] == 60
    milk = next(i for i in p["items"] if i["id"] == "milk")
    assert milk["pinned"] == "fridge" and milk["pin_source"] == "file"
    assert milk["derived_frequency"] > 0
    assert len(p["routines"]) == 18 and p["routines"][0]["steps"][0] == {"take": "coffee_beans"}


def test_upload_reports_errors_with_locations(client):
    files = []
    for f in SAMPLE.iterdir():
        text = f.read_text()
        if f.name == "items.csv":
            text = text.replace(
                "dinner_plate,Dinner plates,tableware,1.8",
                "dinner_plate,Dinner plates,tableware,big",
            )
        files.append(("files", (f.name, text, "text/plain")))
    files.append(("files", ("notes.txt", "hello", "text/plain")))
    p = client.post("/api/project/upload", files=files).json()
    assert p["loaded"] and not p["ok"] and p["source"] == "upload"
    assert "notes.txt" in p["ignored_files"] and "diary.txt" in p["ignored_files"]
    issue = p["issues"][0]
    assert (issue["file"], issue["line"], issue["field"]) == ("items.csv", 2, "volume_l")
    assert client.get("/api/evaluate").status_code == 422
    assert (
        client.post(
            "/api/project/upload", files=[("files", ("x.txt", "x", "text/plain"))]
        ).status_code
        == 400
    )


def test_evaluate_current_and_greedy(loaded):
    greedy = loaded.get("/api/evaluate", params={"source": "greedy"}).json()
    current = loaded.get("/api/evaluate", params={"source": "current"}).json()
    assert greedy["violations"] == [] and len(current["violations"]) == 2
    assert current["evaluation"]["cost"] > greedy["evaluation"]["cost"]
    assert {r["id"] for r in greedy["evaluation"]["routines"]} >= {"espresso", "unload_dishwasher"}
    assert loaded.get("/api/evaluate", params={"source": "result"}).status_code == 404
    drawer = next(loc for loc in greedy["locations"] if loc["id"] == "n2_dr1")
    assert drawer["capacity_volume"] == pytest.approx(29 * 0.7)


def test_optimize_job_progress_result_and_export(loaded):
    job = loaded.post("/api/optimize", json={"seed": 1, "iterations": 60_000}).json()
    assert job["status"] == "running" and job["kind"] == "optimize"

    events = []
    with loaded.stream("GET", f"/api/jobs/{job['id']}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events[-1]["status"] == "done"
    assert any(e["progress"].get("best_cost") for e in events)

    result = loaded.get("/api/result").json()
    assert result["present"] and result["violations"] == [] and result["seed"] == 1
    assert result["cost"] < result["greedy_cost"]
    assert result["vs_current"]["cost_change"] < -0.3
    assert (
        result["vs_greedy"]["totals"]["time"]["after"]
        <= result["vs_greedy"]["totals"]["time"]["before"] * 1.05
    )
    assert len(result["current_violations"]) == 2
    assert loaded.get("/api/project").json()["has_result"]

    csv = loaded.get("/api/export/assignment.csv").text
    assert csv.startswith("item_id,location_id") and "dinner_plate," in csv

    trace = loaded.get("/api/trace", params={"routine": "espresso"}).json()
    assert trace["events"][0]["kind"] == "start" and trace["metrics"]["distance"] > 0
    assert loaded.get("/api/trace", params={"routine": "nope"}).status_code == 404
    with_best = loaded.get(f"/api/jobs/{job['id']}", params={"best": True}).json()
    assert "dinner_plate" in with_best["best_assignment"]


def test_stop_keeps_the_best_solution(loaded):
    job = loaded.post("/api/optimize", json={"iterations": 40_000_000}).json()
    deadline = time.time() + 30
    while time.time() < deadline:
        if loaded.get(f"/api/jobs/{job['id']}").json()["progress"].get("iteration", 0) > 0:
            break
        time.sleep(0.1)
    loaded.post(f"/api/jobs/{job['id']}/stop")
    assert wait(loaded, job["id"])["status"] == "stopped"
    result = loaded.get("/api/result").json()
    assert result["present"] and result["stopped_early"] and result["violations"] == []
    assert result["cost"] <= result["greedy_cost"]


def test_pin_unpin_and_reoptimize(loaded):
    job = loaded.post("/api/optimize", json={"iterations": 20_000}).json()
    wait(loaded, job["id"])
    before = loaded.get("/api/result").json()["assignment"]
    target = "pantry_6" if before["dinner_plate"] != "pantry_6" else "pantry_5"

    # Pinning an item where it already is keeps the result; moving it elsewhere drops it.
    p = loaded.post("/api/pins", json={"item": "mug", "location": before["mug"]}).json()
    assert p["has_result"]
    p = loaded.post("/api/pins", json={"item": "dinner_plate", "location": target}).json()
    current = loaded.get("/api/evaluate", params={"source": "current"}).json()
    assert {v["kind"] for v in current["violations"]} == {"constraint"}  # pins are not rules
    plate = next(i for i in p["items"] if i["id"] == "dinner_plate")
    assert plate["pinned"] == target and plate["pin_source"] == "ui" and not p["has_result"]

    job = loaded.post("/api/optimize", json={"iterations": 20_000}).json()
    wait(loaded, job["id"])
    after = loaded.get("/api/result").json()
    assert after["assignment"]["dinner_plate"] == target and after["violations"] == []

    # A pin from items.csv can be lifted in the UI, and everything can be reset.
    p = loaded.post("/api/pins", json={"item": "toaster", "location": None}).json()
    assert next(i for i in p["items"] if i["id"] == "toaster")["pinned"] is None
    p = loaded.delete("/api/pins").json()
    items = {i["id"]: i for i in p["items"]}
    assert items["toaster"]["pinned"] == "counter_east" and items["dinner_plate"]["pinned"] is None
    assert loaded.post("/api/pins", json={"item": "nope", "location": None}).status_code == 404


def test_sensitivity_job(loaded):
    request = {"runs": 2, "iterations": 3_000, "workers": 1, "seed": 2}
    job = loaded.post("/api/sensitivity", json=request).json()
    final = wait(loaded, job["id"])
    assert final["status"] == "done" and final["progress"]["done"] == 2
    report = loaded.get("/api/sensitivity").json()
    assert report["present"] and report["runs"] == 2 and len(report["costs"]) == 2
    assert sum(report["counts"].values()) == len(report["items"])
    assert {"name", "label", "baseline_share", "modal_location"} <= set(report["items"][0])


def test_coverage_endpoint(loaded):
    cov = loaded.get("/api/coverage").json()
    assert cov["total"] == 197 and cov["unused_items"][0].keys() == {"id", "name", "category"}


def test_infeasible_project_explains_itself(client):
    files = []
    for f in SAMPLE.iterdir():
        text = f.read_text()
        if f.name == "constraints.yaml":
            text = text.replace(
                "tag: child_reachable\n    note: The kids", "tag: nowhere\n    note: The kids"
            )
        files.append(("files", (f.name, text, "text/plain")))
    p = client.post("/api/project/upload", files=files).json()
    assert p["ok"] and not p["feasible"]
    assert any("kids_tableware_reachable" in line for line in p["infeasible"])
    response = client.post("/api/optimize", json={})
    assert response.status_code == 422 and response.json()["detail"]["reasons"]


ROUTINE_YAML = """\
- id: glass_of_water
  name: Glass of water
  frequency_per_week: 20
  start: entrance
  steps:
    - take: water_glass
    - use: water_glass at main_sink
    - put_down: water_glass at table
"""


def test_routine_editor_validate_save_and_delete(loaded):
    ok = loaded.post("/api/routines/validate", json={"yaml": ROUTINE_YAML}).json()
    assert ok["ok"] and ok["routine"]["id"] == "glass_of_water"
    bad = loaded.post(
        "/api/routines/validate",
        json={"yaml": ROUTINE_YAML.replace("water_glass at main_sink", "goblet at main_sink")},
    ).json()
    assert not bad["ok"] and bad["issues"] == ["steps[1]: unknown item 'goblet'"]
    assert loaded.post("/api/routines/validate", json={"yaml": "- [broken"}).status_code == 422

    p = loaded.post("/api/routines", json={"yaml": ROUTINE_YAML}).json()
    assert len(p["routines"]) == 19 and p["ok"]
    rejected = loaded.post(
        "/api/routines", json={"yaml": ROUTINE_YAML.replace("take: water_glass", "take: goblet")}
    )
    assert rejected.status_code == 422
    # Saving again with the same id replaces it.
    p = loaded.post("/api/routines", json={"yaml": ROUTINE_YAML.replace("20", "3")}).json()
    saved = next(r for r in p["routines"] if r["id"] == "glass_of_water")
    assert len(p["routines"]) == 19 and saved["frequency_per_week"] == 3
    assert "glass_of_water" in loaded.get("/api/files/routines.yaml").text

    p = loaded.post("/api/routines/frequencies", json={"frequencies": {"espresso": 21}}).json()
    assert next(r for r in p["routines"] if r["id"] == "espresso")["frequency_per_week"] == 21
    assert (
        loaded.post("/api/routines/frequencies", json={"frequencies": {"nope": 1}}).status_code
        == 404
    )

    p = loaded.delete("/api/routines/glass_of_water").json()
    assert len(p["routines"]) == 18
    assert loaded.delete("/api/routines/glass_of_water").status_code == 404


def test_approved_constraints_are_appended(loaded):
    new = {"id": "mugs_handy", "type": "max_level", "select": {"items": ["mug"]}, "level": 1}
    p = loaded.post("/api/constraints", json={"hard": [new]}).json()
    assert p["ok"] and [c["id"] for c in p["constraints"]["hard"]][-1] == "mugs_handy"
    assert len(p["constraints"]["hard"]) == 12 and len(p["constraints"]["soft"]) == 4
    separate = next(c for c in p["constraints"]["hard"] if c["type"] == "separate")
    assert "from" in separate
    bad = {"id": "x", "type": "max_level", "select": {"items": ["unicorn"]}, "level": 1}
    assert loaded.post("/api/constraints", json={"hard": [bad]}).status_code == 422


def test_llm_endpoints_without_a_key(loaded, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    assert loaded.get("/api/project").json()["llm"]["available"] is False
    response = loaded.post("/api/llm/routine", json={"text": "make tea"})
    assert response.status_code == 503 and "unavailable" in response.json()["detail"]
    # The deterministic half of the diary import still works.
    parsed = loaded.post("/api/diary/parse", json={"text": "12/10\nespresso\n"}).json()
    assert parsed["days"] == ["12/10"] and parsed["entries"][0]["text"] == "espresso"


def test_llm_proposal_goes_through_review(loaded):
    routine = LlmRoutine(
        id="glass_of_water",
        name="Glass of water",
        frequency_per_week=20,
        start="entrance",
        steps=[
            LlmStep(kind="take", item="water_glass"),
            LlmStep(kind="use", item="water_glass", at="main_sink"),
            LlmStep(kind="put_down", item="water_glass", at="table"),
        ],
    )

    class Fake:
        def complete(self, *, system, prompt, schema):
            return RoutineDraft(routines=[routine], missing_items=[], notes="n")

    state.llm_client_factory = Fake
    assert loaded.get("/api/project").json()["llm"]["available"] is True
    proposal = loaded.post("/api/llm/routine", json={"text": "I drink water a lot"}).json()
    assert proposal["routines"][0]["issues"] == [] and proposal["notes"] == "n"
    # Proposing changes nothing; only the explicit save does.
    assert len(loaded.get("/api/project").json()["routines"]) == 18
    saved = loaded.post(
        "/api/routines", json={"routine": proposal["routines"][0]["routine"]}
    ).json()
    assert len(saved["routines"]) == 19
