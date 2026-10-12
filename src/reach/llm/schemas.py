"""Shapes the model is asked to fill in.

They are deliberately plain (no aliases, unions or numeric bounds) so they work as structured
output schemas. Everything is converted to the strict file schemas and validated afterwards.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class LlmStep(BaseModel):
    kind: Literal["go_to", "take", "use", "put_down", "store", "set_state"]
    item: str | None = None
    at: str | None = None
    obstruction: str | None = None
    state: Literal["open", "closed"] | None = None


class LlmRoutine(BaseModel):
    id: str
    name: str
    frequency_per_week: float
    start: str
    steps: list[LlmStep]


class MissingItem(BaseModel):
    name: str
    reason: str


class RoutineDraft(BaseModel):
    routines: list[LlmRoutine]
    missing_items: list[MissingItem]
    notes: str


class DiaryActivity(BaseModel):
    existing_routine_id: str | None = None
    """Set when the entries match a routine that already exists."""
    new_routine: LlmRoutine | None = None
    """Set otherwise. Its frequency is ignored; it is computed from the entries."""
    entry_ids: list[str]


class UnclearEntry(BaseModel):
    entry_id: str
    reason: str


class DiaryDraft(BaseModel):
    activities: list[DiaryActivity]
    unclear: list[UnclearEntry]
    missing_items: list[MissingItem]


class LlmHard(BaseModel):
    id: str
    type: Literal[
        "require_tag",
        "forbid_tag",
        "min_distance",
        "max_distance",
        "min_level",
        "max_level",
        "separate",
    ]
    items: list[str]
    categories: list[str]
    item_tags: list[str]
    tag: str | None = None
    poi: str | None = None
    distance_cm: float | None = None
    level: int | None = None
    from_items: list[str]
    from_categories: list[str]
    rationale: str


class LlmSoft(BaseModel):
    id: str
    items: list[str]
    categories: list[str]
    mode: Literal["same_compartment", "nearby"]
    rationale: str


class ConstraintDraft(BaseModel):
    hard: list[LlmHard]
    soft: list[LlmSoft]
    ignored_comments: list[str]
