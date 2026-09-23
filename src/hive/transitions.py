"""Explicit transitions; persistence and external effects belong to adapters."""

from dataclasses import replace

from hive.errors import ErrorCode, HiveError
from hive.model import (
    ArtifactDelivery,
    Bead,
    Cancelled,
    CodeDelivery,
    Deferred,
    Delivery,
    Done,
    Drafting,
    Draining,
    Implementing,
    Owned,
    Owner,
    PauseReason,
    Phase,
    Preparing,
    Queued,
    Reviewing,
    ReviewingArtifact,
    Settled,
    WaitingForDelivery,
    owner_of,
)


def require_owner(bead: Bead, owner: Owner) -> None:
    if owner_of(bead.state) != owner:
        raise HiveError(ErrorCode.STALE_OWNER, f"{bead.id} is not owned by this turn")


def enter_turn(bead: Bead, previous: Owner, current: Owner) -> Bead:
    """Replace the recorded turn only while the same task still owns work."""
    require_owner(bead, previous)
    if current.task != previous.task:
        raise HiveError(ErrorCode.STALE_OWNER, "A new task must use recovery")
    if not isinstance(bead.state, Owned):
        raise HiveError(ErrorCode.PAUSED, f"{bead.id} is deferred")
    return replace(bead, state=Owned(current, bead.state.phase))


def advance(bead: Bead, owner: Owner, phase: Phase) -> Bead:
    require_owner(bead, owner)
    if not isinstance(bead.state, Owned):
        raise HiveError(ErrorCode.PAUSED, f"{bead.id} is deferred")
    old = bead.state.phase
    allowed = False
    match old, phase:
        case Preparing(), Implementing():
            allowed = True
        case Implementing(workspace=a), Reviewing(workspace=b):
            allowed = a == b
        case Reviewing(workspace=a), Implementing(workspace=b):
            allowed = a == b
        case Reviewing(workspace=a, source=x), WaitingForDelivery(
            workspace=b, source=y
        ):
            allowed = a == b and x == y
        case WaitingForDelivery(workspace=a), Implementing(workspace=b):
            allowed = a == b
        case Drafting(), ReviewingArtifact(location=location):
            allowed = bool(location.strip())
        case ReviewingArtifact(), Drafting():
            allowed = True
    if not allowed:
        raise HiveError(
            ErrorCode.INVALID_INPUT, "Invalid phase or workspace transition"
        )
    return replace(bead, state=Owned(owner, phase))


def defer(bead: Bead, reason: PauseReason, note: str) -> Bead:
    """Revocation does not pretend that the previous owner's resources stopped."""
    state = bead.state
    if isinstance(state, Queued):
        return replace(bead, state=Deferred(reason, note, state.work))
    if isinstance(state, Owned):
        return replace(
            bead, state=Deferred(reason, note, Draining(state.owner, state.phase))
        )
    if isinstance(state, Deferred):
        if (
            state.reason in {PauseReason.USER, PauseReason.APPROVAL}
            and reason != state.reason
        ):
            raise HiveError(ErrorCode.PAUSED, "Cannot replace a protected pause")
        return replace(bead, state=Deferred(reason, note, state.work))
    raise HiveError(ErrorCode.INVALID_INPUT, "Terminal work cannot be deferred")


def settle(bead: Bead, owner: Owner) -> Bead:
    require_owner(bead, owner)
    state = bead.state
    if not isinstance(state, Deferred) or not isinstance(state.work, Draining):
        raise HiveError(
            ErrorCode.INVALID_INPUT, "Defer work before releasing its owner"
        )
    return replace(bead, state=replace(state, work=Settled(state.work.phase)))


def resume(bead: Bead, *, user_authorized: bool = False) -> Bead:
    state = bead.state
    if not isinstance(state, Deferred):
        raise HiveError(ErrorCode.INVALID_INPUT, "Only deferred work can resume")
    work = state.work
    if isinstance(work, Draining):
        raise HiveError(ErrorCode.RECOVERY_REQUIRED, "Resources have not settled")
    if state.reason in {PauseReason.USER, PauseReason.APPROVAL} and not user_authorized:
        raise HiveError(
            ErrorCode.PAUSED, "Explicit user resumption or approval is required"
        )
    return replace(bead, state=Queued(work))


def recover(bead: Bead, observed_owner: Owner, note: str) -> Bead:
    """Caller must inspect native execution and settle writers before this CAS."""
    require_owner(bead, observed_owner)
    state = bead.state
    if isinstance(state, Deferred):
        if state.reason == PauseReason.USER:
            raise HiveError(ErrorCode.PAUSED, "Recovery cannot override a user pause")
        if isinstance(state.work, Draining):
            return replace(bead, state=replace(state, work=Settled(state.work.phase)))
    if isinstance(state, Owned):
        return replace(
            bead, state=Deferred(PauseReason.RECOVERY, note, Settled(state.phase))
        )
    raise HiveError(ErrorCode.INVALID_INPUT, "No recoverable owner")


def complete(bead: Bead, owner: Owner, summary: str, delivery: Delivery) -> Bead:
    require_owner(bead, owner)
    state = bead.state
    if not isinstance(state, Owned):
        raise HiveError(
            ErrorCode.PAUSED, "Deferred work cannot be completed by its owner"
        )
    if not summary.strip():
        raise HiveError(ErrorCode.INVALID_INPUT, "Completion needs an outcome summary")
    if isinstance(delivery, CodeDelivery):
        phase = state.phase
        if not isinstance(phase, WaitingForDelivery) or (
            phase.candidate != delivery.candidate or phase.source != delivery.source
        ):
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Delivery does not match this candidate"
            )
    elif isinstance(delivery, ArtifactDelivery):
        if not delivery.location.strip():
            raise HiveError(ErrorCode.INVALID_INPUT, "Artifact location is required")
        if (
            not isinstance(state.phase, ReviewingArtifact)
            or state.phase.location != delivery.location
        ):
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Delivery does not match this artifact"
            )
    return replace(bead, state=Done(summary, delivery))


def cancel(bead: Bead, reason: str) -> Bead:
    if owner_of(bead.state) is not None:
        raise HiveError(
            ErrorCode.RECOVERY_REQUIRED, "Settle owned resources before cancelling"
        )
    if isinstance(bead.state, (Done, Cancelled)):
        raise HiveError(ErrorCode.INVALID_INPUT, "Terminal outcomes are immutable")
    if not reason.strip():
        raise HiveError(ErrorCode.INVALID_INPUT, "Cancellation requires a reason")
    return replace(bead, state=Cancelled(reason))
