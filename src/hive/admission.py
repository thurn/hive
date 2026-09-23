"""Decide admission from a fresh locked snapshot, without scheduling workers."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from hive.errors import ErrorCode, HiveError
from hive.identity import BeadId, ProjectId
from hive.model import (
    Bead,
    Capacity,
    Deferred,
    Done,
    Drafting,
    Owned,
    Owner,
    Preparing,
    Queued,
    Settled,
    WorkKind,
    owner_of,
)


def blockers(bead: Bead, prerequisites: Mapping[BeadId, Bead]) -> tuple[BeadId, ...]:
    result: list[BeadId] = []
    for identifier in bead.dependencies:
        prerequisite = prerequisites.get(identifier)
        if prerequisite is None or not isinstance(prerequisite.state, Done):
            result.append(identifier)
    return tuple(result)


def admit(
    bead: Bead,
    owner: Owner,
    project: ProjectId,
    active: Sequence[Bead],
    prerequisites: Mapping[BeadId, Bead],
    capacity: Capacity,
) -> Bead:
    """Apply identical predicates to both explicit and next-bead claims."""
    if bead.project != project:
        raise HiveError(
            ErrorCode.INVALID_INPUT, "Bead is outside the executor's project"
        )
    if not isinstance(bead.state, Queued):
        code = (
            ErrorCode.PAUSED
            if isinstance(bead.state, Deferred)
            else (
                ErrorCode.ALREADY_OWNED
                if isinstance(bead.state, Owned)
                else ErrorCode.INVALID_INPUT
            )
        )
        raise HiveError(code, "Only queued work can be claimed")
    owned = [item for item in active if owner_of(item.state) is not None]
    for item in owned:
        existing = owner_of(item.state)
        if existing is not None and existing.task == owner.task:
            raise HiveError(
                ErrorCode.ALREADY_OWNED, f"This task already owns {item.id}"
            )
    if len(owned) >= capacity.global_limit or sum(
        item.project == project for item in owned
    ) >= capacity.for_project(project):
        raise HiveError(ErrorCode.CAPACITY_FULL, "Execution capacity is full")
    pending = blockers(bead, prerequisites)
    if pending:
        raise HiveError(
            ErrorCode.DEPENDENCY_BLOCKED, "Waiting for " + ", ".join(pending)
        )
    phase = (
        bead.state.work.phase
        if isinstance(bead.state.work, Settled)
        else (
            Drafting()
            if bead.kind == WorkKind.ARTIFACT
            else Preparing(f"codex/{bead.id}")
        )
    )
    return replace(bead, state=Owned(owner, phase))


def next_ready(
    project: ProjectId,
    active: Sequence[Bead],
    prerequisites: Mapping[BeadId, Bead],
) -> Bead:
    candidates = (
        bead
        for bead in active
        if bead.project == project
        and isinstance(bead.state, Queued)
        and not blockers(bead, prerequisites)
    )
    bead = min(
        candidates,
        key=lambda item: (item.priority, item.created, item.id),
        default=None,
    )
    if bead is None:
        raise HiveError(ErrorCode.NO_READY_WORK, f"No ready work in {project}")
    return bead


def require_acyclic(
    dependent: BeadId, prerequisite: BeadId, edges: Mapping[BeadId, tuple[BeadId, ...]]
) -> None:
    """Native dependency writes still use Beads' own cycle detection."""
    pending = [prerequisite]
    visited: set[BeadId] = set()
    while pending:
        current = pending.pop()
        if current == dependent:
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Dependency would introduce a cycle"
            )
        if current not in visited:
            visited.add(current)
            pending.extend(edges.get(current, ()))
