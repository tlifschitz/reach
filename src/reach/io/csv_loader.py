"""CSV loading for the item catalog and for assignments."""

from __future__ import annotations

import csv
import io
import re

from pydantic import ValidationError

from reach.io.errors import ValidationReport
from reach.io.schemas import ITEM_COLUMNS, ItemRow

_REQUIRED_ITEM_COLUMNS = ("id", "volume_l")
_TAG_SPLIT = re.compile(r"[;|]")


def load_items(
    text: str, file: str, report: ValidationReport
) -> tuple[list[ItemRow], dict[str, int]]:
    """Parse ``items.csv``. Returns the valid rows and the line of each item id."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    for column in _REQUIRED_ITEM_COLUMNS:
        if column not in header:
            report.error(file, f"missing column '{column}'", 1, column)
    for column in header:
        if column not in ITEM_COLUMNS:
            report.error(
                file, f"unknown column '{column}'; expected {', '.join(ITEM_COLUMNS)}", 1, column
            )
    if not report.ok and any(i.file == file for i in report.errors):
        return [], {}

    rows: list[ItemRow] = []
    lines: dict[str, int] = {}
    for raw in reader:
        line = reader.line_num
        if None in raw:
            report.error(file, "more cells than columns (is a comma missing quotes?)", line)
            continue
        if all(not (v or "").strip() for v in raw.values()):
            continue
        data: dict[str, object] = {}
        for key, value in raw.items():
            value = (value or "").strip()
            if key == "tags":
                data[key] = [t.strip() for t in _TAG_SPLIT.split(value) if t.strip()]
            elif key == "comments" or key == "name" or key == "category" or value != "":
                data[key] = value
        try:
            row = ItemRow.model_validate(data)
        except ValidationError as exc:
            for err in exc.errors():
                field = str(err["loc"][0]) if err["loc"] else None
                report.error(file, err["msg"], line, field)
            continue
        if row.id in lines:
            report.error(
                file, f"duplicate item id '{row.id}' (first at line {lines[row.id]})", line, "id"
            )
            continue
        lines[row.id] = line
        rows.append(row)
    return rows, lines


def load_assignment(
    text: str, file: str, report: ValidationReport
) -> tuple[dict[str, str], dict[str, int]]:
    """Parse an assignment CSV with ``item_id`` and ``location_id`` columns."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    item_col = next((c for c in ("item_id", "item", "id") if c in header), None)
    loc_col = next((c for c in ("location_id", "location", "compartment_id") if c in header), None)
    if item_col is None or loc_col is None:
        report.error(file, "expected columns 'item_id' and 'location_id'", 1)
        return {}, {}
    out: dict[str, str] = {}
    lines: dict[str, int] = {}
    for raw in reader:
        item = (raw.get(item_col) or "").strip()
        loc = (raw.get(loc_col) or "").strip()
        if not item and not loc:
            continue
        if not item or not loc:
            report.error(file, "both item_id and location_id are required", reader.line_num)
            continue
        if item in out:
            report.error(file, f"duplicate item '{item}'", reader.line_num, item_col)
            continue
        out[item] = loc
        lines[item] = reader.line_num
    return out, lines
