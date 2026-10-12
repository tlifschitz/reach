"""FastAPI application: one in-memory project, background jobs, and the built frontend."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from reach import __version__
from reach.api.jobs import Job, JobManager, optimize_worker, sensitivity_worker
from reach.core.compile import Compiled
from reach.core.constraints import check_assignment
from reach.core.coverage import analyze_coverage
from reach.core.model import Assignment, Problem
from reach.core.optim import InfeasibleError, greedy_assignment
from reach.io.export import assignment_csv, dump_yaml, routines_yaml
from reach.io.project import (
    CONSTRAINTS_FILE,
    KNOWN_FILES,
    ROUTINES_FILE,
    Project,
    load_project_from_texts,
    read_project_files,
)
from reach.io.schemas import ConstraintsFile, RoutineModel
from reach.summary import (
    assignment_report,
    baseline_normalizers,
    comparison,
    coverage_dict,
    routine_trace,
    sample_dir,
)


class State:
    """Everything the server remembers. A single user, a single project."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.jobs = JobManager()
        self.llm_client_factory: Callable[[], Any] | None = None
        self.clear()

    def clear(self) -> None:
        self.texts: dict[str, str] = {}
        self.source: str | None = None
        self.extra_pins: dict[str, str | None] = {}
        self.project: Project | None = None
        self.compiled: Compiled | None = None
        self.normalizers: dict[str, float] | None = None
        self.greedy: Assignment | None = None
        self.infeasible: list[str] = []
        self.result: dict[str, Any] | None = None
        self.sensitivity: dict[str, Any] | None = None

    def load(self, texts: dict[str, str], source: str | None = None, *, keep_pins: bool = False):
        with self.lock:
            previous = self.result
            if not keep_pins:
                self.extra_pins = {}
            self.texts = dict(texts)
            if source is not None:
                self.source = source
            self.project = load_project_from_texts(self.texts, self.extra_pins)
            self.compiled = self.normalizers = self.greedy = None
            self.infeasible = []
            self.result = self.sensitivity = None
            problem = self.project.problem
            if problem is not None:
                self.compiled = Compiled(problem)
                try:
                    self.greedy = greedy_assignment(problem, self.compiled)
                    self.normalizers = baseline_normalizers(problem, self.compiled)
                except InfeasibleError as exc:
                    self.infeasible = exc.report.lines()
                # Keep the previous result when it is still valid for the edited project.
                if (
                    keep_pins
                    and previous is not None
                    and self.greedy is not None
                    and not check_assignment(problem, previous["assignment"], self.compiled)
                ):
                    self.result = {**previous, "stale": True}

    def problem(self) -> Problem:
        if self.project is None:
            raise HTTPException(409, "No project is loaded.")
        if self.project.problem is None:
            raise HTTPException(422, "The project has validation errors.")
        return self.project.problem

    def ready(self) -> tuple[Problem, Compiled, dict[str, float], Assignment]:
        problem = self.problem()
        if self.greedy is None or self.normalizers is None or self.compiled is None:
            raise HTTPException(
                422, {"message": "No feasible assignment exists.", "reasons": self.infeasible}
            )
        return problem, self.compiled, self.normalizers, self.greedy

    def assignment(self, source: str) -> Assignment:
        _, _, _, greedy = self.ready()
        if source == "greedy":
            return greedy
        if source == "current":
            current = self.project.current_assignment if self.project else None
            if current is None:
                raise HTTPException(404, "The project has no usable current_assignment.csv.")
            return current
        if source == "result":
            if self.result is None:
                raise HTTPException(404, "Run the optimization first.")
            return self.result["assignment"]
        raise HTTPException(400, f"Unknown assignment source '{source}'.")


state = State()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    state.jobs.stop_all()


app = FastAPI(title="Reach", version=__version__, lifespan=lifespan)


# --- project --------------------------------------------------------------------------------


