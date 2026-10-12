"""Background jobs in a child process, with progress, cancellation and a result."""

from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from reach.core.model import Assignment, Problem

_CTX = mp.get_context("spawn")


def optimize_worker(
    problem: Problem,
    params: dict[str, Any],
    normalizers: dict[str, float],
    initial: Assignment | None,
    out: Any,
    cancel: Any,
) -> None:
    """Runs in the child process."""
    try:
        from reach.core.optim import SimulatedAnnealing

        def progress(e) -> None:
            out.put(
                (
                    "progress",
                    {
                        "iteration": e.iteration,
                        "total_iterations": e.total_iterations,
                        "elapsed_s": e.elapsed_s,
                        "fraction": e.fraction,
                        "temperature": e.temperature,
                        "current_cost": e.current_cost,
                        "best_cost": e.best_cost,
                        "initial_cost": e.initial_cost,
                        "best_assignment": e.best_assignment,
                    },
                )
            )

        result = SimulatedAnnealing(
            seed=params.get("seed", 0),
            iterations=params.get("iterations", 200_000),
            time_limit_s=params.get("time_limit_s"),
            restarts=params.get("restarts", 1),
        ).optimize(
            problem, initial=initial, normalizers=normalizers, progress=progress, cancel=cancel
        )
        out.put(
            (
                "done",
                {
                    "assignment": result.assignment,
                    "cost": result.cost,
                    "initial_cost": result.initial_cost,
                    "iterations": result.iterations,
                    "elapsed_s": result.elapsed_s,
                    "stopped_early": result.stopped_early,
                    "seed": result.seed,
                    "history": result.history,
                },
            )
        )
    except Exception as exc:  # reported to the parent, which owns the user-facing error
        out.put(("error", f"{type(exc).__name__}: {exc}"))


def sensitivity_worker(
    problem: Problem,
    params: dict[str, Any],
    normalizers: dict[str, float],
    baseline: Assignment | None,
    out: Any,
    cancel: Any,
) -> None:
    """Runs in the child process, which in turn uses a process pool for the runs."""
    try:
        from reach.core.sensitivity import run_sensitivity
        from reach.summary import sensitivity_dict

        report = run_sensitivity(
            problem,
            runs=params.get("runs", 20),
            noise_runs=params.get("noise_runs"),
            perturbation=params.get("perturbation", 0.3),
            seed=params.get("seed", 0),
            iterations=params.get("iterations", 200_000),
            restarts=params.get("restarts", 1),
            workers=params.get("workers"),
            baseline=baseline,
            normalizers=normalizers,
            progress=lambda done, total: out.put(
                ("progress", {"done": done, "total": total, "fraction": done / max(total, 1)})
            ),
            cancel=cancel,
        )
        out.put(("done", sensitivity_dict(report, problem)))
    except Exception as exc:
        out.put(("error", f"{type(exc).__name__}: {exc}"))


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    status: str = "running"
    """``running``, ``done``, ``stopped`` or ``error``."""
    progress: dict[str, Any] = field(default_factory=dict)
    history: list[tuple[int, float]] = field(default_factory=list)
    best_assignment: Assignment | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    version: int = 0
    started: float = field(default_factory=time.time)
    _cancel: Any = None
    _process: Any = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": {k: v for k, v in self.progress.items() if k != "best_assignment"},
            "history": self.history[-400:],
            "error": self.error,
            "version": self.version,
        }


class JobManager:
    """Starts jobs and keeps their latest state. One job of each kind runs at a time."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def running(self, kind: str) -> Job | None:
        return next(
            (j for j in self._jobs.values() if j.kind == kind and j.status == "running"), None
        )

    def start(
        self,
        kind: str,
        target: Callable[..., None],
        args: tuple,
        on_done: Callable[[Job], None] | None = None,
    ) -> Job:
        with self._lock:
            existing = self.running(kind)
            if existing is not None:
                self.stop(existing.id)
            job = Job(uuid.uuid4().hex[:12], kind)
            job._cancel = _CTX.Event()
            out = _CTX.Queue()
            job._process = _CTX.Process(target=target, args=(*args, out, job._cancel))
            job._process.start()
            self._jobs[job.id] = job
        threading.Thread(target=self._pump, args=(job, out, on_done), daemon=True).start()
        return job

    def stop(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status != "running":
            return False
        job._cancel.set()
        return True

    def stop_all(self) -> None:
        for job in list(self._jobs.values()):
            if job.status == "running":
                job._cancel.set()
                if job._process is not None:
                    job._process.join(timeout=5)
                    if job._process.is_alive():
                        job._process.terminate()

    def _pump(self, job: Job, out: Any, on_done: Callable[[Job], None] | None) -> None:
        """Drain the child's queue into the job until it finishes."""
        process = job._process
        while True:
            try:
                kind, payload = out.get(timeout=0.2)
            except queue.Empty:
                if not process.is_alive():
                    job.status, job.error = "error", "the worker process exited unexpectedly"
                    break
                continue
            if kind == "progress":
                best = payload.get("best_assignment")
                if best is not None:
                    job.best_assignment = best
                job.progress = payload
                if "best_cost" in payload:
                    job.history.append((payload["iteration"], payload["best_cost"]))
                job.version += 1
            elif kind == "done":
                job.result = payload
                # Publish the result before the status flips, so a client that sees "done" can
                # read it straight away.
                if on_done is not None:
                    on_done(job)
                stopped = job._cancel.is_set() or bool(payload.get("stopped_early"))
                job.status = "stopped" if stopped else "done"
                break
            else:
                job.status, job.error = "error", str(payload)
                break
        job.version += 1
        process.join(timeout=5)
