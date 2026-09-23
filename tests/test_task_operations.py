"""Real processes compete through the same durable task operations."""

import shutil
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from server_fixture import private_server

from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore, NewTask
from hive.configuration import Project
from hive.configuration_store import ConfigurationStore
from hive.errors import ErrorCode, HiveError
from hive.filing import Filing
from hive.identity import (
    CodexProjectId,
    CodexTaskId,
    CodexTurnId,
    ProjectId,
    SourceCommit,
    WorktreePath,
)
from hive.jsonvalue import parse, record
from hive.locking import Guards
from hive.model import (
    ArtifactDelivery,
    Capacity,
    Deferred,
    Implementing,
    Owned,
    Owner,
    PauseReason,
    Reviewing,
    ReviewingArtifact,
    WorkKind,
    owner_of,
)
from hive.task_service import TaskService


def project(root: Path) -> Project:
    return Project(
        ProjectId("search"),
        root,
        root / "invariants.md",
        CodexProjectId("native-project"),
    )


def owner(name: str) -> Owner:
    return Owner(CodexTaskId(name), CodexTurnId("claim-turn"))


def setup(
    connection: BeadsConnection,
) -> tuple[BeadsStore, Guards, TaskService, Filing]:
    guards = Guards(connection.directory.parent / "guards")
    store = BeadsStore(BeadsProcess(connection, "fixture"), guards.write_barrier)
    ConfigurationStore(store, guards).initialize((project(connection.directory),))
    return store, guards, TaskService(store, guards), Filing(store, guards)


def compete(
    connection: BeadsConnection, guards: Guards, targets: list[str]
) -> list[dict[str, object]]:
    processes: list[subprocess.Popen[str]] = []
    try:
        for index, target in enumerate(targets):
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("claim_worker.py")),
                        str(connection.directory),
                        str(guards.directory),
                        f"peer-{index}-{target}",
                        target,
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for process in processes:
            if process.stdout is None or process.stdout.readline().strip() != "ready":
                raise AssertionError("Claimant failed before barrier")
        for process in processes:
            stream = process.stdin
            if stream is None:
                raise AssertionError("Missing claimant input")
            stream.write("go\n")
            stream.flush()
        results: list[dict[str, object]] = []
        for process in processes:
            output, error = process.communicate(timeout=15)
            if process.returncode:
                raise AssertionError(error)
            results.append(record(parse(output)))
        return results
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


