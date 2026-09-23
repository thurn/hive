"""Native Tollgate values, distinct from Hive task ownership and lifecycle."""

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.identity import CandidateId, SourceCommit, WorktreePath
from hive.jsonvalue import record, string


class CandidateState(StrEnum):
    CONSTRUCTING = "constructing"
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    READY = "ready"
    PROMOTING = "promoting"
    PUSH_PENDING = "promoted-local-push-pending"
    PROMOTED = "promoted"
    EXTERNALLY_INTEGRATED = "externally-integrated"
    FAILED = "failed"
    MERGE_CONFLICT = "merge-conflict"
    DEPENDENCY_FAILED = "dependency-failed"
    CANCELED = "canceled"
    SUPERSEDED = "superseded"
    INFRASTRUCTURE_EXHAUSTED = "infrastructure-exhausted"
    CHECK_PASSED = "check-passed"
    CHECK_FAILED = "check-failed"


class RemoteState(StrEnum):
    DISABLED = "disabled"
    PREFLIGHT_PENDING = "preflight-pending"
    READY = "ready"
    PUSHING = "pushing"
    PUSH_BLOCKED = "push-blocked"
    SYNCHRONIZED = "synchronized"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class Candidate:
    id: CandidateId
    source: SourceCommit
    state: CandidateState
    remote: RemoteState
    authorized: bool
    reason: str | None


@dataclass(frozen=True)
class Workspace:
    path: WorktreePath
    branch: str
    base: SourceCommit


@dataclass(frozen=True)
class Approval:
    candidate: CandidateId
    source: SourceCommit
    already_authorized: bool


def source_oid(value: object) -> SourceCommit:
    data = record(value, "Git object ID")
    oid = string(data.get("bytes"), "Git object ID bytes")
    length = {"sha1": 40, "sha256": 64}.get(str(data.get("format")))
    if length is None or len(oid) != length or re.fullmatch(r"[0-9a-f]+", oid) is None:
        raise HiveError(ErrorCode.INVALID_RECORD, "Invalid native Git object ID")
    return SourceCommit(oid)


def decode_candidate(value: object, expected: CandidateId) -> Candidate:
    data = record(record(value, "candidate status").get("item"), "candidate")
    if data.get("id") != expected:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Tollgate returned a different candidate"
        )
    authorized = data.get("promotion_authorized")
    if not isinstance(authorized, bool):
        raise HiveError(ErrorCode.INVALID_RECORD, "Missing promotion authority")
    reason = data.get("terminal_reason")
    try:
        return Candidate(
            expected,
            source_oid(data.get("source_oid")),
            CandidateState(string(data.get("state"), "candidate state")),
            RemoteState(string(data.get("remote_state"), "remote state")),
            authorized,
            None if reason is None else string(reason, "terminal reason"),
        )
    except ValueError as error:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Unknown Tollgate candidate or remote state"
        ) from error


def decode_workspace(value: object, branch: str) -> Workspace:
    data = record(value, "workspace result")
    path = Path(string(data.get("path"), "workspace path"))
    if (
        data.get("action") != "created"
        or data.get("branch") != branch
        or not path.is_absolute()
    ):
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Unexpected workspace creation result"
        )
    return Workspace(WorktreePath(path), branch, source_oid(data.get("new_oid")))
