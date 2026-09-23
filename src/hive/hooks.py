"""Bounded native hooks: one advisory reminder or an owner-checked user pause."""

import json
import sqlite3
import sys

from hive import transitions
from hive.admission import admit, next_ready
from hive.bead_json import decode_bead
from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore
from hive.configuration import find_configuration
from hive.errors import ErrorCode, HiveError
from hive.hook_events import HookEvent, Interrupt, Stop, decode
from hive.jsonvalue import parse
from hive.launch_context import LaunchContext
from hive.locking import Guards, file_lock
from hive.model import Bead, PauseReason, Queued, owner_of
from hive.reminders import ReminderGuard, observe_transition, report_failure
from hive.session import Role, sessions


def tasks(records: tuple[dict[str, object], ...]) -> tuple[Bead, ...]:
    return tuple(
        decode_bead(raw)
        for raw in records
        if raw.get("issue_type") not in {"role", "agent", "message"}
    )


def stop(
    event: Stop, store: BeadsStore, guards: Guards, reminder: ReminderGuard
) -> dict[str, object]:
    if guards.stop.exists():
        return {}
    records = store.active_records()
    session = next((s for s in sessions(records) if s.task == event.owner.task), None)
    if session is None or session.focus.role != Role.EXECUTOR:
        return {}
    configuration = find_configuration(records)
    configuration.project(session.project)
    active = tasks(records)
    known = {bead.id: bead for bead in active}
    missing = {
        dependency
        for bead in active
        if bead.project == session.project and isinstance(bead.state, Queued)
        for dependency in bead.dependencies
        if dependency not in known
    }
    known.update((bead.id, bead) for bead in store.tasks(ids=tuple(sorted(missing))))
    candidate = next_ready(session.project, active, known)
    # This checks the same predicates as admission but never persists the result.
    # A later real claim must recheck everything under the admission lock.
    admit(
        candidate, event.owner, session.project, active, known, configuration.capacity
    )
    if not reminder.reserve(event.owner, already_continued=event.already_continued):
        return {}
    return {
        "decision": "block",
        "reason": (
            f"Hive has eligible work in project {session.project}. "
            "Use the executor's normal next-work operation and resource judgment "
            "before ending. This reminder neither claims a bead nor resumes a pause."
        ),
    }


def interrupt(
    event: Interrupt, store: BeadsStore, guards: Guards, reminder: ReminderGuard
) -> dict[str, object]:
    if guards.stop.exists():
        raise HiveError(
            ErrorCode.PAUSED, "Maintenance is stopped; interruption retained"
        )
    # The hook entrypoint already owns the shared maintenance guard.
    with file_lock(guards.directory / "admission.lock", timeout=0.1):
        guards.write_barrier.require_clear()
        records = store.active_records()
        session = next(
            (s for s in sessions(records) if s.task == event.owner.task), None
        )
        if session is None:
            return {}
        owned = [bead for bead in tasks(records) if owner_of(bead.state) == event.owner]
        if not owned:
            return {}
        if len(owned) != 1 or owned[0].project != session.project:
            return {
                "systemMessage": "Hive found ambiguous ownership; recovery is required."
            }
        bead = transitions.defer(
            owned[0],
            PauseReason.USER,
            "Native Codex user interruption",
            expected_owner=event.owner,
        )
        store.save_lifecycle(bead)
        observe_transition(reminder.path, owned[0], bead)
    return {
        "systemMessage": (
            f"Hive paused {bead.id}. External work may still be settling; "
            "the execution slot remains held until its writers are stopped."
        )
    }


def handle(event: HookEvent, context: LaunchContext) -> dict[str, object]:
    guards = Guards(context.state / "locks")
    reminder = ReminderGuard(guards.directory / "reminders.sqlite3")
    if isinstance(event, Interrupt):
        try:
            # Record before configuration reads: a broken Beads connection must
            # not erase a native stop. A later entry can apply the exact pause.
            reminder.interrupt(event.owner)
        except (OSError, ValueError, sqlite3.Error) as error:
            report_failure(f"Hive could not retain the native interruption: {error}")
    store = BeadsStore(
        BeadsProcess(BeadsConnection.read(context.beads), "hive-hook", timeout=0.5),
        guards.write_barrier,
    )
    if isinstance(event, Interrupt):
        return interrupt(event, store, guards, reminder)
    return stop(event, store, guards, reminder)


def main() -> int:
    context = LaunchContext.read()
    released = False
    try:
        payload = sys.stdin.buffer.read(1_048_577)
        if len(payload) > 1_048_576:
            raise ValueError("Native hook payload exceeds 1 MiB")
        event = decode(parse(payload.decode("utf-8")))
        if isinstance(event, Stop):
            context.release()
            released = True
        result = handle(event, context)
    except HiveError as error:
        if error.code in {
            ErrorCode.NO_READY_WORK,
            ErrorCode.CAPACITY_FULL,
            ErrorCode.ALREADY_OWNED,
            ErrorCode.DEPENDENCY_BLOCKED,
        }:
            result = {}
        else:
            result = {"systemMessage": f"Hive hook did not complete: {error}"}
    except (OSError, ValueError, sqlite3.Error) as error:
        # Failure can never request another model turn. Report the uncertainty;
        # interrupted ownership must be inspected before any attempted resumption.
        result = {"systemMessage": f"Hive hook did not complete: {error}"}
    finally:
        if not released:
            context.release()
    print(json.dumps(result, ensure_ascii=False))
    return 0