def _llm_info() -> dict[str, Any]:
    if state.llm_client_factory is not None:
        return {"available": True, "reason": ""}
    from reach.llm import llm_status

    ok, reason = llm_status()
    return {"available": ok, "reason": reason}


def _project_payload() -> dict[str, Any]:
    project = state.project
    if project is None:
        return {"loaded": False, "llm": _llm_info(), "version": __version__}
    problem = project.problem
    file_pins = {row.id: row.fixed_location for row in project.items if row.fixed_location}
    pins = dict(problem.pins) if problem is not None else file_pins
    derived = problem.derived_frequencies() if problem is not None else {}
    return {
        "loaded": True,
        "version": __version__,
        "source": state.source,
        "ok": project.ok,
        "feasible": state.greedy is not None,
        "infeasible": state.infeasible,
        "issues": [asdict(i) for i in project.report.sorted()],
        "files": sorted(state.texts),
        "layout": project.layout(),
        "items": [
            {
                "id": row.id,
                "name": row.name or row.id,
                "category": row.category,
                "volume_l": row.volume_l,
                "quantity": row.quantity,
                "weight_kg": row.weight_kg,
                "estimated_frequency": row.estimated_frequency_per_week,
                "derived_frequency": derived.get(row.id, 0.0),
                "nest_group": row.nest_group,
                "tags": row.tags,
                "comments": row.comments,
                "pinned": pins.get(row.id),
                "pin_source": (
                    None
                    if row.id not in pins
                    else "file"
                    if file_pins.get(row.id) == pins[row.id] and row.id not in state.extra_pins
                    else "ui"
                ),
            }
            for row in project.items
        ],
        "routines": [
            {**r.to_yaml(), "step_count": len(r.steps)}
            for r in (project.routines.routines if project.routines else [])
        ],
        "constraints": (project.constraints or ConstraintsFile()).model_dump(by_alias=True),
        "has_current": project.current_assignment is not None,
        "has_result": state.result is not None,
        "llm": _llm_info(),
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "llm": _llm_info()}


@app.get("/api/project")
def get_project() -> dict[str, Any]:
    return _project_payload()


@app.post("/api/project/sample")
def load_sample() -> dict[str, Any]:
    state.load(read_project_files(sample_dir()), "sample")
    return _project_payload()


