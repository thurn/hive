"""The Beads-backed project registry and admission limits, without a scheduler."""

from dataclasses import dataclass
from pathlib import Path

from hive.bead_json import bead_id
from hive.errors import ErrorCode, HiveError
from hive.identity import BeadId, CodexProjectId, ProjectId
from hive.jsonvalue import integer, record, sequence, string
from hive.model import Capacity


@dataclass(frozen=True)
class Project:
    id: ProjectId
    repository: Path
    invariants: Path
    native_id: CodexProjectId


@dataclass(frozen=True)
class Configuration:
    id: BeadId
    projects: tuple[Project, ...]
    capacity: Capacity = Capacity()

    def project(self, identifier: ProjectId) -> Project:
        for project in self.projects:
            if project.id == identifier:
                return project
        raise HiveError(ErrorCode.INVALID_INPUT, f"Unregistered project: {identifier}")


def decode_configuration(value: object) -> Configuration:
    data = record(value, "configuration issue")
    if (
        data.get("issue_type") != "role"
        or data.get("status") != "open"
        or data.get("assignee")
    ):
        raise HiveError(
            ErrorCode.INVALID_RECORD,
            "Hive configuration must be an unassigned open role record",
        )
    if data.get("ephemeral") or data.get("no_history") is not True:
        raise HiveError(
            ErrorCode.INVALID_RECORD,
            "Hive configuration must be a permanent infrastructure record",
        )
    body = record(
        record(data.get("metadata"), "metadata").get("hive_config"),
        "Hive configuration",
    )
    projects: list[Project] = []
    for item in sequence(body.get("projects"), "projects"):
        project = record(item, "project")
        root = Path(string(project.get("repository"), "repository"))
        invariants = Path(string(project.get("invariants"), "invariants"))
        if not root.is_absolute() or not invariants.is_absolute():
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Project locations must be absolute"
            )
        projects.append(
            Project(
                ProjectId(string(project.get("id"), "project ID")),
                root,
                invariants,
                CodexProjectId(string(project.get("native_id"), "native project ID")),
            )
        )
    identifiers = {project.id for project in projects}
    if len(identifiers) != len(projects):
        raise HiveError(ErrorCode.INVALID_RECORD, "Duplicate project identity")
    ceiling = integer(body.get("global_limit"), "global capacity", minimum=1)
    overrides: list[tuple[ProjectId, int]] = []
    for key, value in record(body.get("project_limits"), "project capacities").items():
        identifier = ProjectId(key)
        limit = integer(value, "project capacity", minimum=1)
        if identifier not in identifiers:
            raise HiveError(
                ErrorCode.INVALID_RECORD,
                "Project capacity must reference a registered project",
            )
        overrides.append((identifier, limit))
    return Configuration(
        bead_id(data.get("id")), tuple(projects), Capacity(ceiling, tuple(overrides))
    )


def configuration_metadata(
    projects: tuple[Project, ...], capacity: Capacity
) -> dict[str, object]:
    identifiers = {project.id for project in projects}
    if (
        len(identifiers) != len(projects)
        or isinstance(capacity.global_limit, bool)
        or capacity.global_limit < 1
    ):
        raise HiveError(ErrorCode.INVALID_INPUT, "Invalid projects or global capacity")
    for project in projects:
        if (
            not project.id.strip()
            or not project.native_id.strip()
            or not project.repository.is_absolute()
            or not project.invariants.is_absolute()
        ):
            raise HiveError(
                ErrorCode.INVALID_INPUT,
                "Project registration needs identities and absolute locations",
            )
    if len(dict(capacity.project_limits)) != len(capacity.project_limits):
        raise HiveError(ErrorCode.INVALID_INPUT, "Duplicate project capacity override")
    for identifier, limit in capacity.project_limits:
        if identifier not in identifiers or isinstance(limit, bool) or limit < 1:
            raise HiveError(ErrorCode.INVALID_INPUT, "Invalid project capacity")
    return {
        "hive_config": {
            "projects": [
                {
                    "id": p.id,
                    "repository": str(p.repository),
                    "invariants": str(p.invariants),
                    "native_id": p.native_id,
                }
                for p in projects
            ],
            "global_limit": capacity.global_limit,
            "project_limits": dict(capacity.project_limits),
        }
    }


def find_configuration(records: tuple[dict[str, object], ...]) -> Configuration:
    matches = [
        data
        for data in records
        if "hive_config" in record(data.get("metadata", {}), "metadata")
    ]
    if len(matches) != 1:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Expected exactly one Hive configuration record"
        )
    return decode_configuration(matches[0])
