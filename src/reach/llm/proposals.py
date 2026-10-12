"""LLM-backed proposals. Nothing here changes a project: every function returns a proposal."""

from __future__ import annotations

import html.parser
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import ValidationError

from reach.core.coverage import analyze_coverage, inspect_routine
from reach.io.errors import ValidationReport
from reach.io.export import dump_yaml
from reach.io.project import Project, build_routine
from reach.io.schemas import ConstraintsFile, RoutineModel
from reach.llm.client import LlmClient, LlmError
from reach.llm.schemas import ConstraintDraft, DiaryDraft, LlmRoutine, RoutineDraft

MAX_SOURCE_CHARS = 60_000

ROUTINE_RULES = """\
Rules for routines:
- Use ONLY ids from the catalog below. Never invent an item, POI, zone or obstruction id.
- If the activity needs something that is not in the catalog, do not make up an id: leave it out
  of the steps and list it under missing_items with a short reason.
- Prefer atomic routines: one activity each ("espresso", "toast"), with its own weekly frequency.
  Split composite activities such as "breakfast" into the atomic routines they consist of.
- Step kinds: go_to (at = POI or zone), take (item), use (item, at = POI or zone),
  put_down (item, at = zone or POI), store (item), set_state (obstruction, state).
- "take" walks to wherever the item is stored, so do not add a go_to before it.
- An item that is taken must later be used, put down or stored. Store things that go back.
- Items that stay out (a plate carried to the table) end with put_down.
- Washing up and putting away are separate routines, not part of cooking.
- start is the POI or zone where the person is when the activity begins.
- Routine ids are short snake_case and must not collide with existing routine ids unless you are
  deliberately reusing that routine.
"""


def catalog_context(project: Project, *, routines: bool = True) -> str:
    """The ids the model may use, as compact text."""
    space = project.space
    lines = ["## Items (id | name | category)"]
    for row in project.items:
        fixed = f" | fixed at {row.fixed_location}" if row.fixed_location else ""
        lines.append(f"{row.id} | {row.name} | {row.category}{fixed}")
    if space is not None:
        lines.append("\n## POIs (id | type | name)")
        lines += [f"{p.id} | {p.type} | {p.name}" for p in space.pois]
        lines.append("\n## Zones (id | type | name)")
        lines += [f"{z.id} | {z.type} | {z.name}" for z in space.zones]
        if space.obstructions:
            lines.append("\n## Obstructions (id | default state | blocks when)")
            lines += [f"{o.id} | {o.default_state} | {o.blocks_when}" for o in space.obstructions]
    if routines and project.routines is not None:
        lines.append("\n## Existing routines (id | name | per week | steps)")
        for r in project.routines.routines:
            steps = "; ".join(f"{k} {v}" for s in r.steps for k, v in s.to_yaml().items())
            lines.append(f"{r.id} | {r.name} | {r.frequency_per_week:g} | {steps}")
    return "\n".join(lines)


@dataclass(slots=True)
class RoutineProposal:
    routine: dict[str, Any]
    """The routine in ``routines.yaml`` form."""
    yaml: str
    issues: list[str] = field(default_factory=list)
    reuses_existing: bool = False


@dataclass(slots=True)
class RoutinesResult:
    routines: list[RoutineProposal]
    missing_items: list[dict[str, str]]
    notes: str = ""


def validate_routine(
    project: Project, data: dict[str, Any]
) -> tuple[RoutineModel | None, list[str]]:
    """Run one routine through the same validation as ``routines.yaml``."""
    try:
        model = RoutineModel.model_validate(data)
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" if e["loc"] else e["msg"]
            for e in exc.errors()
        ]
    problem = project.require_problem()
    report = ValidationReport()
    routine = build_routine(
        model,
        (),
        problem.items,
        problem.locations,
        {o.id for o in problem.obstructions},
        report,
    )
    issues = [f"{i.field}: {i.message}" if i.field else i.message for i in report.issues]
    if not issues:
        issues += [f.message for f in inspect_routine(routine) if f.severity == "warning"]
    return model, issues


