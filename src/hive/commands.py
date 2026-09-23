"""Immutable command requests shared by CLI and future native adapters."""

from dataclasses import dataclass

from hive.beads_store import NewTask
from hive.configuration import Project
from hive.identity import BeadId, CandidateId, CodexTaskId, ProjectId
from hive.model import Capacity, Delivery, Owner, PauseReason, Phase
from hive.session import AppliedName, FailedName, Focus


@dataclass(frozen=True)
class SourceRequest:
    pass


@dataclass(frozen=True)
class Initialize:
    pass


@dataclass(frozen=True)
class Register:
    project: Project


@dataclass(frozen=True)
class SetCapacity:
    capacity: Capacity


@dataclass(frozen=True)
class Status:
    project: ProjectId | None = None


@dataclass(frozen=True)
class Ready:
    project: ProjectId


@dataclass(frozen=True)
class Show:
    bead: BeadId


@dataclass(frozen=True)
class Add:
    task: NewTask
    dependencies: tuple[BeadId, ...]


@dataclass(frozen=True)
class Claim:
    project: ProjectId
    owner: Owner
    bead: BeadId | None = None


@dataclass(frozen=True)
class Advance:
    owner: Owner
    phase: Phase


@dataclass(frozen=True)
class Defer:
    reason: PauseReason
    note: str


@dataclass(frozen=True)
class Resume:
    reason: PauseReason | None
    user_authorized: bool


@dataclass(frozen=True)
class Settle:
    owner: Owner


@dataclass(frozen=True)
class Complete:
    owner: Owner
    summary: str
    delivery: Delivery


@dataclass(frozen=True)
class Cancel:
    reason: str


@dataclass(frozen=True)
class EnterTurn:
    previous: Owner
    current: Owner


type Transition = Advance | Defer | Resume | Settle | Complete | Cancel | EnterTurn


@dataclass(frozen=True)
class Change:
    bead: BeadId
    project: ProjectId
    transition: Transition


@dataclass(frozen=True)
class Dependency:
    bead: BeadId
    project: ProjectId
    prerequisite: BeadId
    remove: bool


@dataclass(frozen=True)
class Prioritize:
    bead: BeadId
    project: ProjectId
    priority: int


@dataclass(frozen=True)
class CreateWorkspace:
    bead: BeadId
    project: ProjectId
    owner: Owner


@dataclass(frozen=True)
class SubmitWork:
    bead: BeadId
    project: ProjectId
    owner: Owner


@dataclass(frozen=True)
class ApproveWork:
    bead: BeadId
    project: ProjectId
    owner: Owner


type ExternalRequest = CreateWorkspace | SubmitWork | ApproveWork


@dataclass(frozen=True)
class WaitDelivery:
    candidate: CandidateId
    project: ProjectId
    timeout: int = 3600


@dataclass(frozen=True)
class InspectDelivery:
    candidate: CandidateId
    project: ProjectId


@dataclass(frozen=True)
class EnterSession:
    task: CodexTaskId
    project: ProjectId
    focus: Focus
    inline_bead: bool


@dataclass(frozen=True)
class RecordName:
    task: CodexTaskId
    title: str
    result: AppliedName | FailedName


@dataclass(frozen=True)
class ListSessions:
    project: ProjectId | None


type Request = (
    SourceRequest
    | Initialize
    | Register
    | SetCapacity
    | Status
    | Ready
    | Show
    | Add
    | Claim
    | Change
    | Dependency
    | Prioritize
    | ExternalRequest
    | WaitDelivery
    | InspectDelivery
    | EnterSession
    | RecordName
    | ListSessions
)


def mutates(request: Request) -> bool:
    return not isinstance(
        request,
        (
            SourceRequest,
            Status,
            Ready,
            Show,
            WaitDelivery,
            InspectDelivery,
            ListSessions,
        ),
    )
