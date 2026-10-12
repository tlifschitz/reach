"""Validation issues that point at a file, a line and a field."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Issue:
    severity: str
    """``error`` or ``warning``."""
    file: str
    message: str
    line: int | None = None
    field: str | None = None

    def __str__(self) -> str:
        where = self.file if self.line is None else f"{self.file}:{self.line}"
        what = f"{self.field}: {self.message}" if self.field else self.message
        return f"{where}: {self.severity}: {what}"


@dataclass(slots=True)
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)

    def error(self, file: str, message: str, line: int | None = None, field: str | None = None):
        self.issues.append(Issue("error", file, message, line, field))

    def warning(self, file: str, message: str, line: int | None = None, field: str | None = None):
        self.issues.append(Issue("warning", file, message, line, field))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def sorted(self) -> list[Issue]:
        return sorted(
            self.issues, key=lambda i: (i.severity != "error", i.file, i.line or 0, i.field or "")
        )


class ProjectError(Exception):
    """The input files contain errors."""

    def __init__(self, report: ValidationReport) -> None:
        super().__init__("\n".join(str(i) for i in report.errors))
        self.report = report