def _routine_dict(r: LlmRoutine) -> dict[str, Any]:
    steps = []
    for s in r.steps:
        if s.kind == "go_to":
            steps.append({"go_to": s.at or ""})
        elif s.kind in ("take", "store"):
            steps.append({s.kind: s.item or ""})
        elif s.kind == "set_state":
            steps.append({"set_state": f"{s.obstruction} {s.state}"})
        else:
            steps.append({s.kind: f"{s.item} at {s.at}"})
    return {
        "id": r.id,
        "name": r.name,
        "frequency_per_week": round(max(r.frequency_per_week, 0.0), 2),
        "start": r.start,
        "steps": steps,
    }


def _proposal(project: Project, data: dict[str, Any]) -> RoutineProposal:
    model, issues = validate_routine(project, data)
    shown = model.to_yaml() if model is not None else data
    return RoutineProposal(shown, dump_yaml([shown]), issues)


class _TextExtractor(html.parser.HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "svg", "head", "nav", "footer", "header"})
    _BLOCK = frozenset({"p", "br", "li", "div", "h1", "h2", "h3", "h4", "tr", "section"})

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def fetch_url_text(url: str, *, timeout: float = 20.0) -> str:
    """Download a page (a recipe, typically) and reduce it to readable text."""
    if not re.match(r"^https?://", url):
        raise LlmError("only http(s) URLs can be fetched")
    try:
        response = httpx.get(
            url, timeout=timeout, follow_redirects=True, headers={"User-Agent": "reach/0.1"}
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise LlmError(f"could not fetch {url}: {exc}") from exc
    content_type = response.headers.get("content-type", "")
    return html_to_text(response.text) if "html" in content_type else response.text.strip()


def _check_length(text: str, what: str) -> None:
    if len(text) > MAX_SOURCE_CHARS:
        raise LlmError(
            f"{what} is {len(text):,} characters long; the limit is {MAX_SOURCE_CHARS:,}. "
            "Paste only the relevant part instead."
        )


def propose_routines(
    project: Project, text: str, client: LlmClient, *, url: str | None = None
) -> RoutinesResult:
    """Draft routines from a free-text description, a pasted recipe, or a URL."""
    source = text.strip()
    if url:
        page = fetch_url_text(url)
        _check_length(page, "The page")
        source = f"{source}\n\nContent of {url}:\n{page}".strip()
    if not source:
        raise LlmError("describe the activity, paste a recipe, or give a URL")
    _check_length(source, "The description")
    system = (
        "You turn descriptions of activities in a physical space into step-by-step routines for "
        "a storage-layout optimizer. The person performing them is alone.\n\n"
        + ROUTINE_RULES
        + "\nIf the text gives no frequency, estimate a sensible weekly frequency and say so in "
        "notes."
    )
    prompt = f"{catalog_context(project)}\n\n## Activity to model\n{source}"
    draft = client.complete(system=system, prompt=prompt, schema=RoutineDraft)
    return RoutinesResult(
        [_proposal(project, _routine_dict(r)) for r in draft.routines],
        [m.model_dump() for m in draft.missing_items],
        draft.notes,
    )


def propose_coverage_routines(project: Project, client: LlmClient) -> RoutinesResult:
    """Draft routines that use the items no routine mentions yet."""
    problem = project.require_problem()
    unused = analyze_coverage(problem).unused_items
    if not unused:
        return RoutinesResult([], [], "Every item already appears in a routine.")
    listing = "\n".join(
        f"{i} | {problem.items[i].name} | {problem.items[i].category} | "
        f"estimated {problem.items[i].estimated_frequency:g}/week"
        for i in unused
    )
    system = (
        "You complete the set of routines for a storage-layout optimizer so that the items "
        "people actually use appear in at least one routine.\n\n"
        + ROUTINE_RULES
        + "\nGroup the unused items into plausible atomic activities. Use each item's estimated "
        "weekly frequency to set the routine frequency. Items that are plainly never used "
        "(estimated near 0) may be left uncovered; mention them in notes. Do not rewrite "
        "existing routines."
    )
    prompt = f"{catalog_context(project)}\n\n## Items that no routine uses\n{listing}"
    draft = client.complete(system=system, prompt=prompt, schema=RoutineDraft)
    return RoutinesResult(
        [_proposal(project, _routine_dict(r)) for r in draft.routines],
        [m.model_dump() for m in draft.missing_items],
        draft.notes,
    )


# --- usage diary ----------------------------------------------------------------------------

_DAY_HEADER = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{1,2})\s*[:.\-]?\s*$")
_BULLET = re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s*")
_DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


