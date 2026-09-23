"""Validate bd list JSON, including its hydrated native dependency edges."""

import re
from datetime import datetime

from hive.errors import ErrorCode, HiveError
from hive.identity import BeadId, ProjectId
from hive.jsonvalue import integer, record, sequence, string
from hive.model import (
    ArtifactDelivery,
    Bead,
    Deferred,
    Done,
    Drafting,
    Draining,
    Owned,
    Queued,
    ReviewingArtifact,
    Settled,
    WorkKind,
)
from hive.state_json import NativeState, decode_state, encode_state


def bead_id(value: object) -> BeadId:
    identifier = string(value, "bead ID")
    if not re.fullmatch(r"hv-[a-z0-9]+(?:\.[a-z0-9]+)*", identifier):
        raise HiveError(ErrorCode.INVALID_RECORD, "Expected a native hv- bead ID")
    return BeadId(identifier)


def decode_bead(value: object) -> Bead:
    data = record(value, "bead")
    metadata = record(
        record(data.get("metadata"), "metadata").get("hive"), "Hive metadata"
    )
    identifier = bead_id(data.get("id"))
    # Configuration and registry records use Beads infrastructure types and are
    # decoded elsewhere. They must never accidentally count as work items.
    if data.get("issue_type") not in {
        "task",
        "bug",
        "feature",
        "epic",
        "chore",
        "decision",
    }:
        raise HiveError(ErrorCode.INVALID_RECORD, "Not an implementation bead")
    priority = integer(data.get("priority"), "priority")
    if priority > 4:
        raise HiveError(ErrorCode.INVALID_RECORD, "Priority must be P0 through P4")
    try:
        created = datetime.fromisoformat(
            string(data.get("created_at"), "creation time")
        )
        kind = WorkKind(string(metadata.get("kind"), "work kind"))
    except ValueError as error:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Invalid creation time or work kind"
        ) from error
    if created.tzinfo is None:
        raise HiveError(ErrorCode.INVALID_RECORD, "Creation time has no timezone")
    dependencies: list[BeadId] = []
    edges = sequence(data.get("dependencies", []), "dependencies")
    if integer(data.get("dependency_count"), "dependency count") != len(edges):
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Native dependencies were not fully hydrated"
        )
    for edge in edges:
        dependency = record(edge, "dependency")
        if (
            dependency.get("type") != "blocks"
            or dependency.get("issue_id") != identifier
        ):
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Unsupported native dependency edge"
            )
        dependencies.append(bead_id(dependency.get("depends_on_id")))
    state = decode_state(
        string(data.get("status"), "status"),
        string(data.get("assignee", ""), "assignee", empty=True),
        metadata,
    )
    phase = None
    if isinstance(state, Owned):
        phase = state.phase
    elif isinstance(state, (Queued, Deferred)):
        work = state.work
        if isinstance(work, (Settled, Draining)):
            phase = work.phase
    if phase is not None and isinstance(phase, (Drafting, ReviewingArtifact)) != (
        kind == WorkKind.ARTIFACT
    ):
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Work kind contradicts execution phase"
        )
    if isinstance(state, Done) and isinstance(state.delivery, ArtifactDelivery) != (
        kind == WorkKind.ARTIFACT
    ):
        raise HiveError(ErrorCode.INVALID_RECORD, "Work kind contradicts delivery")
    return Bead(
        identifier,
        ProjectId(string(metadata.get("project"), "project")),
        string(data.get("title"), "title"),
        string(data.get("description", ""), "description", empty=True),
        string(data.get("acceptance_criteria", ""), "acceptance", empty=True),
        priority,
        created,
        tuple(dependencies),
        state,
        kind,
    )


def encode_lifecycle(bead: Bead) -> NativeState:
    """Only the Hive metadata subtree is replaced; preserve other native fields."""
    native = encode_state(bead.state)
    metadata = {**native.metadata, "project": bead.project, "kind": bead.kind}
    return NativeState(native.status, native.assignee, {"hive": metadata})
