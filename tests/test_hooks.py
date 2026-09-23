"""Native-shaped hooks across independent processes and real task persistence."""

import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from pathlib import Path

from cli_fixture import ROOT, SCOPE, WORKER, Cli, cli_fixture
from server_fixture import private_server

from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore
from hive.identity import BeadId, CodexTaskId, CodexTurnId, ProjectId
from hive.jsonvalue import parse, record
from hive.locking import Guards
from hive.model import (
    ArtifactDelivery,
    Done,
    Owner,
    PauseReason,
    Queued,
    ReviewingArtifact,
)
from hive.reminders import ReminderGuard
from hive.task_service import TaskService

OWNER: Owner = Owner(CodexTaskId("task-1"), CodexTurnId("turn-1"))


def hook(
    cli: Cli,
    event: str = "Stop",
    *,
    task: str = "task-1",
    turn: str = "turn-1",
    continued: bool = False,
) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(ROOT / "scripts/hive.py"), "hook"],
        input=json.dumps(
            {
                "session_id": task,
                "turn_id": turn,
                "hook_event_name": event,
                "stop_hook_active": continued,
                "cwd": str(cli.state),
                "transcript_path": "/unused/transcript.jsonl",
            }
        ),
        env=cli.environment,
        text=True,
        capture_output=True,
        timeout=10,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return record(parse(result.stdout))


def enroll(cli: Cli, role: str = "executor") -> None:
    cli.call(
        "session",
        "enter",
        "--task",
        "task-1",
        *SCOPE,
        "--role",
        role,
        "--subject",
        "Hook journey",
    )


def finish(cli: Cli, bead: str) -> None:
    cli.call("task", "claim", bead, *SCOPE, *WORKER)
    cli.call(
        "task",
        "advance",
        bead,
        *SCOPE,
        *WORKER,
        "--phase-json",
        '{"kind":"reviewing-artifact","location":"/tmp/report.md"}',
    )
    cli.call(
        "task",
        "complete",
        bead,
        *SCOPE,
        *WORKER,
        "--summary",
        "Delivered",
        "--delivery-json",
        '{"kind":"artifact","location":"/tmp/report.md"}',
    )