@dataclass(slots=True)
class DiaryEntry:
    id: str
    date: str | None
    """``DD/MM``, or ``None`` when the entry is not under a valid day header."""
    text: str
    line: int


@dataclass(slots=True)
class ParsedDiary:
    days: list[str]
    entries: list[DiaryEntry]
    problems: list[dict[str, Any]]


def parse_diary(text: str) -> ParsedDiary:
    """Split a plain-text diary into dated entries. Deterministic; no model involved.

    A day starts with a line holding only ``DD/MM``. Every non-empty line below it is one entry.
    """
    days: list[str] = []
    entries: list[DiaryEntry] = []
    problems: list[dict[str, Any]] = []
    current: str | None = None
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        header = _DAY_HEADER.match(line)
        if header:
            day, month = int(header.group(1)), int(header.group(2))
            if 1 <= month <= 12 and 1 <= day <= _DAYS_IN_MONTH[month - 1]:
                current = f"{day:02d}/{month:02d}"
                if current in days:
                    problems.append(
                        {"line": n, "text": line, "reason": f"day {current} appears twice; merged"}
                    )
                else:
                    days.append(current)
            else:
                current = None
                problems.append({"line": n, "text": line, "reason": "not a valid DD/MM date"})
            continue
        entry = DiaryEntry(f"e{len(entries) + 1}", current, _BULLET.sub("", line), n)
        entries.append(entry)
        if current is None:
            problems.append({"line": n, "text": entry.text, "reason": "no clear date"})
    return ParsedDiary(days, entries, problems)


@dataclass(slots=True)
class DiaryActivityProposal:
    routine_id: str
    name: str
    existing: bool
    occurrences: int
    days: list[str]
    frequency_per_week: float
    previous_frequency: float | None
    entries: list[str]
    proposal: RoutineProposal | None
    """The routine to add, for new activities."""


@dataclass(slots=True)
class DiaryResult:
    recorded_days: int
    activities: list[DiaryActivityProposal]
    unclear: list[dict[str, Any]]
    missing_items: list[dict[str, str]]


