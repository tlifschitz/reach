"""Command line interface: validate, simulate, optimize, analyse, without the web frontend."""

from __future__ import annotations

import json
import signal
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from reach.core.compile import Compiled
from reach.core.constraints import check_assignment
from reach.core.coverage import analyze_coverage
from reach.core.model import METRICS, Assignment, Problem
from reach.core.optim import InfeasibleError, SimulatedAnnealing, greedy_assignment
from reach.core.sensitivity import run_sensitivity
from reach.io.csv_loader import load_assignment
from reach.io.errors import ValidationReport
from reach.io.export import assignment_csv
from reach.io.project import Project, load_project
from reach.summary import (
    DEFAULT_ITERATIONS,
    DEFAULT_RESTARTS,
    METRIC_LABELS,
    METRIC_SCALE,
    assignment_report,
    baseline_normalizers,
    comparison,
    coverage_dict,
    sample_dir,
    sensitivity_dict,
)

app = typer.Typer(
    help="Reach: optimize where things live.", no_args_is_help=True, add_completion=False
)
llm_app = typer.Typer(help="Optional LLM helpers. They write proposals for you to review.")
app.add_typer(llm_app, name="llm")
console = Console()
err = Console(stderr=True)

ProjectDir = Annotated[
    Path | None,
    typer.Argument(help="Folder with the input files. Defaults to the bundled sample kitchen."),
]
JsonFlag = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]


def _load(path: Path | None, *, quiet: bool = False) -> Project:
    project = load_project(path or sample_dir())
    if not quiet or not project.ok:
        _print_issues(project.report)
    if not project.ok:
        raise typer.Exit(1)
    return project


def _print_issues(report: ValidationReport) -> None:
    for issue in report.sorted():
        style = "red" if issue.severity == "error" else "yellow"
        err.print(f"[{style}]{issue}[/{style}]", highlight=False)


def _fmt(metric: str, value: float) -> str:
    return f"{value * METRIC_SCALE.get(metric, 1.0):,.1f}"


def _feasible_start(problem: Problem) -> Assignment:
    try:
        return greedy_assignment(problem)
    except InfeasibleError as exc:
        err.print("[red]No feasible assignment exists.[/red]")
        for line in exc.report.lines():
            err.print(f"  - {line}", highlight=False)
        raise typer.Exit(2) from exc


def _read_assignment(problem: Problem, path: Path) -> Assignment:
    report = ValidationReport()
    assignment, _ = load_assignment(path.read_text(encoding="utf-8"), path.name, report)
    if not report.ok:
        _print_issues(report)
        raise typer.Exit(1)
    for item_id, loc_id in problem.pins.items():
        assignment.setdefault(item_id, loc_id)
    return assignment


def _metrics_table(title: str, report: dict[str, Any]) -> Table:
    ev = report["evaluation"]
    table = Table(title=title, title_justify="left")
    table.add_column("Routine")
    table.add_column("per week", justify="right")
    for m in METRICS:
        table.add_column(METRIC_LABELS[m], justify="right")
    table.add_column("Cost", justify="right")
    for r in ev["routines"]:
        table.add_row(
            r["name"],
            f"{r['frequency']:g}",
            *[_fmt(m, r["weekly"][m]) for m in METRICS],
            f"{r['cost']:.3f}",
        )
    occ = ev["occasional"]
    if occ["items"]:
        table.add_row(
            f"Occasional items ({occ['items']})",
            "",
            *[_fmt(m, occ["weekly"][m]) for m in METRICS],
            f"{occ['cost']:.3f}",
        )
    table.add_section()
    soft = f" (+{ev['soft_total']:.3f} soft)" if ev["soft_total"] else ""
    table.add_row(
        "Weekly total", "", *[_fmt(m, ev["totals"][m]) for m in METRICS], f"{ev['cost']:.3f}{soft}"
    )
    return table


