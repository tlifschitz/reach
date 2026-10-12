"""YAML loading that remembers the source line of every key and list entry."""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError

from reach.io.errors import ValidationReport


class LineMap:
    """Maps a path such as ``("compartments", 3, "volume_l")`` to a 1-based line number."""

    def __init__(self) -> None:
        self._lines: dict[tuple[str, ...], int] = {}

    def set(self, path: tuple[str, ...], line: int) -> None:
        self._lines[path] = line

    def find(self, loc: tuple[Any, ...] | list[Any]) -> int | None:
        """Line of the deepest known prefix of ``loc``.

        Components that do not exist in the document are skipped, which absorbs the extra
        entries Pydantic puts in error locations (union tags, validator names).
        """
        path: tuple[str, ...] = ()
        line = self._lines.get(path)
        for part in loc:
            candidate = (*path, str(part))
            if candidate in self._lines:
                path = candidate
                line = self._lines[candidate]
        return line


def load_yaml(text: str, file: str, report: ValidationReport) -> tuple[Any, LineMap]:
    """Parse ``text``. Syntax errors are added to ``report`` and yield ``(None, empty map)``."""
    lines = LineMap()
    loader = yaml.SafeLoader(text)
    try:
        node = loader.get_single_node()
        data = loader.construct_document(node) if node is not None else None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        problem = getattr(exc, "problem", None) or str(exc)
        report.error(file, f"invalid YAML: {problem}", mark.line + 1 if mark else None)
        return None, lines
    finally:
        loader.dispose()

    def walk(n: yaml.Node, path: tuple[str, ...]) -> None:
        if isinstance(n, yaml.MappingNode):
            for key, value in n.value:
                child = (*path, str(key.value))
                walk(value, child)
                lines.set(child, key.start_mark.line + 1)
        elif isinstance(n, yaml.SequenceNode):
            for i, value in enumerate(n.value):
                child = (*path, str(i))
                walk(value, child)
                lines.set(child, value.start_mark.line + 1)

    if node is not None:
        lines.set((), node.start_mark.line + 1)
        walk(node, ())
    return data, lines


def field_path(loc: tuple[Any, ...] | list[Any]) -> str:
    """``("compartments", 3, "volume_l")`` -> ``compartments[3].volume_l``."""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


def report_validation_error(
    exc: ValidationError, file: str, lines: LineMap, report: ValidationReport
) -> None:
    for err in exc.errors():
        loc = err["loc"]
        message = err["msg"]
        if message.startswith("Value error, "):
            message = message[len("Value error, ") :]
        report.error(file, message, lines.find(loc), field_path(loc) or None)