def import_diary(project: Project, text: str, client: LlmClient) -> DiaryResult:
    """Interpret a usage diary: group entries into routines and measure weekly frequencies.

    The frequency of an activity is ``occurrences / recorded days * 7``, where the recorded days
    are the day headers found in the file.
    """
    _check_length(text, "The diary")
    parsed = parse_diary(text)
    dated = [e for e in parsed.entries if e.date is not None]
    unclear = list(parsed.problems)
    if not parsed.days or not dated:
        return DiaryResult(len(parsed.days), [], unclear, [])

    listing = "\n".join(f"{e.id} | {e.date} | {e.text}" for e in dated)
    system = (
        "You interpret a personal diary of activities in a physical space and turn it into "
        "routines for a storage-layout optimizer.\n\n" + ROUTINE_RULES + "\nDiary rules:\n"
        "- Each diary entry has an id. Assign every entry to exactly one activity or to unclear.\n"
        "- Group entries that describe the same activity, even when worded differently.\n"
        "- One entry may describe several activities ('coffee and toast'): put its id in each.\n"
        "- When an activity matches an existing routine, set existing_routine_id and leave "
        "new_routine empty. Otherwise fill new_routine.\n"
        "- Put an entry under unclear when you cannot tell what was done, or it is not an "
        "activity in this space. Do not guess.\n"
        "- Do not compute frequencies; they are derived from the entry ids."
    )
    prompt = f"{catalog_context(project)}\n\n## Diary entries (id | date | text)\n{listing}"
    draft = client.complete(system=system, prompt=prompt, schema=DiaryDraft)

    by_id = {e.id: e for e in dated}
    existing = {r.id: r for r in (project.routines.routines if project.routines else [])}
    n_days = len(parsed.days)
    merged: dict[str, DiaryActivityProposal] = {}
    for activity in draft.activities:
        entries = [by_id[i] for i in dict.fromkeys(activity.entry_ids) if i in by_id]
        if not entries:
            continue
        if activity.existing_routine_id in existing:
            current = existing[activity.existing_routine_id]
            routine_id, name, proposal = current.id, current.name, None
            previous: float | None = current.frequency_per_week
        elif activity.new_routine is not None:
            routine_id, name = activity.new_routine.id, activity.new_routine.name
            proposal, previous = None, None
        else:
            unclear += [
                {"line": e.line, "text": e.text, "reason": "matched no routine"} for e in entries
            ]
            continue
        slot = merged.get(routine_id)
        if slot is None:
            slot = merged[routine_id] = DiaryActivityProposal(
                routine_id, name, previous is not None, 0, [], 0.0, previous, [], proposal
            )
            if activity.new_routine is not None and previous is None:
                # Validated below, once the measured frequency is known.
                slot.proposal = RoutineProposal(_routine_dict(activity.new_routine), "")
        for e in entries:
            slot.occurrences += 1
            slot.entries.append(f"{e.date}: {e.text}")
            if e.date not in slot.days:
                slot.days.append(e.date)  # type: ignore[arg-type]
    for slot in merged.values():
        slot.frequency_per_week = round(slot.occurrences / n_days * 7, 2)
        if slot.proposal is not None:
            data = {**slot.proposal.routine, "frequency_per_week": slot.frequency_per_week}
            slot.proposal = _proposal(project, data)
    for u in draft.unclear:
        e = by_id.get(u.entry_id)
        if e is not None:
            unclear.append({"line": e.line, "text": e.text, "date": e.date, "reason": u.reason})
    assigned = {i for a in draft.activities for i in a.entry_ids} | {
        u.entry_id for u in draft.unclear
    }
    for e in dated:
        if e.id not in assigned:
            unclear.append(
                {"line": e.line, "text": e.text, "date": e.date, "reason": "not interpreted"}
            )
    activities = sorted(merged.values(), key=lambda a: -a.frequency_per_week)
    return DiaryResult(n_days, activities, unclear, [m.model_dump() for m in draft.missing_items])


# --- constraints from comments --------------------------------------------------------------


@dataclass(slots=True)
class ConstraintsResult:
    constraints: dict[str, Any]
    """Valid proposals, in ``constraints.yaml`` form."""
    yaml: str
    rationale: dict[str, str]
    rejected: list[dict[str, Any]]
    ignored_comments: list[str]


