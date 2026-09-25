"""Immutable project observation configuration at the launcher boundary."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from hive.jsonvalue import parse, record, sequence, string


@dataclass(frozen=True)
class Project:
    id: str
    repository: Path
    observe_since: date | None = None


def projects(raw: str) -> tuple[Project, ...]:
    result: list[Project] = []
    for item in sequence(parse(raw), "projects"):
        value = record(item, "project")
        since = value.get("observe_since")
        result.append(
            Project(
                string(value.get("id"), "project id"),
                Path(string(value.get("repository"), "project repository")),
                (
                    None
                    if since is None
                    else date.fromisoformat(string(since, "observe_since"))
                ),
            )
        )
    return tuple(result)