def _locations_table(report: dict[str, Any]) -> Table:
    table = Table(title="Where things go", title_justify="left")
    table.add_column("Location")
    table.add_column("Level", justify="right")
    table.add_column("Fill", justify="right")
    table.add_column("Items")
    for loc in report["locations"]:
        if not loc["items"]:
            continue
        fill = f"{loc['fill']:.0%}" if loc["fill"] is not None else "-"
        table.add_row(
            loc["name"], str(loc["level"]), fill, ", ".join(i["name"] for i in loc["items"])
        )
    return table


def _print_comparison(label: str, cmp: dict[str, Any]) -> None:
    table = Table(title=f"Compared with {label}", title_justify="left")
    table.add_column("Metric")
    table.add_column(label.capitalize(), justify="right")
    table.add_column("This", justify="right")
    table.add_column("Change", justify="right")
    for m in METRICS:
        t = cmp["totals"][m]
        change = "-" if t["change"] is None else f"{t['change']:+.0%}"
        table.add_row(METRIC_LABELS[m], _fmt(m, t["before"]), _fmt(m, t["after"]), change)
    table.add_section()
    change = "-" if cmp["cost_change"] is None else f"{cmp['cost_change']:+.0%}"
    table.add_row("Cost", f"{cmp['cost_before']:.3f}", f"{cmp['cost_after']:.3f}", change)
    console.print(table)
    console.print(f"{len(cmp['moved'])} items change location.")


@app.command()
def validate(project_dir: ProjectDir = None, as_json: JsonFlag = False) -> None:
    """Check the input files and report every problem with its file, line and field."""
    project = load_project(project_dir or sample_dir())
    report = project.report
    feasibility: list[str] = []
    if project.problem is not None:
        try:
            greedy_assignment(project.problem)
        except InfeasibleError as exc:
            feasibility = exc.report.lines()
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "ok": project.ok and not feasibility,
                    "issues": [asdict(i) for i in report.sorted()],
                    "infeasible": feasibility,
                },
                indent=2,
            )
        )
    else:
        _print_issues(report)
        for line in feasibility:
            err.print(f"[red]infeasible: {line}[/red]", highlight=False)
        if project.ok and not feasibility:
            p = project.require_problem()
            n_assignable = sum(1 for loc in p.locations.values() if loc.assignable)
            console.print(
                f"[green]OK[/green]: {len(p.items)} items, {n_assignable} compartments, "
                f"{len(p.routines)} routines, {len(p.hard)} hard and {len(p.soft)} soft "
                f"constraints, {len(p.obstructions)} obstructions "
                f"({len(report.warnings)} warnings)."
            )
    if not project.ok or feasibility:
        raise typer.Exit(1)


