"""Native status and assignee remain authoritative; metadata supplies structure."""

from dataclasses import dataclass
from typing import assert_never

from hive.errors import ErrorCode, HiveError
from hive.identity import BeadId, CandidateId, CodexTaskId, CodexTurnId, SourceCommit
from hive.jsonvalue import record, sequence, string
from hive.model import (
    ArtifactDelivery,
    Cancelled,
    CodeDelivery,
    Deferred,
    Done,
    Draining,
    Owned,
    Owner,
    PauseReason,
    Queued,
    Settled,
    State,
    Unstarted,
)
from hive.phase_json import decode_phase, encode_phase


@dataclass(frozen=True)
class NativeState:
    status: str
    assignee: str
    metadata: dict[str, object]


def decode_state(status: str, assignee: str, data: dict[str, object]) -> State:
    phase = decode_phase(data["phase"]) if "phase" in data else None
    turn = data.get("turn")
    pause = data.get("pause")
    outcome = data.get("outcome")
    pending = tuple(
        BeadId(string(value, "pending prerequisite"))
        for value in sequence(
            data.get("pending_dependencies", []), "pending dependencies"
        )
    )
    if pending and status != "deferred":
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Unfinished dependency edits require deferral"
        )
    owner = None
    if assignee:
        owner = Owner(CodexTaskId(assignee), CodexTurnId(string(turn, "owning turn")))
    elif turn is not None:
        raise HiveError(ErrorCode.INVALID_RECORD, "Unassigned work retains a turn")
    if status == "closed":
        if owner is not None or phase is not None or pause is not None:
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Closed work retains execution state"
            )
        terminal = record(outcome, "terminal outcome")
        kind = string(terminal.get("kind"), "outcome kind")
        if kind == "cancelled":
            return Cancelled(string(terminal.get("reason"), "cancellation reason"))
        summary = string(terminal.get("summary"), "completion summary")
        if kind == "code":
            return Done(
                summary,
                CodeDelivery(
                    SourceCommit(string(terminal.get("source"), "delivered source")),
                    CandidateId(
                        string(terminal.get("candidate"), "delivered candidate")
                    ),
                ),
            )
        if kind == "artifact":
            return Done(
                summary, ArtifactDelivery(string(terminal.get("location"), "artifact"))
            )
        raise HiveError(ErrorCode.INVALID_RECORD, "Unknown terminal outcome")
    if outcome is not None:
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Unfinished work has a terminal outcome"
        )
    if status == "deferred":
        details = record(pause, "pause")
        try:
            reason = PauseReason(string(details.get("reason"), "pause reason"))
        except ValueError as error:
            raise HiveError(ErrorCode.INVALID_RECORD, "Unknown pause reason") from error
        note = string(details.get("note"), "pause note")
        if owner is not None:
            if phase is None:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Unsettled owner has no phase"
                )
            return Deferred(reason, note, Draining(owner, phase), pending)
        return Deferred(
            reason, note, Unstarted() if phase is None else Settled(phase), pending
        )
    if pause is not None:
        raise HiveError(ErrorCode.INVALID_RECORD, "Runnable work retains a pause")
    if status == "in_progress" and owner is not None and phase is not None:
        return Owned(owner, phase)
    if status == "open" and owner is None:
        return Queued(Unstarted() if phase is None else Settled(phase))
    raise HiveError(ErrorCode.INVALID_RECORD, "Status and ownership are inconsistent")


def encode_state(state: State) -> NativeState:
    data: dict[str, object] = {}
    if isinstance(state, Queued):
        if isinstance(state.work, Settled):
            data["phase"] = encode_phase(state.work.phase)
        return NativeState("open", "", data)
    if isinstance(state, Owned):
        return NativeState(
            "in_progress",
            state.owner.task,
            {
                "turn": state.owner.turn,
                "phase": encode_phase(state.phase),
            },
        )
    if isinstance(state, Deferred):
        data["pause"] = {"reason": state.reason, "note": state.note}
        if state.pending_dependencies:
            data["pending_dependencies"] = list(state.pending_dependencies)
        work = state.work
        if isinstance(work, Unstarted):
            return NativeState("deferred", "", data)
        data["phase"] = encode_phase(work.phase)
        if isinstance(work, Draining):
            data["turn"] = work.owner.turn
            return NativeState("deferred", work.owner.task, data)
        return NativeState("deferred", "", data)
    if isinstance(state, Cancelled):
        return NativeState(
            "closed", "", {"outcome": {"kind": "cancelled", "reason": state.reason}}
        )
    if isinstance(state, Done):
        terminal: dict[str, object] = {"summary": state.summary}
        delivery = state.delivery
        if isinstance(delivery, CodeDelivery):
            terminal.update(
                kind="code", source=delivery.source, candidate=delivery.candidate
            )
        elif isinstance(delivery, ArtifactDelivery):
            terminal.update(kind="artifact", location=delivery.location)
        else:
            assert_never(delivery)
        return NativeState("closed", "", {"outcome": terminal})
    assert_never(state)
