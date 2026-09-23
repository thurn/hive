"""Immutable task states; fields exist only in states that can use them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import assert_never

from hive.identity import (
    BeadId,
    CandidateId,
    CodexTaskId,
    CodexTurnId,
    ProjectId,
    SourceCommit,
    WorktreePath,
)


@dataclass(frozen=True)
class Owner:
    task: CodexTaskId
    turn: CodexTurnId


@dataclass(frozen=True)
class Preparing:
    branch: str


@dataclass(frozen=True)
class Implementing:
    workspace: WorktreePath


@dataclass(frozen=True)
class Reviewing:
    workspace: WorktreePath
    source: SourceCommit


@dataclass(frozen=True)
class WaitingForDelivery:
    workspace: WorktreePath
    source: SourceCommit
    candidate: CandidateId


@dataclass(frozen=True)
class Drafting:
    pass


@dataclass(frozen=True)
class ReviewingArtifact:
    location: str


type Phase = (
    Preparing
    | Implementing
    | Reviewing
    | WaitingForDelivery
    | Drafting
    | ReviewingArtifact
)


@dataclass(frozen=True)
class Unstarted:
    pass


@dataclass(frozen=True)
class Settled:
    phase: Phase


@dataclass(frozen=True)
class Queued:
    work: Unstarted | Settled = Unstarted()


@dataclass(frozen=True)
class Owned:
    owner: Owner
    phase: Phase


class PauseReason(StrEnum):
    USER = "user-pause"
    APPROVAL = "design-approval"
    INPUT = "needs-input"
    CHECKPOINT = "checkpoint"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class Draining:
    owner: Owner
    phase: Phase


type RetainedWork = Unstarted | Settled | Draining


@dataclass(frozen=True)
class Deferred:
    reason: PauseReason
    note: str
    work: RetainedWork
    pending_dependencies: tuple[BeadId, ...] = ()


@dataclass(frozen=True)
class CodeDelivery:
    source: SourceCommit
    candidate: CandidateId


@dataclass(frozen=True)
class ArtifactDelivery:
    location: str


type Delivery = CodeDelivery | ArtifactDelivery


@dataclass(frozen=True)
class Done:
    summary: str
    delivery: Delivery


@dataclass(frozen=True)
class Cancelled:
    reason: str


type State = Queued | Owned | Deferred | Done | Cancelled


class WorkKind(StrEnum):
    CODE = "code"
    ARTIFACT = "artifact"


@dataclass(frozen=True)
class Bead:
    id: BeadId
    project: ProjectId
    title: str
    description: str
    acceptance: str
    priority: int
    created: datetime
    dependencies: tuple[BeadId, ...]
    state: State
    kind: WorkKind = WorkKind.CODE


@dataclass(frozen=True)
class Capacity:
    global_limit: int = 8
    project_limits: tuple[tuple[ProjectId, int], ...] = ()

    def for_project(self, project: ProjectId) -> int:
        return next(
            (limit for key, limit in self.project_limits if key == project),
            self.global_limit,
        )


def owner_of(state: State) -> Owner | None:
    """Deferred work with unsettled writers still owns its capacity slot."""
    if isinstance(state, Owned):
        return state.owner
    if isinstance(state, Deferred):
        return state.work.owner if isinstance(state.work, Draining) else None
    if isinstance(state, (Queued, Done, Cancelled)):
        return None
    assert_never(state)
