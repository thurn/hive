"""The narrow JSON boundary for retained execution resources."""

from pathlib import Path
from typing import assert_never

from hive.errors import ErrorCode, HiveError
from hive.identity import CandidateId, SourceCommit, WorktreePath
from hive.jsonvalue import record, string
from hive.model import (
    Drafting,
    Implementing,
    Phase,
    Preparing,
    Reviewing,
    ReviewingArtifact,
    WaitingForDelivery,
)


def decode_phase(value: object) -> Phase:
    data = record(value, "phase")
    name = string(data.get("kind"), "phase kind")
    fields = {
        "drafting": {"kind"},
        "reviewing-artifact": {"kind", "location"},
        "preparing": {"kind", "branch"},
        "implementing": {"kind", "workspace"},
        "reviewing": {"kind", "workspace", "source"},
        "waiting-for-delivery": {"kind", "workspace", "source", "candidate"},
    }
    if set(data) != fields.get(name):
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Unknown or inconsistent phase fields"
        )
    if name == "drafting":
        return Drafting()
    if name == "reviewing-artifact":
        return ReviewingArtifact(string(data.get("location"), "artifact location"))
    if name == "preparing":
        return Preparing(string(data.get("branch"), "intended branch"))
    path = Path(string(data.get("workspace"), "workspace"))
    if not path.is_absolute():
        raise HiveError(ErrorCode.INVALID_RECORD, "Workspace must be absolute")
    workspace = WorktreePath(path)
    if name == "implementing":
        return Implementing(workspace)
    source = SourceCommit(string(data.get("source"), "source commit"))
    if name == "reviewing":
        return Reviewing(workspace, source)
    if name == "waiting-for-delivery":
        return WaitingForDelivery(
            workspace,
            source,
            CandidateId(string(data.get("candidate"), "candidate")),
        )
    raise HiveError(ErrorCode.INVALID_RECORD, f"Unknown phase: {name}")


def encode_phase(phase: Phase) -> dict[str, object]:
    if isinstance(phase, Preparing):
        return {"kind": "preparing", "branch": phase.branch}
    if isinstance(phase, Drafting):
        return {"kind": "drafting"}
    if isinstance(phase, ReviewingArtifact):
        return {"kind": "reviewing-artifact", "location": phase.location}
    if isinstance(phase, Implementing):
        return {"kind": "implementing", "workspace": str(phase.workspace)}
    if isinstance(phase, Reviewing):
        return {
            "kind": "reviewing",
            "workspace": str(phase.workspace),
            "source": phase.source,
        }
    if isinstance(phase, WaitingForDelivery):
        return {
            "kind": "waiting-for-delivery",
            "workspace": str(phase.workspace),
            "source": phase.source,
            "candidate": phase.candidate,
        }
    assert_never(phase)
