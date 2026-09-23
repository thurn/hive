"""Recoverable filing and dependency changes using native Beads edges."""

from dataclasses import dataclass, replace

from hive.beads_store import BeadsStore, NewTask
from hive.configuration_store import ConfigurationStore
from hive.errors import ErrorCode, HiveError
from hive.identity import BeadId, ProjectId
from hive.locking import Guards
from hive.model import (
    Bead,
    Deferred,
    PauseCondition,
    PauseReason,
    Queued,
    Unstarted,
    owner_of,
)
from hive.transitions import resume


@dataclass(frozen=True)
class Filing:
    store: BeadsStore
    guards: Guards

    def file(self, task: NewTask, dependencies: tuple[BeadId, ...] = ()) -> Bead:
        with self.guards.mutation(), self.guards.admission():
            ConfigurationStore(self.store, self.guards).read().project(task.project)
            prerequisites = tuple(dict.fromkeys(dependencies))
            found = {bead.id for bead in self.store.tasks(ids=prerequisites)}
            if found != set(prerequisites):
                raise HiveError(ErrorCode.NOT_FOUND, "A prerequisite does not exist")
            if not prerequisites:
                return self.store.create(task)
            original = task.state
            staged = (
                replace(original, pending_dependencies=prerequisites)
                if isinstance(original, Deferred)
                else Deferred(
                    (
                        PauseCondition(
                            PauseReason.CHECKPOINT, "Attaching prerequisites"
                        ),
                    ),
                    Unstarted(),
                    prerequisites,
                )
            )
            created = self.store.create(replace(task, state=staged))
            try:
                self.store.add_dependencies(created.id, prerequisites)
                finished = replace(created, dependencies=prerequisites, state=original)
                self.store.save_lifecycle(finished)
                return finished
            except HiveError as error:
                raise HiveError(
                    error.code,
                    f"Filed {created.id}; dependency filing needs inspection: {error.detail}",
                    uncertain=error.uncertain,
                ) from error

    def dependency(
        self,
        identifier: BeadId,
        project: ProjectId,
        prerequisite: BeadId,
        *,
        remove: bool = False,
    ) -> Bead:
        with self.guards.mutation(), self.guards.admission():
            bead = self.store.get(identifier)
            if bead.project != project:
                raise HiveError(ErrorCode.INVALID_INPUT, "Task is outside this project")
            if owner_of(bead.state) is not None:
                raise HiveError(
                    ErrorCode.ALREADY_OWNED,
                    "Defer and settle work before changing dependencies",
                )
            if not isinstance(bead.state, (Queued, Deferred)):
                raise HiveError(
                    ErrorCode.INVALID_INPUT, "Terminal dependencies are immutable"
                )
            self.store.get(prerequisite)
            if not remove and prerequisite in bead.dependencies:
                return bead
            if remove:
                self.store.dependency(identifier, prerequisite, remove=True)
                changed = replace(
                    bead,
                    dependencies=tuple(
                        d for d in bead.dependencies if d != prerequisite
                    ),
                )
                if isinstance(changed.state, Deferred):
                    changed = replace(
                        changed,
                        state=replace(
                            changed.state,
                            pending_dependencies=tuple(
                                d
                                for d in changed.state.pending_dependencies
                                if d != prerequisite
                            ),
                        ),
                    )
                    self.store.save_lifecycle(changed)
                return changed
            original = bead.state
            pending = (
                tuple(dict.fromkeys((*original.pending_dependencies, prerequisite)))
                if isinstance(original, Deferred)
                else (prerequisite,)
            )
            staged = (
                replace(original, pending_dependencies=pending)
                if isinstance(original, Deferred)
                else Deferred(
                    (PauseCondition(PauseReason.CHECKPOINT, "Attaching prerequisite"),),
                    original.work,
                    pending,
                )
            )
            bead = replace(bead, state=staged)
            self.store.save_lifecycle(bead)
            self.store.add_dependencies(identifier, (prerequisite,))
            changed = replace(
                bead,
                dependencies=tuple(dict.fromkeys((*bead.dependencies, prerequisite))),
            )
            if isinstance(original, Queued):
                changed = resume(changed)
            else:
                changed = replace(changed, state=original)
            self.store.save_lifecycle(changed)
            return changed
