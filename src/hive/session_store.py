"""Durable enrollment; native title RPCs happen outside these short guards."""

import json
import uuid
from dataclasses import dataclass, replace

from hive.beads_store import BeadsStore
from hive.configuration import find_configuration
from hive.errors import ErrorCode, HiveError
from hive.identity import CodexTaskId, ProjectId
from hive.jsonvalue import record, sequence
from hive.locking import Guards
from hive.session import (
    AppliedName,
    FailedName,
    Focus,
    PendingName,
    Role,
    Session,
    decode,
    metadata,
    sessions,
)
from hive.write_barrier import DatabaseChange, RecordChange


@dataclass(frozen=True)
class SessionStore:
    store: BeadsStore
    guards: Guards

    def list(self, project: ProjectId | None = None) -> tuple[Session, ...]:
        return tuple(
            s
            for s in sessions(self.store.active_records())
            if project is None or s.project == project
        )

    def enter(
        self,
        task: CodexTaskId,
        project: ProjectId,
        focus: Focus,
        *,
        inline_bead: bool = False,
    ) -> Session:
        if inline_bead and focus.role != Role.BEAD:
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Inline filing requires the bead role"
            )
        with self.guards.mutation(), self.guards.admission():
            records = self.store.active_records()
            find_configuration(records).project(project)
            old = next((s for s in sessions(records) if s.task == task), None)
            if old is not None and old.project != project:
                raise HiveError(
                    ErrorCode.INVALID_INPUT, "Enrollment is in another project"
                )
            if old is not None and inline_bead:
                return old
            if focus.bead is not None and self.store.get(focus.bead).project != project:
                raise HiveError(
                    ErrorCode.INVALID_INPUT, "Title bead is in another project"
                )
            if old is not None:
                if old.focus == focus:
                    return old
                return self._save(replace(old, focus=focus, naming=PendingName()))
            # The same lock excludes duplicate enrollment. After a lost response,
            # a repeated entry finds the permanent native record by task identity.
            nonce = uuid.uuid4().hex
            return self.guards.write_barrier.perform(
                DatabaseChange(
                    "enroll native conversation",
                    (("write_nonce", nonce), ("task", task), ("project", project)),
                ),
                lambda: self._create(task, project, focus, nonce),
            )

    def _create(
        self, task: CodexTaskId, project: ProjectId, focus: Focus, nonce: str
    ) -> Session:
        initial = metadata(task, project, focus, PendingName())
        tagged = {
            "hive_session": {
                **record(initial["hive_session"]),
                "write_nonce": nonce,
            }
        }
        result = self.store.process.run(
            [
                "create",
                "--type",
                "role",
                "--no-history",
                "--title",
                f"Hive conversation {task}",
                "--description",
                "Enrollment and task title; not bead ownership",
                "--metadata",
                json.dumps(tagged),
            ],
            mutation=True,
        )
        try:
            created = decode(result)
            observed = record(record(result).get("metadata"))
            if (created.task, created.project, created.focus, created.naming) != (
                task,
                project,
                focus,
                PendingName(),
            ) or record(observed.get("hive_session")) != record(tagged["hive_session"]):
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Enrollment was not acknowledged"
                )
            return created
        except HiveError as error:
            raise HiveError(error.code, error.detail, uncertain=True) from error

    def record_name(
        self, task: CodexTaskId, title: str, naming: AppliedName | FailedName
    ) -> Session:
        with self.guards.mutation(), self.guards.admission():
            old = next(
                (s for s in sessions(self.store.active_records()) if s.task == task),
                None,
            )
            if old is None:
                raise HiveError(ErrorCode.NOT_FOUND, "Native task is not enrolled")
            if old.focus.title != title:
                # An older native RPC may have overwritten a newer applied name.
                # Preserve the current intent, but no longer claim it is visible.
                self._save(replace(old, naming=PendingName()))
                raise HiveError(
                    ErrorCode.BUSY,
                    "Title changed during the native rename; apply the current title",
                )
            return self._save(replace(old, naming=naming))

    def _save(self, session: Session) -> Session:
        intended = metadata(
            session.task, session.project, session.focus, session.naming
        )
        return self.guards.write_barrier.perform(
            RecordChange(
                session.id,
                "update conversation title intent",
                (("metadata", json.dumps(intended, sort_keys=True)),),
            ),
            lambda: self._save_record(session),
        )

    def _save_record(self, session: Session) -> Session:
        result = self.store.process.run(
            [
                "update",
                session.id,
                "--metadata",
                json.dumps(
                    metadata(
                        session.task, session.project, session.focus, session.naming
                    )
                ),
            ],
            mutation=True,
        )
        try:
            updated = sequence(result, "updated session")
            if len(updated) != 1 or decode(updated[0]) != session:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Session update was not acknowledged"
                )
        except HiveError as error:
            raise HiveError(error.code, error.detail, uncertain=True) from error
        return session
