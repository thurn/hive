"""Native persistence primitives; compound callers must hold Hive's guards."""

import json
from dataclasses import dataclass

from hive.bead_json import decode_bead, encode_lifecycle
from hive.beads_process import BeadsProcess
from hive.errors import ErrorCode, HiveError
from hive.identity import BeadId, ProjectId
from hive.jsonvalue import integer, record, sequence
from hive.model import Bead, Deferred, Queued, Unstarted, WorkKind
from hive.state_json import encode_state


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

    def add_dependencies(
        self, dependent: BeadId, prerequisites: tuple[BeadId, ...]
    ) -> None:
        if not prerequisites:
            return
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
        if isinstance(task.state, Deferred) and not task.state.note.strip():
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Deferred creation requires a note"
            )
        native = encode_state(task.state)
        metadata = {
            "hive": {**native.metadata, "project": task.project, "kind": task.kind}
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
            return decode_bead({**created, "dependency_count": 0, "dependencies": []})
        except HiveError as error:
            raise HiveError(error.code, error.detail, uncertain=True) from error