class TaskOperationTests(unittest.TestCase):
    def test_retained_work_preserves_project_execution_binding(self) -> None:
        with private_server() as (connection, _):
            store, guards, service, filing = setup(connection)
            registration = project(connection.directory)
            configuration = ConfigurationStore(store, guards)
            task = filing.file(NewTask(registration.id, "Retained", "Fix", "Fixed"))
            held_by = owner("retained")
            service.claim(task.project, held_by, task.id)
            workspace = WorktreePath(connection.directory / "retained-workspace")
            service.advance(task.id, task.project, held_by, Implementing(workspace))
            service.defer(
                task.id, task.project, PauseReason.USER, "Pause", expected_owner=held_by
            )
            service.settle(task.id, task.project, held_by)
            for projects in (
                (),
                (replace(registration, repository=connection.directory / "other"),),
                (replace(registration, native_id=CodexProjectId("other-native")),),
            ):
                with self.assertRaises(HiveError) as caught:
                    configuration.replace(projects, Capacity())
                self.assertEqual(caught.exception.code, ErrorCode.INVALID_INPUT)
            # Updating the review document does not move execution to another repo.
            configuration.replace(
                (replace(registration, invariants=connection.directory / "new.md"),),
                Capacity(4),
            )
            service.resume(task.id, task.project, user_authorized=True)
            reclaimed = service.claim(task.project, held_by, task.id)
            self.assertEqual(reclaimed.state, Owned(held_by, Implementing(workspace)))

    def test_paused_history_does_not_block_unrelated_ready_work(self) -> None:
        with private_server() as (connection, _):
            store, _, service, filing = setup(connection)
            prerequisite = filing.file(
                NewTask(ProjectId("search"), "Old prerequisite", "Old", "Done")
            )
            paused = filing.file(
                NewTask(prerequisite.project, "Paused", "Later", "Done"),
                (prerequisite.id,),
            )
            service.defer(
                paused.id,
                paused.project,
                PauseReason.USER,
                "Pause",
                expected_owner=None,
            )
            # A malformed unowned historical record is irrelevant to this claim.
            store.process.run(
                ["update", prerequisite.id, "--status", "closed"], mutation=True
            )
            independent = filing.file(
                NewTask(prerequisite.project, "Independent", "Fix", "Fixed")
            )
            self.assertEqual(
                service.claim(independent.project, owner("independent")).id,
                independent.id,
            )
            with self.assertRaises(HiveError) as caught:
                service.claim(paused.project, owner("paused"), paused.id)
            self.assertEqual(caught.exception.code, ErrorCode.PAUSED)

    def test_registered_projects_share_capacity_without_crossing_scope(self) -> None:
        with private_server() as (connection, _):
            store, guards, service, filing = setup(connection)
            storage = Project(
                ProjectId("storage"),
                connection.directory.parent / "storage",
                connection.directory.parent / "storage/invariants.md",
                CodexProjectId("storage-native"),
            )
            ConfigurationStore(store, guards).replace(
                (project(connection.directory), storage),
                Capacity(8, ((storage.id, 1),)),
            )
            first = filing.file(NewTask(storage.id, "Storage fix", "Fix", "Fixed"))
            filing.file(NewTask(storage.id, "Later fix", "Fix", "Fixed"))
            search = filing.file(
                NewTask(ProjectId("search"), "Search fix", "Needs storage", "Fixed"),
                (first.id,),
            )
            with self.assertRaises(HiveError) as caught:
                service.claim(search.project, owner("search"), first.id)
            self.assertEqual(caught.exception.code, ErrorCode.INVALID_INPUT)
            with self.assertRaises(HiveError) as caught:
                service.claim(search.project, owner("search"), search.id)
            self.assertEqual(caught.exception.code, ErrorCode.DEPENDENCY_BLOCKED)
            service.claim(storage.id, owner("storage"), first.id)
            with self.assertRaises(HiveError) as caught:
                service.claim(storage.id, owner("other-storage"))
            self.assertEqual(caught.exception.code, ErrorCode.CAPACITY_FULL)
            with self.assertRaises(HiveError):
                filing.file(
                    NewTask(ProjectId("unknown"), "Rejected", "Unknown project", "None")
                )

    def test_competing_claims_capacity_review_and_lost_response(self) -> None:
        with private_server() as (connection, _):
            store, guards, service, filing = setup(connection)
            first = filing.file(NewTask(ProjectId("search"), "First", "Fix", "Fixed"))
            results = compete(connection, guards, [first.id] * 8)
            self.assertEqual(sum(r["code"] == "Claimed" for r in results), 1, results)
            self.assertTrue(
                all(r["code"] in {"Claimed", "AlreadyOwned", "Busy"} for r in results),
                results,
            )
            for index in range(10):
                filing.file(NewTask(first.project, f"Next {index}", "Fix", "Fixed"))
            compete(connection, guards, ["next"] * 12)
            for index in range(8):
                try:
                    service.claim(first.project, owner(f"filler-{index}"))
                except HiveError as error:
                    self.assertEqual(error.code, ErrorCode.CAPACITY_FULL)
                    break
            _, active = service.snapshot()
            owned = [bead for bead in active if owner_of(bead.state) is not None]
            self.assertEqual(len(owned), 8)
            current = store.get(first.id)
            self.assertIsInstance(current.state, Owned)
            if not isinstance(current.state, Owned):
                self.fail("Missing owner")
            held_by = current.state.owner
            path = WorktreePath(connection.directory / "worktree")
            service.advance(first.id, first.project, held_by, Implementing(path))
            service.advance(
                first.id,
                first.project,
                held_by,
                Reviewing(path, SourceCommit("a" * 40)),
            )
            with self.assertRaises(HiveError) as caught:
                service.claim(first.project, held_by)
            self.assertEqual(caught.exception.code, ErrorCode.ALREADY_OWNED)
            service.defer(
                first.id,
                first.project,
                PauseReason.USER,
                "Stop",
                expected_owner=held_by,
            )
            with self.assertRaises(HiveError) as caught:
                service.claim(first.project, owner("excess"))
            self.assertEqual(caught.exception.code, ErrorCode.CAPACITY_FULL)
            service.settle(first.id, first.project, held_by)
            with self.assertRaises(HiveError):
                service.resume(first.id, first.project)
            # Filing is still available even when every execution slot is full.
            service.claim(first.project, owner("replacement"))
            filing.file(NewTask(first.project, "Backlog", "Wait", "Later"))
            configuration = ConfigurationStore(store, guards)
            configuration.replace((project(connection.directory),), Capacity(1))
            self.assertEqual(
                len([b for b in service.snapshot()[1] if owner_of(b.state)]), 8
            )
            with self.assertRaises(HiveError) as caught:
                service.claim(first.project, owner("after-lowering"))
            self.assertEqual(caught.exception.code, ErrorCode.CAPACITY_FULL)

    def test_dependency_completion_cancellation_and_cycle_rejection(self) -> None:
        with private_server() as (connection, _):
            store, guards, service, filing = setup(connection)
            prerequisite = filing.file(
                NewTask(
                    ProjectId("search"),
                    "Research",
                    "Answer",
                    "Evidence",
                    kind=WorkKind.ARTIFACT,
                )
            )
            dependent = filing.file(
                NewTask(prerequisite.project, "Implement", "Needs research", "Fixed"),
                (prerequisite.id,),
            )
            with self.assertRaises(HiveError) as caught:
                service.claim(dependent.project, owner("blocked"), dependent.id)
            self.assertEqual(caught.exception.code, ErrorCode.DEPENDENCY_BLOCKED)
            service.claim(prerequisite.project, owner("research"), prerequisite.id)
            service.advance(
                prerequisite.id,
                prerequisite.project,
                owner("research"),
                ReviewingArtifact("/tmp/report"),
            )
            service.complete(
                prerequisite.id,
                prerequisite.project,
                owner("research"),
                "Answered with evidence",
                ArtifactDelivery("/tmp/report"),
            )
            self.assertEqual(
                service.claim(dependent.project, owner("research")).id, dependent.id
            )
            a = filing.file(NewTask(dependent.project, "A", "Work", "Done"))
            b = filing.file(NewTask(dependent.project, "B", "Work", "Done"), (a.id,))
            with self.assertRaises(HiveError):
                filing.dependency(a.id, a.project, b.id)
            self.assertIsInstance(store.get(a.id).state, Deferred)
            self.assertEqual(guards.write_barrier.inspect()["blocked"], True)
            for operation in (
                lambda: service.resume(a.id, a.project),
                lambda: filing.dependency(a.id, a.project, b.id, remove=True),
                lambda: service.cancel(a.id, a.project, "Cancelled"),
                lambda: service.claim(b.project, owner("blocked-cancelled"), b.id),
            ):
                with self.assertRaises(HiveError) as caught:
                    operation()
                self.assertEqual(caught.exception.code, ErrorCode.RECOVERY_REQUIRED)

    def test_crash_between_creation_and_dependency_attachment_requires_recovery(
        self,
    ) -> None:
        with private_server() as (connection, _):
            store, guards, service, filing = setup(connection)
            first = filing.file(
                NewTask(ProjectId("search"), "Prerequisite", "Fix", "Fixed")
            )
            executable = shutil.which("bd")
            self.assertIsNotNone(executable)
            wrapper = connection.directory / "crash-bd"
            wrapper.write_text(
                "#!/usr/bin/env python3\nimport os,sys,signal\n"
                "if 'dep' in sys.argv:\n os.kill(os.getppid(),signal.SIGKILL)\n sys.exit(0)\n"
                f"os.execv({executable!r},[{executable!r},*sys.argv[1:]])\n"
            )
            wrapper.chmod(0o755)
            script = """
import sys
from pathlib import Path
from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore,NewTask
from hive.filing import Filing
from hive.identity import BeadId,ProjectId
from hive.locking import Guards
guards=Guards(Path(sys.argv[2]))
store=BeadsStore(BeadsProcess(BeadsConnection.read(Path(sys.argv[1])),'crashing',sys.argv[3]),guards.write_barrier)
Filing(store,guards).file(NewTask(ProjectId('search'),'Interrupted','Needs prerequisite','Done'),(BeadId(sys.argv[4]),))
"""
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(connection.directory),
                    str(guards.directory),
                    str(wrapper),
                    first.id,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertLess(result.returncode, 0, result.stderr)
            interrupted = next(
                bead for bead in store.tasks() if bead.title == "Interrupted"
            )
            self.assertIsInstance(interrupted.state, Deferred)
            with self.assertRaises(HiveError) as caught:
                service.resume(interrupted.id, interrupted.project)
            self.assertEqual(caught.exception.code, ErrorCode.RECOVERY_REQUIRED)
            self.assertEqual(
                guards.write_barrier.inspect()["write"],
                {
                    "scope": "record",
                    "bead": interrupted.id,
                    "change": "attach prerequisites",
                    "details": {"prerequisites": f'["{first.id}"]'},
                },
            )
            with self.assertRaises(HiveError) as caught:
                filing.dependency(interrupted.id, interrupted.project, first.id)
            self.assertEqual(caught.exception.code, ErrorCode.RECOVERY_REQUIRED)

    def test_closed_owner_corruption_cannot_disappear_from_admission(self) -> None:
        with private_server() as (connection, _):
            store, _, service, filing = setup(connection)
            task = filing.file(
                NewTask(ProjectId("search"), "Corrupt owner", "Fixture", "Blocked")
            )
            store.process.run(
                ["update", task.id, "--status", "closed", "--assignee", "ghost"],
                mutation=True,
            )
            with self.assertRaises(HiveError) as caught:
                service.claim(task.project, owner("new-owner"))
            self.assertEqual(caught.exception.code, ErrorCode.INVALID_RECORD)
