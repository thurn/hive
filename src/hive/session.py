"""Conversation enrollment and title intent, never execution ownership."""

from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from hive.bead_json import bead_id
from hive.errors import ErrorCode, HiveError
from hive.identity import BeadId, CodexTaskId, ProjectId
from hive.jsonvalue import record, string


class Role(StrEnum):
    EXECUTOR = "executor"
    WARDEN = "warden"
    WEAVER = "weaver"
    SAGE = "sage"
    JUSTICIAR = "justiciar"
    VIZIER = "vizier"
    ARCHIVIST = "archivist"
    BEAD = "bead"

    @property
    def emoji(self) -> str:
        return {
            Role.EXECUTOR: "⚒️",
            Role.WARDEN: "🛡️",
            Role.WEAVER: "🧵",
            Role.SAGE: "📖",
            Role.JUSTICIAR: "🔥",
            Role.VIZIER: "🔮",
            Role.ARCHIVIST: "📁",
            Role.BEAD: "📿",
        }[self]


@dataclass(frozen=True)
class Focus:
    role: Role
    subject: str
    bead: BeadId | None = None
    stage: str = ""

    def __post_init__(self) -> None:
        if not self.subject.strip() or any(
            not c.isprintable() for c in self.subject + self.stage
        ):
            raise HiveError(ErrorCode.INVALID_INPUT, "Task titles need printable text")

    @property
    def title(self) -> str:
        bead = "" if self.bead is None else f" [{self.bead}]"
        stage = "" if not self.stage else f" · {self.stage}"
        return f"{self.role.emoji}{bead} {self.subject}{stage}"


@dataclass(frozen=True)
class PendingName:
    pass


@dataclass(frozen=True)
class AppliedName:
    pass


@dataclass(frozen=True)
class FailedName:
    detail: str

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise HiveError(ErrorCode.INVALID_INPUT, "A naming failure needs a detail")


type Naming = PendingName | AppliedName | FailedName


@dataclass(frozen=True)
class Session:
    id: BeadId
    task: CodexTaskId
    project: ProjectId
    focus: Focus
    naming: Naming


def metadata(
    task: CodexTaskId, project: ProjectId, focus: Focus, naming: Naming
) -> dict[str, object]:
    name: dict[str, object]
    if isinstance(naming, PendingName):
        name = {"kind": "pending"}
    elif isinstance(naming, AppliedName):
        name = {"kind": "applied"}
    elif isinstance(naming, FailedName):
        name = {"kind": "failed", "detail": naming.detail}
    else:
        assert_never(naming)
    return {
        "hive_session": {
            "task": task,
            "project": project,
            "focus": {
                "role": focus.role,
                "subject": focus.subject,
                "bead": focus.bead,
                "stage": focus.stage,
            },
            "naming": name,
        }
    }


def decode(value: object) -> Session:
    raw = record(value, "session issue")
    if (
        raw.get("issue_type") != "role"
        or raw.get("status") != "open"
        or raw.get("assignee")
        or raw.get("ephemeral")
        or raw.get("no_history") is not True
    ):
        raise HiveError(
            ErrorCode.INVALID_RECORD,
            "Session must be permanent, open, unassigned infrastructure",
        )
    data = record(record(raw.get("metadata")).get("hive_session"), "session")
    body = record(data.get("focus"), "focus")
    try:
        role = Role(string(body.get("role"), "role"))
    except ValueError as error:
        raise HiveError(ErrorCode.INVALID_RECORD, "Unknown Hive role") from error
    focus = Focus(
        role,
        string(body.get("subject"), "subject"),
        None if body.get("bead") is None else bead_id(body.get("bead")),
        string(body.get("stage"), "stage", empty=True),
    )
    name = record(data.get("naming"), "naming")
    naming: Naming
    if name == {"kind": "pending"}:
        naming = PendingName()
    elif name == {"kind": "applied"}:
        naming = AppliedName()
    elif name.get("kind") == "failed" and set(name) == {"kind", "detail"}:
        naming = FailedName(string(name.get("detail"), "rename failure"))
    else:
        raise HiveError(ErrorCode.INVALID_RECORD, "Unknown naming outcome")
    return Session(
        bead_id(raw.get("id")),
        CodexTaskId(string(data.get("task"), "native task")),
        ProjectId(string(data.get("project"), "project")),
        focus,
        naming,
    )


def sessions(records: tuple[dict[str, object], ...]) -> tuple[Session, ...]:
    result = tuple(
        decode(raw)
        for raw in records
        if "hive_session" in record(raw.get("metadata", {}))
    )
    if len({s.task for s in result}) != len(result):
        raise HiveError(ErrorCode.INVALID_RECORD, "Duplicate enrolled native task")
    return result


def value(session: Session) -> dict[str, object]:
    return {
        "id": session.id,
        **record(
            metadata(session.task, session.project, session.focus, session.naming)[
                "hive_session"
            ]
        ),
        "title": session.focus.title,
        "rename_required": not isinstance(session.naming, AppliedName),
    }