@app.command()
def simulate(
    project_dir: ProjectDir = None,
    assignment: Annotated[
        Path | None, typer.Option(help="Assignment CSV to simulate (item_id, location_id).")
    ] = None,
    current: Annotated[
        bool, typer.Option("--current", help="Simulate current_assignment.csv.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Simulate every routine under an assignment (the greedy one by default)."""
    project = _load(project_dir, quiet=as_json)
    problem = project.require_problem()
    compiled = Compiled(problem)
    greedy = _feasible_start(problem)
    normalizers = baseline_normalizers(problem, compiled)
    if assignment is not None:
        chosen, label = _read_assignment(problem, assignment), assignment.name
    elif current:
        if project.current_assignment is None:
            err.print("[red]No usable current_assignment.csv in the project.[/red]")
            raise typer.Exit(1)
        chosen, label = project.current_assignment, "current assignment"
    else:
        chosen, label = greedy, "greedy assignment"
    report = assignment_report(problem, chosen, normalizers, compiled)
    if as_json:
        typer.echo(json.dumps(report, indent=2))
        return
    console.print(_metrics_table(f"Metrics for the {label}", report))
    for v in report["violations"]:
        console.print(f"[yellow]violation:[/yellow] {v['message']}", highlight=False)


@app.command()
def optimize(
    project_dir: ProjectDir = None,
    seed: Annotated[int, typer.Option(help="Random seed. Same seed, same result.")] = 0,
    iterations: Annotated[
        int, typer.Option(help="Annealing iterations per restart.")
    ] = DEFAULT_ITERATIONS,
    time_limit: Annotated[
        float | None,
        typer.Option(help="Wall-clock budget in seconds. Replaces the iteration budget."),
    ] = None,
    restarts: Annotated[
        int, typer.Option(help="Independent runs, each with the full budget; the best is kept.")
    ] = DEFAULT_RESTARTS,
    out: Annotated[Path | None, typer.Option(help="Write the assignment CSV here.")] = None,
    as_json: JsonFlag = False,
) -> None:
    """Find the best assignment. Ctrl-C stops early and keeps the best solution so far."""
    project = _load(project_dir, quiet=as_json)
    problem = project.require_problem()
    compiled = Compiled(problem)
    greedy = _feasible_start(problem)
    normalizers = baseline_normalizers(problem, compiled)

    cancel = threading.Event()
    previous = signal.signal(signal.SIGINT, lambda *_: cancel.set())
    optimizer = SimulatedAnnealing(
        seed=seed, iterations=iterations, time_limit_s=time_limit, restarts=restarts
    )
    try:
        if as_json:
            result = optimizer.optimize(problem, normalizers=normalizers, cancel=cancel)
        else:
            with Progress(
                TextColumn("[bold]Optimizing"),
                BarColumn(),
                TextColumn("{task.percentage:>3.0f}%"),
                TextColumn("best {task.fields[best]:.4f}"),
                TimeElapsedColumn(),
                console=err,
                transient=True,
            ) as bar:
                task = bar.add_task("optimize", total=1.0, best=float("nan"))
                result = optimizer.optimize(
                    problem,
                    normalizers=normalizers,
                    cancel=cancel,
                    progress=lambda e: bar.update(task, completed=e.fraction, best=e.best_cost),
                )
    finally:
        signal.signal(signal.SIGINT, previous)

    report = assignment_report(problem, result.assignment, normalizers, compiled)
    payload: dict[str, Any] = {
        "seed": seed,
        "iterations": result.iterations,
        "elapsed_s": result.elapsed_s,
        "stopped_early": result.stopped_early,
        "greedy_cost": result.initial_cost,
        "cost": result.cost,
        **report,
    }
    greedy_report = assignment_report(problem, greedy, normalizers, compiled)
    payload["vs_greedy"] = comparison(greedy_report, report)
    if project.current_assignment is not None:
        current_report = assignment_report(
            problem, project.current_assignment, normalizers, compiled
        )
        payload["vs_current"] = comparison(current_report, report)
    if out is not None:
        out.write_text(assignment_csv(problem, result.assignment), encoding="utf-8")
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
        return

    stopped = " (stopped early)" if result.stopped_early else ""
    console.print(
        f"Finished {result.iterations:,} iterations in {result.elapsed_s:.1f} s{stopped}. "
        f"Cost {result.cost:.4f} (greedy start {result.initial_cost:.4f})."
    )
    console.print(_locations_table(report))
    console.print(_metrics_table("Metrics for the optimized assignment", report))
    if "vs_current" in payload:
        _print_comparison("current", payload["vs_current"])
    else:
        _print_comparison("greedy", payload["vs_greedy"])
    assert not check_assignment(problem, result.assignment, compiled)
    if out is not None:
        console.print(f"Assignment written to {out}")


@app.command()
def sensitivity(
    project_dir: ProjectDir = None,
    runs: Annotated[int, typer.Option(help="Re-optimizations of each kind.")] = 20,
    mode: Annotated[
        str,
        typer.Option(
            help="both: noise and perturbed runs. baseline: only new seeds, same frequencies "
            "(optimizer noise). perturbed: only perturbed frequencies."
        ),
    ] = "both",
    perturbation: Annotated[
        float, typer.Option(help="Frequencies are scaled by U(1-x, 1+x).")
    ] = 0.3,
    seed: Annotated[int, typer.Option()] = 0,
    iterations: Annotated[
        int, typer.Option(help="Annealing iterations per restart.")
    ] = DEFAULT_ITERATIONS,
    restarts: Annotated[
        int, typer.Option(help="Restarts per run; the best is kept.")
    ] = DEFAULT_RESTARTS,
    workers: Annotated[int | None, typer.Option(help="Processes. Default: all cores.")] = None,
    as_json: JsonFlag = False,
) -> None:
    """Separate optimizer noise from real sensitivity to the frequency estimates."""
    if mode not in ("both", "baseline", "perturbed"):
        err.print("[red]--mode must be both, baseline or perturbed.[/red]")
        raise typer.Exit(1)
    project = _load(project_dir, quiet=as_json)
    problem = project.require_problem()
    _feasible_start(problem)
    kwargs: dict[str, Any] = {
        "runs": 0 if mode == "baseline" else runs,
        "noise_runs": 0 if mode == "perturbed" else runs,
        "perturbation": perturbation,
        "seed": seed,
        "iterations": iterations,
        "restarts": restarts,
        "workers": workers,
    }
    if as_json:
        report = run_sensitivity(problem, **kwargs)
        typer.echo(json.dumps(sensitivity_dict(report, problem), indent=2))
        return
    total = kwargs["runs"] + kwargs["noise_runs"]
    with Progress(
        TextColumn("[bold]Sensitivity"),
        BarColumn(),
        TextColumn("{task.completed:.0f}/{task.total:.0f} runs"),
        TimeElapsedColumn(),
        console=err,
        transient=True,
    ) as bar:
        task = bar.add_task("runs", total=total)
        report = run_sensitivity(
            problem,
            progress=lambda done, total: bar.update(task, completed=done, total=total),
            **kwargs,
        )

    table = Table(title="Stability against the baseline layout", title_justify="left")
    table.add_column("Runs")
    table.add_column("n", justify="right")
    table.add_column("Same compartment", justify="right")
    table.add_column("Same wall and level", justify="right")
    table.add_column("Cost min / median / max", justify="right")
    sets = (
        ("Same frequencies, new seed (noise)", report.noise),
        (f"Frequencies ±{perturbation:.0%}", report.perturbed),
    )
    for name, rs in sets:
        if rs is None:
            continue
        s = rs.summary
        table.add_row(
            name,
            str(rs.runs),
            f"{rs.exact_stability:.0%}",
            f"{rs.area_stability:.0%}",
            f"{s['min']:.4f} / {s['median']:.4f} / {s['max']:.4f}",
        )
    console.print(table)
    c = report.counts
    console.print(
        f"Baseline cost {report.baseline_cost:.4f}. By wall and level: {c['robust']} robust, "
        f"{c['noisy']} noisy (move even with the same frequencies), {c['sensitive']} sensitive "
        f"(move only when frequencies change), {c['unused']} unused, {c['pinned']} pinned."
    )
    rows = [i for i in report.items if i.label in ("sensitive", "noisy")]
    if not rows:
        return
    detail = Table(title="Items that move", title_justify="left")
    detail.add_column("Item")
    detail.add_column("Kind")
    detail.add_column("Baseline location")
    detail.add_column("Noise: same wall+level", justify="right")
    detail.add_column("Perturbed: same wall+level", justify="right")
    detail.add_column("Most common location")

    def pct(shares, attr: str) -> str:
        return "-" if shares is None else f"{getattr(shares, attr):.0%}"

    for i in sorted(rows, key=lambda i: (i.label != "sensitive", i.item)):
        modal = (i.perturbed or i.noise).modal_location  # type: ignore[union-attr]
        detail.add_row(
            problem.items[i.item].name,
            i.label,
            i.baseline_location,
            pct(i.noise, "area"),
            pct(i.perturbed, "area"),
            modal,
        )
    console.print(detail)


@app.command()
def coverage(project_dir: ProjectDir = None, as_json: JsonFlag = False) -> None:
    """Report unused items, suspicious routines and frequency mismatches."""
    project = _load(project_dir, quiet=as_json)
    problem = project.require_problem()
    report = analyze_coverage(problem)
    if as_json:
        typer.echo(json.dumps(coverage_dict(report, problem), indent=2))
        return
    console.print(f"{report.covered} of {report.total} items appear in at least one routine.")
    if report.unused_items:
        names = ", ".join(problem.items[i].name for i in report.unused_items)
        console.print(f"[bold]Unused items[/bold] ({len(report.unused_items)}): {names}")
    for f in report.findings:
        style = "yellow" if f.severity == "warning" else "dim"
        console.print(f"[{style}]{f.routine}: {f.message}[/{style}]", highlight=False)
    table = Table(title="Estimated vs derived weekly frequency", title_justify="left")
    table.add_column("Item")
    table.add_column("Estimated", justify="right")
    table.add_column("From routines", justify="right")
    table.add_column("Flag")
    for f in report.frequencies:
        if f.flag in ("underestimated", "overestimated"):
            table.add_row(problem.items[f.item].name, f"{f.estimated:g}", f"{f.derived:g}", f.flag)
    if table.row_count:
        console.print(table)


@app.command()
def export(
    project_dir: ProjectDir = None,
    out: Annotated[Path, typer.Option(help="Destination CSV.")] = Path("assignment.csv"),
    seed: Annotated[int, typer.Option()] = 0,
    iterations: Annotated[int, typer.Option()] = DEFAULT_ITERATIONS,
    restarts: Annotated[int, typer.Option()] = DEFAULT_RESTARTS,
) -> None:
    """Optimize and write only the assignment CSV."""
    project = _load(project_dir, quiet=True)
    problem = project.require_problem()
    _feasible_start(problem)
    result = SimulatedAnnealing(seed=seed, iterations=iterations, restarts=restarts).optimize(
        problem
    )
    out.write_text(assignment_csv(problem, result.assignment), encoding="utf-8")
    console.print(f"Assignment written to {out} (cost {result.cost:.4f}).")


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option(help="Restart on code changes.")] = False,
) -> None:
    """Start the web application."""
    import uvicorn

    uvicorn.run("reach.api.app:app", host=host, port=port, reload=reload)


# --- LLM helpers ----------------------------------------------------------------------------


def _client():
    from reach.llm import LlmUnavailableError, default_client

    try:
        return default_client()
    except LlmUnavailableError as exc:
        err.print(f"[red]LLM features are unavailable: {exc}.[/red]")
        raise typer.Exit(3) from exc


def _llm_call(fn, *args, **kwargs):
    from reach.llm import LlmError, LlmUnavailableError

    try:
        return fn(*args, **kwargs)
    except (LlmError, LlmUnavailableError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(3) from exc


def _print_routines(result, out: Path | None) -> None:
    from reach.io.export import dump_yaml

    text = dump_yaml({"routines": [r.routine for r in result.routines]}) if result.routines else ""
    for r in result.routines:
        for issue in r.issues:
            err.print(f"[yellow]{r.routine.get('id')}: {issue}[/yellow]", highlight=False)
    for m in result.missing_items:
        err.print(
            f"[cyan]suggested missing item:[/cyan] {m['name']} ({m['reason']})", highlight=False
        )
    if result.notes:
        err.print(f"[dim]{result.notes}[/dim]", highlight=False)
    if out is not None and text:
        out.write_text("# Proposed routines. Review before copying into routines.yaml.\n" + text)
        console.print(f"Proposal written to {out}")
    else:
        typer.echo(text)


@llm_app.command("routine")
def llm_routine(
    text: Annotated[str, typer.Argument(help="Describe an activity or paste a recipe.")] = "",
    project_dir: Annotated[Path | None, typer.Option("--project")] = None,
    url: Annotated[str | None, typer.Option(help="Recipe URL to read.")] = None,
    file: Annotated[Path | None, typer.Option(help="Read the description from a file.")] = None,
    out: Annotated[Path | None, typer.Option(help="Write the proposal here.")] = None,
) -> None:
    """Draft routines from natural language, using only ids from your catalog."""
    from reach.llm import propose_routines

    project = _load(project_dir, quiet=True)
    if file is not None:
        text = f"{text}\n{file.read_text(encoding='utf-8')}"
    _print_routines(_llm_call(propose_routines, project, text, _client(), url=url), out)


@llm_app.command("cover")
def llm_cover(
    project_dir: Annotated[Path | None, typer.Option("--project")] = None,
    out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Draft routines for the items that no routine uses."""
    from reach.llm import propose_coverage_routines

    project = _load(project_dir, quiet=True)
    _print_routines(_llm_call(propose_coverage_routines, project, _client()), out)


@llm_app.command("diary")
def llm_diary(
    diary: Annotated[Path, typer.Argument(help="Plain-text diary with DD/MM day headers.")],
    project_dir: Annotated[Path | None, typer.Option("--project")] = None,
    out: Annotated[Path, typer.Option()] = Path("routines.proposed.yaml"),
) -> None:
    """Turn a usage diary into routines with measured weekly frequencies."""
    from reach.io.export import dump_yaml
    from reach.llm import import_diary

    project = _load(project_dir, quiet=True)
    result = _llm_call(import_diary, project, diary.read_text(encoding="utf-8"), _client())
    table = Table(
        title=f"Activities over {result.recorded_days} recorded days", title_justify="left"
    )
    table.add_column("Routine")
    table.add_column("Status")
    table.add_column("Times", justify="right")
    table.add_column("Per week", justify="right")
    table.add_column("Was", justify="right")
    for a in result.activities:
        was = "-" if a.previous_frequency is None else f"{a.previous_frequency:g}"
        table.add_row(
            a.name,
            "existing" if a.existing else "new",
            str(a.occurrences),
            f"{a.frequency_per_week:g}",
            was,
        )
    console.print(table)
    for u in result.unclear:
        console.print(
            f"[yellow]unclear (line {u['line']}): {u['text']}: {u['reason']}[/yellow]",
            highlight=False,
        )
    for m in result.missing_items:
        console.print(
            f"[cyan]suggested missing item:[/cyan] {m['name']} ({m['reason']})", highlight=False
        )
    proposal = {
        "new_routines": [a.proposal.routine for a in result.activities if a.proposal],
        "measured_frequencies": {
            a.routine_id: a.frequency_per_week for a in result.activities if a.existing
        },
    }
    out.write_text(
        "# Proposed from the usage diary. Review before copying into routines.yaml.\n"
        + dump_yaml(proposal)
    )
    console.print(f"Proposal written to {out}")


@llm_app.command("constraints")
def llm_constraints(
    project_dir: Annotated[Path | None, typer.Option("--project")] = None,
    out: Annotated[Path, typer.Option()] = Path("constraints.proposed.yaml"),
) -> None:
    """Propose constraints from the free-text comments in items.csv."""
    from reach.llm import propose_constraints

    project = _load(project_dir, quiet=True)
    result = _llm_call(propose_constraints, project, _client())
    for cid, why in result.rationale.items():
        console.print(f"[green]{cid}[/green]: {why}", highlight=False)
    for r in result.rejected:
        console.print(
            f"[yellow]rejected {r['constraint'].get('id')}: {'; '.join(r['problems'])}[/yellow]",
            highlight=False,
        )
    if result.yaml:
        out.write_text(result.yaml, encoding="utf-8")
        console.print(f"Proposal written to {out}. Nothing was applied.")
    else:
        console.print("No constraints to propose.")


if __name__ == "__main__":
    app()