class HookTests(unittest.TestCase):
    def test_guard_excludes_competing_processes_and_resets_only_after_completion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path: Path = Path(temporary) / "reminders.sqlite3"
            script: str = (
                "import sys; from pathlib import Path; "
                "from hive.reminders import ReminderGuard; "
                "from hive.model import Owner; "
                "from hive.identity import CodexTaskId,CodexTurnId; "
                "print(ReminderGuard(Path(sys.argv[1])).reserve("
                "Owner(CodexTaskId('task-1'),CodexTurnId('turn-1')),"
                "already_continued=False))"
            )

            def reserve(_: int) -> str:
                return subprocess.run(
                    [sys.executable, "-c", script, str(path)],
                    env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                ).stdout.strip()

            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(list(pool.map(reserve, range(8))).count("True"), 1)
            self.assertEqual(reserve(0), "False")
            guard = ReminderGuard(path)
            guard.completed(OWNER, BeadId("hv-first"))
            self.assertTrue(guard.reserve(OWNER, already_continued=True))
            guard.completed(OWNER, BeadId("hv-first"))
            self.assertFalse(guard.reserve(OWNER, already_continued=True))
            guard.completed(OWNER, BeadId("hv-second"))
            self.assertTrue(guard.reserve(OWNER, already_continued=True))
            another = Owner(OWNER.task, CodexTurnId("another-turn"))
            self.assertFalse(guard.reserve(another, already_continued=True))
            self.assertTrue(guard.reserve(another, already_continued=False))
            guard.interrupt(OWNER)
            guard.completed(OWNER, BeadId("hv-third"))
            self.assertFalse(guard.reserve(OWNER, already_continued=False))
            guard.discard(OWNER)
            with sqlite3.connect(path) as connection:
                rows: object = connection.execute(
                    "SELECT turn FROM reminders WHERE task = 'task-1'"
                ).fetchall()
            self.assertEqual(rows, [("another-turn",)])

    def test_native_stop_checks_scope_capacity_and_observes_real_completions(
        self,
    ) -> None:
        with private_server() as (connection, _), cli_fixture(connection) as cli:
            enroll(cli)
            self.assertEqual(hook(cli), {})
            first = cli.add("First artifact")
            second = cli.add("Second artifact", "--depends-on", first)
            third = cli.add("Third artifact", "--depends-on", second)
            self.assertEqual(hook(cli, task="unmanaged"), {})
            enroll(cli, "vizier")
            self.assertEqual(hook(cli), {})
            enroll(cli)
            guards = Guards(cli.state / "locks")
            guards.stop_mutations("Emergency maintenance")
            self.assertEqual(hook(cli), {})
            with guards.maintenance():
                pass
            cli.call("config", "capacity", "--global-limit", "1")
            other = ("--owner", "other", "--turn", "other-turn")
            cli.call("task", "claim", first, *SCOPE, *other)
            extra = cli.add("Ready but no slot")
            self.assertEqual(hook(cli), {})
            cli.call(
                "task",
                "defer",
                first,
                *SCOPE,
                *other,
                "--reason",
                "checkpoint",
                "--note",
                "Free the slot",
            )
            cli.call("task", "settle", first, *SCOPE, *other)
            cli.call("task", "resume", first, *SCOPE)
            self.assertEqual(hook(cli).get("decision"), "block")
            self.assertEqual(hook(cli), {})
            self.assertEqual(hook(cli, continued=True), {})
            self.assertEqual(cli.call("status")["global_owned"], 0)
            finish(cli, first)
            self.assertEqual(hook(cli, continued=True).get("decision"), "block")
            self.assertEqual(hook(cli, continued=True), {})
            finish(cli, second)
            self.assertEqual(hook(cli, continued=True).get("decision"), "block")
            finish(cli, third)
            finish(cli, extra)
            self.assertEqual(hook(cli, continued=True), {})

    def test_interrupt_preserves_owner_and_replays_a_failed_pause_without_takeover(
        self,
    ) -> None:
        with private_server() as (connection, _), cli_fixture(connection) as cli:
            enroll(cli)
            bead = cli.add("Interrupt this work")
            cli.add("Other ready work")
            cli.call("task", "claim", bead, *SCOPE, *WORKER)
            result = hook(cli, "Interrupt")
            self.assertIn(bead, str(result.get("systemMessage")))
            task = record(cli.call("task", "show", bead)["task"])
            self.assertEqual(record(task["state"])["status"], "deferred")
            self.assertEqual(cli.call("status")["global_owned"], 1)
            self.assertEqual(hook(cli), {})
            cli.call("task", "resume", bead, *SCOPE, expected="RecoveryRequired")
            cli.call("task", "settle", bead, *SCOPE, *WORKER)
            cli.call("task", "resume", bead, *SCOPE, expected="Paused")
            cli.call("task", "resume", bead, *SCOPE, "--user-authorized")
            current = ("--owner", "task-1", "--turn", "turn-2")
            cli.call("task", "claim", bead, *SCOPE, *current)
            before = cli.call("task", "show", bead)
            self.assertEqual(hook(cli, "Interrupt"), {})
            self.assertEqual(cli.call("task", "show", bead), before)
            # Break routing before the hook reads Beads; the native stop still
            # survives locally and is applied before a later turn can enter.
            metadata = connection.directory / ".beads/metadata.json"
            contents = metadata.read_text()
            metadata.write_text("{}")
            failed = hook(cli, "Interrupt", turn="turn-2")
            self.assertIn("systemMessage", failed)
            metadata.write_text(contents)
            cli.call(
                "task",
                "enter-turn",
                bead,
                *SCOPE,
                "--owner",
                "task-1",
                "--previous-turn",
                "turn-2",
                "--turn",
                "turn-3",
                expected="Paused",
            )
            self.assertEqual(cli.call("status")["global_owned"], 1)
            paused = record(cli.call("task", "show", bead)["task"])
            self.assertEqual(record(paused["state"])["status"], "deferred")

    def test_corrupt_guard_and_unknown_native_payload_never_request_continuation(
        self,
    ) -> None:
        with private_server() as (connection, _), cli_fixture(connection) as cli:
            enroll(cli)
            bead = BeadId(cli.add("First artifact"))
            next_bead = BeadId(cli.add("Next artifact"))
            guard = cli.state / "locks/reminders.sqlite3"
            guard.write_bytes(b"not a database")
            self.assertNotIn("decision", hook(cli))
            store = BeadsStore(BeadsProcess(connection, "observation-test"))
            service = TaskService(store, Guards(cli.state / "locks"))
            project = ProjectId("search")
            service.claim(project, OWNER, bead)
            service.advance(bead, project, OWNER, ReviewingArtifact("/tmp/report.md"))
            diagnostic = io.StringIO()
            with warnings.catch_warnings(), redirect_stderr(diagnostic):
                warnings.simplefilter("error")
                result = service.complete(
                    bead,
                    project,
                    OWNER,
                    "Delivered",
                    ArtifactDelivery("/tmp/report.md"),
                )
            self.assertIsInstance(result.state, Done)
            self.assertEqual(store.get(bead), result)
            self.assertIn("observation was not saved", diagnostic.getvalue())
            # A closed diagnostic stream must also preserve acknowledged work.
            diagnostic.close()
            with warnings.catch_warnings(), redirect_stderr(diagnostic):
                warnings.simplefilter("error")
                service.defer(
                    next_bead, project, PauseReason.USER, "Pause", expected_owner=None
                )
                resumed = service.resume(next_bead, project, user_authorized=True)
            self.assertIsInstance(resumed.state, Queued)
            self.assertEqual(store.get(next_bead), resumed)
            self.assertEqual(cli.call("status")["global_owned"], 0)
            self.assertNotIn("decision", hook(cli, turn=""))
            self.assertNotIn("decision", hook(cli, "Unknown"))
