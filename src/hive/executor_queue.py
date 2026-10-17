"""One project's queue as peer executors share it; read-only, never a dispatcher.

A project may be split into streams: a bead whose metadata names `hive_stream`
belongs to that stream, and every other bead to the unnamed default stream.
Ready and waiting work is read within the session's stream; held work and the
session's own assignments stay project-wide, so a blocker another stream holds
keeps a wait pending instead of draining it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from hive.beads_process import BeadsProcess
from hive.errors import HiveError
from hive.jsonvalue import record, sequence, string

UNFINISHED: frozenset[str] = frozenset({"open", "in_progress", "blocked"})
WAIT_TIMEOUT_SECONDS: float = 3900


@dataclass(frozen=True)
class Peers:
    """Unfinished project work other sessions hold, and the unclaimed rest.

    Readiness edges decide which waiting beads a held bead actually blocks, so
    any held bead with waiting work beside it is reason to wait, not to drain.
    """

    held: tuple[str, ...]
    waiting: tuple[str, ...]

    @property
    def pending(self) -> bool:
        return bool(self.held) and bool(self.waiting)


def ids(
    value: object, project: str, owner: str | None, stream: str = ""
) -> tuple[str, ...]:
    """Unclaimed open tasks of one stream (owner None) or one owner's unfinished
    beads in any stream."""
    if owner is None:
        return tuple(
            sorted(
                string(bead.get("id"), "bead id")
                for bead in claimable(value, project, stream)
            )
        )
    result: list[str] = []
    for raw in sequence(value, "beads"):
        bead = record(raw, "bead")
        if not _in_project(bead, project):
            continue
        if _assignee(bead) != owner or bead.get("status") not in UNFINISHED:
            continue
        result.append(string(bead.get("id"), "bead id"))
    return tuple(sorted(result))


def claimable(
    value: object, project: str, stream: str
) -> tuple[dict[str, object], ...]:
    """Unclaimed open non-epic beads of one project stream, in native order."""
    result: list[dict[str, object]] = []
    for raw in sequence(value, "beads"):
        bead = record(raw, "bead")
        if not _in_project(bead, project) or not _in_stream(bead, stream):
            continue
        if (
            bead.get("status") != "open"
            or _assignee(bead)
            or bead.get("issue_type") == "epic"
        ):
            continue
        result.append(bead)
    return tuple(result)


def peers(
    process: BeadsProcess,
    project: str,
    session: str,
    ready: tuple[str, ...],
    stream: str,
) -> Peers:
    held: list[str] = []
    waiting: list[str] = []
    for raw in sequence(process.unfinished(project, session), "beads"):
        bead = record(raw, "bead")
        if not _in_project(bead, project) or bead.get("issue_type") == "epic":
            continue
        identifier = string(bead.get("id"), "bead id")
        status = bead.get("status")
        owner = _assignee(bead)
        if owner:
            if owner != session and status in UNFINISHED:
                held.append(identifier)
        elif (
            status in {"open", "blocked"}
            and identifier not in ready
            and _in_stream(bead, stream)
        ):
            waiting.append(identifier)
    return Peers(tuple(sorted(held)), tuple(sorted(waiting)))


def await_work(
    process: BeadsProcess,
    project: str,
    session: str,
    *,
    timeout: float,
    interval: float,
    active: Callable[[], bool],
    stream: str,
) -> dict[str, object]:
    """Poll until this session has work, peers hold nothing it could take next,
    the session is disarmed or re-armed, or the deadline passes.

    Each Beads call is bounded by the remaining time (at least one second), so a
    result arrives within a few seconds of the deadline. An outage is retried until the deadline and
    reported with the timeout.
    """
    deadline = time.monotonic() + timeout
    error: str | None = None
    result: dict[str, object] | None = None
    while True:
        found = Peers((), ())
        result = None
        if active():
            try:
                result, found = _poll(process, project, session, deadline, stream)
                error = None
            except HiveError as failure:
                error = str(failure)
        # New input during a slow read wins over whatever the read found.
        if not active():
            return _scoped({"code": "ExecutorInactive", "project": project}, stream)
        if result is not None:
            return _scoped(result, stream)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out: dict[str, object] = {
                "code": "ExecutorAwaitTimeout",
                "project": project,
                "held": list(found.held),
                "waiting": list(found.waiting),
            }
            if error is not None:
                timed_out["error"] = error
            return _scoped(timed_out, stream)
        time.sleep(min(interval, remaining))


def settled_ready(
    process: BeadsProcess, project: str, session: str, found: Peers, stream: str
) -> tuple[str, ...]:
    """Ready beads read again after peers looked drained.

    A peer closing its blocker between the first ready read and the peer read
    makes a dependent ready that the earlier read missed; a drained verdict
    needs readiness read after the peer state.
    """
    return (
        ()
        if found.pending
        else ids(process.ready(project, session), project, None, stream)
    )


def _poll(
    process: BeadsProcess, project: str, session: str, deadline: float, stream: str
) -> tuple[dict[str, object] | None, Peers]:
    def bounded() -> BeadsProcess:
        """Each call gets what remains of the deadline, at least one second."""
        remaining = deadline - time.monotonic()
        return replace(process, timeout=max(1.0, min(process.timeout, remaining)))

    assigned = ids(bounded().assigned(session), project, session)
    blocked = ids(bounded().blocked(session), project, session) if assigned else ()
    waiting_assigned = tuple(
        identifier for identifier in assigned if identifier in blocked
    )
    actionable = tuple(
        identifier for identifier in assigned if identifier not in blocked
    )
    if actionable:
        return {
            "code": "ExecutorWorkAssigned",
            "project": project,
            "assigned": list(actionable),
        }, Peers((), ())
    ready = ids(bounded().ready(project, session), project, None, stream)
    found = Peers((), ())
    if not ready:
        found = peers(bounded(), project, session, ready, stream)
        found = replace(
            found, waiting=tuple(sorted(set(found.waiting).union(waiting_assigned)))
        )
        ready = settled_ready(bounded(), project, session, found, stream)
    if ready:
        return {
            "code": "ExecutorWorkReady",
            "project": project,
            "ready": list(ready),
        }, (found)
    if not found.pending and not waiting_assigned:
        return {
            "code": "ExecutorQueueDrained",
            "project": project,
            "held": list(found.held),
            "waiting": list(found.waiting),
        }, found
    return None, found


def _in_project(bead: dict[str, object], project: str) -> bool:
    metadata = record(bead.get("metadata", {}), "bead metadata")
    return metadata.get("hive_project") == project


def _in_stream(bead: dict[str, object], stream: str) -> bool:
    """The default stream ("") holds every bead without a `hive_stream`."""
    metadata = record(bead.get("metadata", {}), "bead metadata")
    value = metadata.get("hive_stream")
    return value == stream if stream else value in (None, "")


def _scoped(result: dict[str, object], stream: str) -> dict[str, object]:
    return {**result, "stream": stream} if stream else result


def _assignee(bead: dict[str, object]) -> str:
    value = bead.get("assignee")
    return value if isinstance(value, str) else ""
