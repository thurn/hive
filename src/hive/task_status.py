"""Readable work and corruption reports without guessing unknown ownership."""

from dataclasses import dataclass

from hive.bead_json import decode_bead
from hive.beads_store import BeadsStore
from hive.configuration import Configuration, find_configuration
from hive.errors import HiveError
from hive.identity import ProjectId
from hive.model import Bead, owner_of
from hive.state_json import encode_state


@dataclass(frozen=True)
class RecordProblem:
    identifier: str
    detail: str


@dataclass(frozen=True)
class StatusReport:
    configuration: Configuration | None
    tasks: tuple[Bead, ...]
    problems: tuple[RecordProblem, ...]

    @property
    def owned_count(self) -> int | None:
        if self.problems:
            return None
        return sum(owner_of(bead.state) is not None for bead in self.tasks)


def read_status(store: BeadsStore, project: ProjectId | None) -> StatusReport:
    return decode_status(store.active_records(), project)


def decode_status(
    records: tuple[dict[str, object], ...], project: ProjectId | None
) -> StatusReport:
    tasks: list[Bead] = []
    problems: list[RecordProblem] = []
    try:
        configuration = find_configuration(
            tuple(raw for raw in records if raw.get("issue_type") == "role")
        )
    except HiveError as error:
        configuration = None
        problems.append(RecordProblem("configuration", error.detail))
    if project is not None and configuration is not None:
        configuration.project(project)
    for raw in records:
        if raw.get("issue_type") in {"role", "agent", "message"}:
            continue
        identifier = raw.get("id")
        name = identifier if isinstance(identifier, str) else "unknown"
        metadata = raw.get("metadata")
        if isinstance(metadata, dict) and "hive_config" in metadata:
            problems.append(
                RecordProblem(name, "Configuration metadata appears on a work item")
            )
        try:
            tasks.append(decode_bead(raw))
        except HiveError as error:
            problems.append(RecordProblem(name, error.detail))
    return StatusReport(configuration, tuple(tasks), tuple(problems))


def task_value(bead: Bead) -> dict[str, object]:
    native = encode_state(bead.state)
    return {
        "id": bead.id,
        "project": bead.project,
        "title": bead.title,
        "description": bead.description,
        "acceptance": bead.acceptance,
        "priority": bead.priority,
        "created": bead.created.isoformat(),
        "dependencies": list(bead.dependencies),
        "kind": bead.kind,
        "state": {
            "status": native.status,
            "owner": native.assignee or None,
            **native.metadata,
        },
    }


def status_value(report: StatusReport, project: ProjectId | None) -> dict[str, object]:
    configuration = report.configuration
    return {
        "code": "Status",
        "global_limit": (
            None if configuration is None else configuration.capacity.global_limit
        ),
        "global_owned": report.owned_count,
        "project_limits": (
            None
            if configuration is None
            else dict(configuration.capacity.project_limits)
        ),
        "tasks": [
            task_value(bead)
            for bead in report.tasks
            if project is None or bead.project == project
        ],
        "problems": [
            {"id": problem.identifier, "detail": problem.detail}
            for problem in report.problems
        ],
        "resources": {"cpu": None, "memory_pressure": None, "tollgate_queue": None},
    }
