"""Replay native ownership transitions; never infer work from creator links."""

from dataclasses import dataclass
from datetime import datetime

from hive.bead_events import Event
from hive.identity import ThreadId
from hive.thread_links import thread_id

NON_OWNERSHIP: frozenset[str] = frozenset({"renamed", "label_added", "label_removed"})


@dataclass(frozen=True)
class State:
    status: str
    assignee: str


@dataclass(frozen=True)
class Interval:
    thread: ThreadId
    start: datetime
    end: datetime | None


@dataclass(frozen=True)
class Replay:
    state: State
    intervals: tuple[Interval, ...]
    error: str | None


def replay(events: tuple[Event, ...]) -> Replay:
    state: State = State("open", "")
    intervals: list[Interval] = []
    opened: tuple[ThreadId, datetime] | None = None

    def failed(reason: str) -> Replay:
        return Replay(state, (), reason)

    if not events or events[0].kind != "created":
        return failed("Missing creation event")
    # The first before-state establishes an already-assigned creation, including
    # a bead created directly in progress; current assignee is never backdated.
    first_change = next(
        (event for event in events[1:] if event.kind not in NON_OWNERSHIP), None
    )
    if first_change is not None and first_change.kind in {
        "claimed",
        "updated",
        "status_changed",
    }:
        if first_change.old_assignee:
            state = State(first_change.old_status, first_change.old_assignee)
    for index, event in enumerate(events):
        if event.error is not None or event.occurred is None:
            return failed(event.error or "Missing event time")
        if index == 0:
            owner = thread_id(state.assignee)
            if state.status == "in_progress" and owner is not None:
                opened = owner, event.occurred
            continue
        if event.kind in {"claimed", "updated", "status_changed"} or (
            event.kind == "reopened" and event.old_status
        ):
            if State(event.old_status, event.old_assignee) != state:
                return failed("Event before-state disagrees with replay")
        if event.kind in NON_OWNERSHIP:
            if event.kind != "renamed" and (
                event.old_status
                or event.old_assignee
                or event.new_status is not None
                or event.new_assignee is not None
            ):
                return failed("Label event unexpectedly changes ownership")
            next_state = state
        elif event.kind == "closed":
            next_state = State("closed", state.assignee)
        elif event.kind in {"claimed", "updated", "status_changed", "reopened"}:
            if event.kind == "claimed" and (
                event.new_status is None or event.new_assignee is None
            ):
                return failed("Incomplete claimed event")
            next_state = State(
                (
                    event.new_status
                    if event.new_status is not None
                    else ("open" if event.kind == "reopened" else state.status)
                ),
                state.assignee if event.new_assignee is None else event.new_assignee,
            )
        else:
            return failed("Unsupported ownership event: " + event.kind)
        if next_state != state:
            if opened is not None:
                owner, start = opened
                if event.occurred > start:
                    intervals.append(Interval(owner, start, event.occurred))
                opened = None
            owner = thread_id(next_state.assignee)
            if next_state.status == "in_progress" and owner is not None:
                opened = owner, event.occurred
        state = next_state
    if opened is not None:
        owner, start = opened
        intervals.append(Interval(owner, start, None))
    return Replay(state, tuple(intervals), None)
