"""Short guarded read/decide/write operations used by every worker."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from hive import transitions
from hive.admission import admit, blockers, next_ready
from hive.bead_json import decode_bead
from hive.beads_store import BeadsStore
from hive.configuration import Configuration, find_configuration
from hive.errors import ErrorCode, HiveError
from hive.identity import BeadId, ProjectId
from hive.locking import Guards
from hive.model import (
    Bead,
    Deferred,
    Delivery,
    Draining,
    Owned,
    Owner,
    PauseReason,
    Phase,
    Queued,
    WaitingForDelivery,
)


@dataclass(frozen=True)
class TaskService:
    store: BeadsStore
    guards: Guards

    def snapshot(self) -> tuple[Configuration, tuple[Bead, ...]]:
        records = self.store.active_records()
        configuration = find_configuration(records)
        tasks = tuple(
            decode_bead(raw)
            for raw in records
            if raw.get("issue_type") not in {"role", "agent", "message"}
        )
        if len({bead.id for bead in tasks}) != len(tasks):
            raise HiveError(ErrorCode.INVALID_RECORD, "Duplicate task identities")
        return configuration, tasks

    def claim(
        self, project: ProjectId, owner: Owner, identifier: BeadId | None = None
    ) -> Bead:
        with self.guards.mutation(), self.guards.admission():
            configuration, active = self.snapshot()
            configuration.project(project)
            known = {bead.id: bead for bead in active}
            if identifier is not None and identifier not in known:
                known[identifier] = self.store.get(identifier)
            candidates = (
                [known[identifier]]
                if identifier is not None
                else [b for b in active if b.project == project]
            )
            missing = {
                dependency
                for b in candidates
                if isinstance(b.state, Queued)
                for dependency in b.dependencies
                if dependency not in known
            }
            known.update(
                (bead.id, bead) for bead in self.store.tasks(ids=tuple(sorted(missing)))
            )
            bead = (
                known[identifier]
                if identifier is not None
                else next_ready(project, active, known)
            )
            claimed = admit(bead, owner, project, active, known, configuration.capacity)
            self.store.save_lifecycle(claimed)
            return claimed

    def ready(self, project: ProjectId) -> tuple[Bead, ...]:
        """Advisory read only; the caller still needs an atomic claim."""
        configuration, active = self.snapshot()
        configuration.project(project)
        known = {bead.id: bead for bead in active}
        candidates = tuple(
            b for b in active if b.project == project and isinstance(b.state, Queued)
        )
        missing = {
            dependency
            for b in candidates
            for dependency in b.dependencies
            if dependency not in known
        }
        known.update(
            (bead.id, bead) for bead in self.store.tasks(ids=tuple(sorted(missing)))
        )
        return tuple(
            sorted(
                (bead for bead in candidates if not blockers(bead, known)),
                key=lambda b: (b.priority, b.created, b.id),
            )
        )

    def enter_turn(
        self, identifier: BeadId, project: ProjectId, previous: Owner, current: Owner
    ) -> Bead:
        import sqlite3

        from hive.reminders import ReminderGuard

        with self.guards.mutation(), self.guards.admission():
            bead = self.store.get(identifier)
            if bead.project != project:
                raise HiveError(ErrorCode.INVALID_INPUT, "Task is outside this project")
            transitions.require_owner(bead, previous)
            try:
                interrupted = ReminderGuard(
                    self.guards.directory / "reminders.sqlite3"
                ).interrupted(previous)
            except (OSError, ValueError, sqlite3.Error) as error:
                raise HiveError(
                    ErrorCode.RECOVERY_REQUIRED,
                    f"Native interruption observation is unavailable: {error}",
                ) from error
            if interrupted:
                paused = transitions.defer(
                    bead,
                    PauseReason.USER,
                    "Native Codex user interruption",
                    expected_owner=previous,
                )
                self.store.save_lifecycle(paused)
                from hive.reminders import observe_transition

                observe_transition(
                    self.guards.directory / "reminders.sqlite3", bead, paused
                )
                raise HiveError(ErrorCode.PAUSED, "The previous turn was interrupted")
            changed = transitions.enter_turn(bead, previous, current)
            self.store.save_lifecycle(changed)
            return changed

    def prioritize(self, identifier: BeadId, project: ProjectId, priority: int) -> None:
        if isinstance(priority, bool) or not 0 <= priority <= 4:
            raise HiveError(ErrorCode.INVALID_INPUT, "Priority must be P0 through P4")
        with self.guards.mutation():
            bead = self.store.get(identifier)
            if bead.project != project:
                raise HiveError(
                    ErrorCode.INVALID_INPUT, "Task is outside the executor's project"
                )
            self.store.set_priority(identifier, priority)

    def _change(
        self, identifier: BeadId, project: ProjectId, apply: Callable[[Bead], Bead]
    ) -> Bead:
        with self.guards.mutation(), self.guards.admission():
            bead = self.store.get(identifier)
            if bead.project != project:
                raise HiveError(
                    ErrorCode.INVALID_INPUT, "Task is outside the executor's project"
                )
            changed = apply(bead)
            self.store.save_lifecycle(changed)
            # Preserve observation ordering under the existing admission lock.
            # Failure does not turn successful Beads work into failure.
            from hive.reminders import observe_transition

            observe_transition(
                self.guards.directory / "reminders.sqlite3", bead, changed
            )
            return changed

    def advance(
        self, identifier: BeadId, project: ProjectId, owner: Owner, phase: Phase
    ) -> Bead:
        return self._local_change(
            identifier, project, lambda bead: transitions.advance(bead, owner, phase)
        )

    def finish_observed_change(self, observed: Bead, changed: Bead) -> Bead:
        """Apply a prepared lifecycle change only to the state actually inspected."""

        def apply(current: Bead) -> Bead:
            if current.state != observed.state:
                raise HiveError(
                    ErrorCode.STALE_OWNER,
                    "Task state changed during provider inspection; inspect before retrying",
                )
            return replace(current, state=changed.state)

        return self._change(observed.id, observed.project, apply)

    def _local_change(
        self, identifier: BeadId, project: ProjectId, apply: Callable[[Bead], Bead]
    ) -> Bead:
        """An uninspected route cannot race into releasing live delivery work."""

        def local(bead: Bead) -> Bead:
            state = bead.state
            phase = (
                state.phase
                if isinstance(state, Owned)
                else (
                    state.work.phase
                    if isinstance(state, Deferred) and isinstance(state.work, Draining)
                    else None
                )
            )
            if isinstance(phase, WaitingForDelivery):
                raise HiveError(
                    ErrorCode.RECOVERY_REQUIRED,
                    "Retained delivery requires provider inspection before this transition",
                )
            return apply(bead)

        return self._change(identifier, project, local)

    def defer(
        self,
        identifier: BeadId,
        project: ProjectId,
        reason: PauseReason,
        note: str,
        *,
        expected_owner: Owner | None,
    ) -> Bead:
        if not note.strip():
            raise HiveError(ErrorCode.INVALID_INPUT, "Deferral requires a note")
        return self._change(
            identifier,
            project,
            lambda bead: transitions.defer(
                bead, reason, note, expected_owner=expected_owner
            ),
        )

    def resume(
        self,
        identifier: BeadId,
        project: ProjectId,
        *,
        reason: PauseReason | None = None,
        user_authorized: bool = False,
    ) -> Bead:
        return self._change(
            identifier,
            project,
            lambda bead: transitions.resume(
                bead, reason=reason, user_authorized=user_authorized
            ),
        )

    def settle(self, identifier: BeadId, project: ProjectId, owner: Owner) -> Bead:
        """The owner calls after stopping writers; peer recovery is a separate path."""
        return self._local_change(
            identifier, project, lambda bead: transitions.settle(bead, owner)
        )

    def complete(
        self,
        identifier: BeadId,
        project: ProjectId,
        owner: Owner,
        summary: str,
        delivery: Delivery,
    ) -> Bead:
        return self._local_change(
            identifier,
            project,
            lambda bead: transitions.complete(bead, owner, summary, delivery),
        )

    def cancel(self, identifier: BeadId, project: ProjectId, reason: str) -> Bead:
        return self._change(
            identifier, project, lambda bead: transitions.cancel(bead, reason)
        )