def propose_constraints(project: Project, client: LlmClient) -> ConstraintsResult:
    """Read the free-text ``comments`` of the catalog and propose structured constraints."""
    commented = [row for row in project.items if row.comments.strip()]
    if not commented:
        return ConstraintsResult({"hard": [], "soft": []}, "", {}, [], [])
    space = project.space
    tags = sorted({t for c in (space.compartments if space else []) for t in c.tags})
    existing = project.constraints or ConstraintsFile()
    system = (
        "You read free-text notes that a person wrote next to items of their catalog and turn "
        "the notes that express a storage rule into structured constraints.\n\n"
        "Constraint types:\n"
        "- require_tag / forbid_tag: the items may only go in, or may not go in, compartments "
        "carrying `tag`. Use only the compartment tags listed below.\n"
        "- min_distance / max_distance: distance in cm from the compartment to `poi`.\n"
        "- min_level / max_level: level 0 = low (bending), 1 = comfortable, 2 = high, "
        "3 = needs a step stool.\n"
        "- separate: the selected items never share a compartment with from_items / "
        "from_categories.\n"
        "- soft (together): items that should live in the same compartment or nearby.\n\n"
        "Rules:\n"
        "- Select items by id, category or item tag; use only ids, categories and tags that "
        "exist.\n"
        "- Only propose what a note actually asks for. A note that is not a storage rule goes "
        "to ignored_comments.\n"
        "- Do not repeat a constraint that already exists.\n"
        "- Leave unused fields empty. Give each constraint a short snake_case id and a one-line "
        "rationale quoting the note it comes from."
    )
    notes = "\n".join(f"{r.id} | {r.category} | {r.comments.strip()}" for r in commented)
    prompt = (
        f"{catalog_context(project, routines=False)}\n\n"
        f"## Compartment tags\n{', '.join(tags) or '(none)'}\n\n"
        f"## Existing constraint ids\n"
        f"{', '.join(c.id for c in [*existing.hard, *existing.soft]) or '(none)'}\n\n"
        f"## Notes (item id | category | note)\n{notes}"
    )
    draft = client.complete(system=system, prompt=prompt, schema=ConstraintDraft)

    taken = {c.id for c in [*existing.hard, *existing.soft]}
    hard: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []
    rationale: dict[str, str] = {}
    rejected: list[dict[str, Any]] = []

    def selector(items: list[str], categories: list[str], item_tags: list[str] | None = None):
        out: dict[str, Any] = {}
        if items:
            out["items"] = items
        if categories:
            out["categories"] = categories
        if item_tags:
            out["tags"] = item_tags
        return out

    for h in draft.hard:
        data: dict[str, Any] = {
            "id": h.id,
            "type": h.type,
            "select": selector(h.items, h.categories, h.item_tags),
        }
        if h.type in ("require_tag", "forbid_tag"):
            data["tag"] = h.tag
        elif h.type in ("min_distance", "max_distance"):
            data["poi"] = h.poi
            data["distance_cm"] = h.distance_cm
        elif h.type in ("min_level", "max_level"):
            data["level"] = h.level
        else:
            data["from"] = selector(h.from_items, h.from_categories)
        problems = _check_constraint(project, {"hard": [data]}, taken)
        if problems:
            rejected.append({"constraint": data, "problems": problems, "rationale": h.rationale})
        else:
            hard.append(data)
            taken.add(h.id)
            rationale[h.id] = h.rationale
    for s in draft.soft:
        data = {
            "id": s.id,
            "type": "together",
            "select": selector(s.items, s.categories),
            "mode": s.mode,
        }
        problems = _check_constraint(project, {"soft": [data]}, taken)
        if problems:
            rejected.append({"constraint": data, "problems": problems, "rationale": s.rationale})
        else:
            soft.append(data)
            taken.add(s.id)
            rationale[s.id] = s.rationale
    constraints = {"hard": hard, "soft": soft}
    header = (
        "# Proposed by `reach llm constraints` from the comments in items.csv.\n"
        "# Review each entry, then copy the ones you want into constraints.yaml.\n"
    )
    body = dump_yaml(constraints) if hard or soft else ""
    return ConstraintsResult(
        constraints, header + body if body else "", rationale, rejected, draft.ignored_comments
    )


def _check_constraint(project: Project, data: dict[str, Any], taken: set[str]) -> list[str]:
    """Schema and reference checks for a single proposed constraint."""
    try:
        parsed = ConstraintsFile.model_validate(data)
    except ValidationError as exc:
        return [f"{'.'.join(str(p) for p in e['loc'][2:])}: {e['msg']}" for e in exc.errors()]
    problem = project.require_problem()
    space = project.space
    location_tags = {t for c in (space.compartments if space else []) for t in c.tags}
    categories = {i.category for i in problem.items.values()}
    out: list[str] = []
    for c in [*parsed.hard, *parsed.soft]:
        if c.id in taken:
            out.append(f"id '{c.id}' is already used")
        selectors = [c.select]
        if getattr(c, "other", None) is not None:
            selectors.append(c.other)  # type: ignore[union-attr]
        for sel in selectors:
            out += [f"unknown item '{i}'" for i in sel.items if i not in problem.items]
            out += [f"unknown category '{k}'" for k in sel.categories if k not in categories]
        tag = getattr(c, "tag", None)
        if tag is not None and tag not in location_tags:
            out.append(f"no compartment has tag '{tag}'")
        poi = getattr(c, "poi", None)
        if poi is not None and poi not in problem.locations:
            out.append(f"unknown POI '{poi}'")
    return out