@app.post("/api/project/upload")
async def upload_project(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    """Load a project from uploaded files. Files are recognised by name."""
    texts: dict[str, str] = {}
    unknown: list[str] = []
    for upload in files:
        name = Path(upload.filename or "").name
        if name not in KNOWN_FILES:
            unknown.append(name)
            continue
        try:
            texts[name] = (await upload.read()).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(400, f"{name} is not UTF-8 text.") from exc
    if not texts:
        raise HTTPException(400, f"No recognised files. Expected names: {', '.join(KNOWN_FILES)}.")
    state.load(texts, "upload")
    payload = _project_payload()
    payload["ignored_files"] = unknown
    return payload


@app.delete("/api/project")
def close_project() -> dict[str, Any]:
    state.jobs.stop_all()
    state.clear()
    return _project_payload()


@app.get("/api/files/{name}")
def get_file(name: str) -> PlainTextResponse:
    if name not in state.texts:
        raise HTTPException(404, f"No file named {name}.")
    return PlainTextResponse(
        state.texts[name], headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


# --- evaluation -----------------------------------------------------------------------------

Source = Literal["greedy", "current", "result"]


@app.get("/api/evaluate")
def evaluate_assignment(source: Source = "greedy") -> dict[str, Any]:
    problem, compiled, normalizers, _ = state.ready()
    report = assignment_report(problem, state.assignment(source), normalizers, compiled)
    report["source"] = source
    return report


@app.get("/api/trace")
def trace(routine: str, source: Source = "result") -> dict[str, Any]:
    problem, compiled, _, _ = state.ready()
    try:
        return routine_trace(problem, state.assignment(source), routine, compiled)
    except KeyError as exc:
        raise HTTPException(404, f"No routine named {routine}.") from exc


@app.get("/api/coverage")
def coverage() -> dict[str, Any]:
    problem = state.problem()
    return coverage_dict(analyze_coverage(problem), problem)


@app.get("/api/export/assignment.csv")
def export_assignment(source: Source = "result") -> PlainTextResponse:
    problem = state.problem()
    return PlainTextResponse(
        assignment_csv(problem, state.assignment(source)),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="assignment_{source}.csv"'},
    )


# --- optimization ---------------------------------------------------------------------------


class OptimizeRequest(BaseModel):
    seed: int = 0
    iterations: int = Field(default=200_000, ge=0, le=50_000_000)
    time_limit_s: float | None = Field(default=None, gt=0, le=3600)
    start: Literal["greedy", "result"] = "greedy"


def _store_result(job: Job) -> None:
    payload = job.result or {}
    with state.lock:
        state.result = {
            "assignment": payload["assignment"],
            "cost": payload["cost"],
            "greedy_cost": payload["initial_cost"],
            "iterations": payload["iterations"],
            "elapsed_s": payload["elapsed_s"],
            "stopped_early": payload["stopped_early"],
            "seed": payload["seed"],
            "history": payload["history"],
            "stale": False,
        }
        state.sensitivity = None


@app.post("/api/optimize")
def start_optimize(request: OptimizeRequest) -> dict[str, Any]:
    problem, _, normalizers, _ = state.ready()
    initial = state.result["assignment"] if request.start == "result" and state.result else None
    job = state.jobs.start(
        "optimize",
        optimize_worker,
        (problem, request.model_dump(), normalizers, initial),
        _store_result,
    )
    return job.snapshot()


def _job(job_id: str) -> Job:
    job = state.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job.")
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, best: bool = False) -> dict[str, Any]:
    job = _job(job_id)
    snapshot = job.snapshot()
    if best and job.best_assignment is not None and job.kind == "optimize":
        snapshot["best_assignment"] = job.best_assignment
    return snapshot


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict[str, Any]:
    job = _job(job_id)
    state.jobs.stop(job_id)
    return job.snapshot()


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """Server-Sent Events: one message per progress update, then a final one."""
    job = _job(job_id)

    async def stream():
        seen = -1
        while True:
            if job.version != seen:
                seen = job.version
                yield f"data: {json.dumps(job.snapshot())}\n\n"
            if job.status != "running" and job.version == seen:
                break
            await asyncio.sleep(0.15)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/result")
def get_result() -> dict[str, Any]:
    problem, compiled, normalizers, greedy = state.ready()
    if state.result is None:
        return {"present": False}
    meta = {k: v for k, v in state.result.items() if k != "assignment"}
    report = assignment_report(problem, state.result["assignment"], normalizers, compiled)
    out: dict[str, Any] = {"present": True, **meta, **report}
    out["vs_greedy"] = comparison(assignment_report(problem, greedy, normalizers, compiled), report)
    current = state.project.current_assignment if state.project else None
    if current is not None:
        current_report = assignment_report(problem, current, normalizers, compiled)
        out["vs_current"] = comparison(current_report, report)
        out["current_violations"] = current_report["violations"]
    return out


class PinRequest(BaseModel):
    item: str
    location: str | None = None
    """``None`` removes the pin, including one that comes from ``items.csv``."""


@app.post("/api/pins")
def set_pin(request: PinRequest) -> dict[str, Any]:
    problem = state.problem()
    if request.item not in problem.items:
        raise HTTPException(404, f"No item named {request.item}.")
    if request.location is not None and request.location not in problem.locations:
        raise HTTPException(404, f"No location named {request.location}.")
    with state.lock:
        state.extra_pins[request.item] = request.location
        state.load(state.texts, keep_pins=True)
    return _project_payload()


@app.delete("/api/pins")
def reset_pins() -> dict[str, Any]:
    """Drop every pin made in the UI and go back to the pins in ``items.csv``."""
    with state.lock:
        state.extra_pins = {}
        state.load(state.texts, keep_pins=True)
    return _project_payload()


# --- sensitivity ----------------------------------------------------------------------------


class SensitivityRequest(BaseModel):
    runs: int = Field(default=20, ge=1, le=500)
    perturbation: float = Field(default=0.3, gt=0, lt=1)
    seed: int = 0
    iterations: int = Field(default=50_000, ge=0, le=10_000_000)
    workers: int | None = Field(default=None, ge=1)


@app.post("/api/sensitivity")
def start_sensitivity(request: SensitivityRequest) -> dict[str, Any]:
    problem, _, normalizers, _ = state.ready()
    baseline = state.result["assignment"] if state.result else None

    def store(job: Job) -> None:
        with state.lock:
            state.sensitivity = job.result

    job = state.jobs.start(
        "sensitivity",
        sensitivity_worker,
        (problem, request.model_dump(), normalizers, baseline),
        store,
    )
    return job.snapshot()


@app.get("/api/sensitivity")
def get_sensitivity() -> dict[str, Any]:
    if state.sensitivity is None:
        return {"present": False}
    return {"present": True, **state.sensitivity}


# --- editing routines and constraints -------------------------------------------------------


class RoutineRequest(BaseModel):
    routine: dict[str, Any] | None = None
    yaml: str | None = None


def _routine_data(request: RoutineRequest) -> dict[str, Any]:
    if request.routine is not None:
        return request.routine
    try:
        data = yaml.safe_load(request.yaml or "")
    except yaml.YAMLError as exc:
        raise HTTPException(422, f"Invalid YAML: {exc}") from exc
    if isinstance(data, dict) and "routines" in data:
        data = data["routines"]
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        raise HTTPException(422, "Expected a single routine.")
    return data


@app.post("/api/routines/validate")
def validate_one_routine(request: RoutineRequest) -> dict[str, Any]:
    from reach.llm.proposals import validate_routine

    if state.project is None:
        raise HTTPException(409, "No project is loaded.")
    state.problem()
    model, issues = validate_routine(state.project, _routine_data(request))
    return {
        "ok": model is not None and not issues,
        "issues": issues,
        "routine": model.to_yaml() if model else None,
        "yaml": dump_yaml([model.to_yaml()]) if model else None,
    }


def _save_routines(routines: list[RoutineModel]) -> dict[str, Any]:
    texts = dict(state.texts)
    texts[ROUTINES_FILE] = routines_yaml(routines)
    state.load(texts, keep_pins=True)
    return _project_payload()


@app.post("/api/routines")
def save_routine(request: RoutineRequest) -> dict[str, Any]:
    """Add a routine, or replace the one with the same id. This is the approve step."""
    from reach.llm.proposals import validate_routine

    project = state.project
    if project is None or project.routines is None:
        raise HTTPException(409, "No project is loaded.")
    state.problem()
    model, issues = validate_routine(project, _routine_data(request))
    blocking = [i for i in issues if "unknown" in i] if model is not None else issues
    if model is None or blocking:
        raise HTTPException(422, {"message": "The routine is not valid.", "issues": issues})
    routines = [r for r in project.routines.routines if r.id != model.id] + [model]
    return _save_routines(routines)


@app.delete("/api/routines/{routine_id}")
def delete_routine(routine_id: str) -> dict[str, Any]:
    project = state.project
    if project is None or project.routines is None:
        raise HTTPException(409, "No project is loaded.")
    routines = [r for r in project.routines.routines if r.id != routine_id]
    if len(routines) == len(project.routines.routines):
        raise HTTPException(404, f"No routine named {routine_id}.")
    return _save_routines(routines)


class FrequenciesRequest(BaseModel):
    frequencies: dict[str, float]


@app.post("/api/routines/frequencies")
def set_frequencies(request: FrequenciesRequest) -> dict[str, Any]:
    project = state.project
    if project is None or project.routines is None:
        raise HTTPException(409, "No project is loaded.")
    known = {r.id for r in project.routines.routines}
    unknown = sorted(set(request.frequencies) - known)
    if unknown:
        raise HTTPException(404, f"Unknown routines: {', '.join(unknown)}.")
    if any(f < 0 for f in request.frequencies.values()):
        raise HTTPException(422, "Frequencies cannot be negative.")
    routines = [
        r.model_copy(
            update={"frequency_per_week": request.frequencies.get(r.id, r.frequency_per_week)}
        )
        for r in project.routines.routines
    ]
    return _save_routines(routines)


class ConstraintsRequest(BaseModel):
    hard: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []


@app.post("/api/constraints")
def add_constraints(request: ConstraintsRequest) -> dict[str, Any]:
    """Append approved constraints to the project."""
    project = state.project
    if project is None:
        raise HTTPException(409, "No project is loaded.")
    current = (project.constraints or ConstraintsFile()).model_dump(by_alias=True)
    for c in current["hard"] + current["soft"]:
        if not c.get("note"):
            c.pop("note", None)
    merged = {"hard": current["hard"] + request.hard, "soft": current["soft"] + request.soft}
    texts = dict(state.texts)
    texts[CONSTRAINTS_FILE] = dump_yaml(merged)
    candidate = load_project_from_texts(texts, state.extra_pins)
    errors = [str(i) for i in candidate.report.errors if i.file == CONSTRAINTS_FILE]
    if errors:
        raise HTTPException(422, {"message": "The constraints are not valid.", "issues": errors})
    state.load(texts, keep_pins=True)
    return _project_payload()


# --- LLM proposals --------------------------------------------------------------------------


def _llm_client():
    from reach.llm import LlmUnavailableError, default_client

    if state.llm_client_factory is not None:
        return state.llm_client_factory()
    try:
        return default_client()
    except LlmUnavailableError as exc:
        raise HTTPException(503, f"LLM features are unavailable: {exc}.") from exc


def _llm(fn, *args, **kwargs):
    from reach.llm import LlmError, LlmUnavailableError

    if state.project is None:
        raise HTTPException(409, "No project is loaded.")
    state.problem()
    client = _llm_client()
    try:
        return asdict(fn(state.project, *args, client, **kwargs))
    except LlmUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(502, str(exc)) from exc


class RoutineFromText(BaseModel):
    text: str = ""
    url: str | None = None


@app.post("/api/llm/routine")
def llm_routine(request: RoutineFromText) -> dict[str, Any]:
    from reach.llm import propose_routines

    return _llm(propose_routines, request.text, url=request.url or None)


@app.post("/api/llm/cover")
def llm_cover() -> dict[str, Any]:
    from reach.llm import propose_coverage_routines

    return _llm(propose_coverage_routines)


class DiaryRequest(BaseModel):
    text: str


@app.post("/api/llm/diary")
def llm_diary(request: DiaryRequest) -> dict[str, Any]:
    from reach.llm import import_diary

    return _llm(import_diary, request.text)


@app.post("/api/llm/constraints")
def llm_constraints() -> dict[str, Any]:
    from reach.llm import propose_constraints

    return _llm(propose_constraints)


@app.post("/api/diary/parse")
def diary_parse(request: DiaryRequest) -> dict[str, Any]:
    """The deterministic part of the diary import. Works without an API key."""
    from reach.llm import parse_diary

    return asdict(parse_diary(request.text))


# --- the built frontend ---------------------------------------------------------------------


def _frontend_dir() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (here.parents[3] / "frontend" / "dist", here.parents[1] / "_web"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


_dist = _frontend_dir()
if _dist is not None:
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        target = (_dist / path).resolve()
        if path and target.is_file() and _dist in target.parents:
            return FileResponse(target)
        return FileResponse(_dist / "index.html")
