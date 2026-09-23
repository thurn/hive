"""Native persistence primitives; compound callers must hold Hive's guards."""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from hive.bead_json import decode_bead, encode_lifecycle
from hive.beads_process import BeadsProcess
from hive.errors import ErrorCode, HiveError
from hive.identity import BeadId, ProjectId
from hive.jsonvalue import integer, record, sequence
from hive.model import Bead, Deferred, Queued, Unstarted, WorkKind
from hive.state_json import encode_state
from hive.write_barrier import DatabaseChange, Intent, RecordChange, WriteBarrier

T = TypeVar("T")


@dataclass(frozen=True)
class NewTask:
    project: ProjectId
    title: str
    description: str
    acceptance: str
    priority: int = 2
    kind: WorkKind = WorkKind.CODE
    state: Queued | Deferred = Queued()


@dataclass(frozen=True)
class BeadsStore:
    process: BeadsProcess
    barrier: WriteBarrier | None = None

    def _write(self, intent: Intent, operation: Callable[[], T]) -> T:
        if self.barrier is None:
            return operation()
        return self.barrier.perform(intent, operation)

    def set_priority(self, identifier: BeadId, priority: int) -> None:
        self._write(
            RecordChange(identifier, "set priority", (("priority", str(priority)),)),
            lambda: self._set_priority(identifier, priority),
        )

    def _set_priority(self, identifier: BeadId, priority: int) -> None:
        value = self.process.run(
            ["update", identifier, "--priority", str(priority)], mutation=True
        )
        try:
            results = sequence(value, "updated issues")
            if len(results) != 1:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Expected one priority acknowledgement"
                )
            updated = record(results[0])
            if updated.get("id") != identifier or updated.get("priority") != priority:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Priority update was not acknowledged"
                )
        except HiveError as error:
            raise HiveError(error.code, error.detail, uncertain=True) from error

    def add_dependencies(
        self, dependent: BeadId, prerequisites: tuple[BeadId, ...]
    ) -> None:
        if not prerequisites:
            return
        self._write(
            RecordChange(
                dependent,
                "attach prerequisites",
                (("prerequisites", json.dumps(prerequisites)),),
            ),
            lambda: self._add_dependencies(dependent, prerequisites),
        )

    def _add_dependencies(
        self, dependent: BeadId, prerequisites: tuple[BeadId, ...]
    ) -> None:
        payload = "".join(
            json.dumps({"from": dependent, "to": prerequisite, "type": "blocks"}) + "\n"
            for prerequisite in prerequisites
        )
        value = self.process.run(
            ["dep", "add", "--file", "-"], mutation=True, input_text=payload
        )
        try:
            result = record(value, "dependency result")
            edges = sequence(result.get("dependencies"), "added dependencies")
            expected = {prerequisite for prerequisite in prerequisites}
            actual: set[str] = set()
            from hive.jsonvalue import string

            for item in edges:
                edge = record(item, "dependency")
                if edge.get("issue_id") != dependent or edge.get("type") != "blocks":
                    raise HiveError(
                        ErrorCode.INVALID_RECORD, "Wrong dependency acknowledgement"
                    )
                actual.add(string(edge.get("depends_on_id"), "prerequisite"))
            if (
                result.get("status") != "added"
                or result.get("count") != len(expected)
                or actual != expected
            ):
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Incomplete dependency acknowledgement"
                )
        except HiveError as error:
            raise HiveError(error.code, error.detail, uncertain=True) from error

    def active_records(self) -> tuple[dict[str, object], ...]:
        """Include corrupted closed owners without decoding unrelated history."""
        values = sequence(
            self.process.run(
                [
                    "query",
                    "status!=closed OR assignee!=none",
                    "--all",
                    "--limit",
                    "0",
                ]
            ),
            "active issues",
        )
        return tuple(record(value, "issue") for value in values)

    def dependency(
        self, dependent: BeadId, prerequisite: BeadId, *, remove: bool = False
    ) -> None:
        self._write(
            RecordChange(
                dependent,
                "remove prerequisite" if remove else "add prerequisite",
                (("prerequisite", prerequisite),),
            ),
            lambda: self._dependency(dependent, prerequisite, remove=remove),
        )

    def _dependency(
        self, dependent: BeadId, prerequisite: BeadId, *, remove: bool = False
    ) -> None:
        operation = "remove" if remove else "add"
        value = self.process.run(
            ["dep", operation, dependent, prerequisite], mutation=True
        )
        try:
            result = record(value, "dependency result")
            if (
                result.get("status") != ("removed" if remove else "added")
                or result.get("issue_id") != dependent
                or result.get("depends_on_id") != prerequisite
            ):
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Unexpected dependency result"
                )
        except HiveError as error:
            raise HiveError(error.code, error.detail, uncertain=True) from error

    def tasks(self, *, ids: tuple[BeadId, ...] | None = None) -> tuple[Bead, ...]:
        if ids == ():
            return ()
        arguments = [
            "list",
            "--limit",
            "0",
            "--skip-labels",
            "--include-infra",
            "--include-gates",
            "--include-templates",
        ]
        if ids is None:
            arguments.extend(["--status", "open,in_progress,blocked,deferred"])
        else:
            arguments.extend(["--all", "--id", ",".join(ids)])
        # --skip-labels deliberately selects bd's envelope output form.
        envelope = record(self.process.run(arguments), "list response")
        values = sequence(envelope.get("issues"), "issues")
        info = record(envelope.get("meta"), "list metadata")
        if integer(info.get("count"), "issue count") != len(values):
            raise HiveError(ErrorCode.INVALID_RECORD, "Incomplete issue list")
        result: list[Bead] = []
        for value in values:
            data = record(value, "issue")
            if data.get("issue_type") in {"agent", "role", "message"}:
                continue
            bead = decode_bead(data)
            if ids is not None and bead.id not in ids:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Beads returned an unexpected task"
                )
            result.append(bead)
        if len({bead.id for bead in result}) != len(result):
            raise HiveError(ErrorCode.INVALID_RECORD, "Beads returned duplicate tasks")
        return tuple(result)

    def get(self, identifier: BeadId) -> Bead:
        values = self.tasks(ids=(identifier,))
        if not values:
            raise HiveError(ErrorCode.NOT_FOUND, f"No task {identifier}")
        return values[0]

    def save_lifecycle(self, bead: Bead) -> None:
        native = encode_lifecycle(bead)
        self._write(
            RecordChange(
                bead.id,
                "save lifecycle",
                (
                    (
                        "state",
                        json.dumps(
                            {
                                "status": native.status,
                                "assignee": native.assignee,
                                "hive": native.metadata["hive"],
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                ),
            ),
            lambda: self._save_lifecycle(bead),
        )

    def _save_lifecycle(self, bead: Bead) -> None:
        """One native update for status, assignee and the complete Hive subtree.

        The owner/readiness check and this call run under admission exclusion.
        Do not use --claim here: it writes before the metadata update.
        """
        native = encode_lifecycle(bead)
        arguments = [
            "update",
            bead.id,
            "--status",
            native.status,
            "--assignee",
            native.assignee,
            "--metadata",
            json.dumps(native.metadata, separators=(",", ":")),
        ]
        if native.status == "open":
            arguments.extend(["--defer", ""])
        value = self.process.run(arguments, mutation=True)
        try:
            results = sequence(value, "updated issues")
            if len(results) != 1:
                raise HiveError(ErrorCode.INVALID_RECORD, "Expected one updated issue")
            updated = record(results[0])
            metadata = record(updated.get("metadata"))
            if (
                updated.get("id") != bead.id
                or updated.get("status") != native.status
                or updated.get("assignee", "") != native.assignee
                or metadata.get("hive") != native.metadata["hive"]
            ):
                raise HiveError(
                    ErrorCode.INVALID_RECORD,
                    "Beads did not acknowledge this lifecycle change",
                )
        except HiveError as error:
            raise HiveError(error.code, error.detail, uncertain=True) from error

    def create(self, task: NewTask) -> Bead:
        nonce = uuid.uuid4().hex
        return self._write(
            DatabaseChange(
                "create task",
                (
                    ("write_nonce", nonce),
                    ("project", task.project),
                    ("title", task.title),
                ),
            ),
            lambda: self._create(task, nonce),
        )

    def _create(self, task: NewTask, nonce: str) -> Bead:
        if (
            not task.title.strip()
            or not task.project.strip()
            or isinstance(task.priority, bool)
            or not 0 <= task.priority <= 4
        ):
            raise HiveError(
                ErrorCode.INVALID_INPUT,
                "Project, title and valid priority are required",
            )
        if not isinstance(task.state.work, Unstarted):
            raise HiveError(
                ErrorCode.INVALID_INPUT, "New tasks cannot inherit existing execution"
            )
        native = encode_state(task.state)
        metadata = {
            "hive": {
                **native.metadata,
                "project": task.project,
                "kind": task.kind,
                "write_nonce": nonce,
            }
        }
        arguments = [
            "create",
            "--type",
            "task",
            "--title",
            task.title,
            "--description",
            task.description,
            "--acceptance",
            task.acceptance,
            "--priority",
            str(task.priority),
            "--metadata",
            json.dumps(metadata),
        ]
        if isinstance(task.state, Deferred):
            # The native create interface accepts a date rather than status.
            # Hive uses deferred status/reason, never passage of time, to resume.
            arguments.extend(["--defer", "9999-12-31"])
        value = self.process.run(arguments, mutation=True)
        try:
            created = record(value, "created issue")
            # Native create returns no hydrated dependency envelope. There were
            # no dependency arguments; edges are deliberately added separately.
            bead = decode_bead({**created, "dependency_count": 0, "dependencies": []})
            hive = record(record(created.get("metadata")).get("hive"))
            if (
                hive.get("write_nonce") != nonce
                or bead.project != task.project
                or bead.title != task.title
                or bead.description != task.description
                or bead.acceptance != task.acceptance
                or bead.priority != task.priority
                or bead.kind != task.kind
                or bead.state != task.state
            ):
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Task creation was not acknowledged"
                )
            return bead
        except HiveError as error:
            raise HiveError(error.code, error.detail, uncertain=True) from error
