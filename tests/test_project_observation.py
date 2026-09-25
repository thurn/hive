"""Real files, native claims and public reports cover expanded observation."""

import json
import sqlite3
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

from server_fixture import private_server
from test_bead_cost import command, last, setup
from test_links import bd
from test_usage import counters

from hive.collection import sweep
from hive.collection_registry import CollectionRegistry
from hive.identity import SourceCommit, ThreadId
from hive.jsonvalue import sequence
from hive.launch_context import LaunchContext
from hive.project_config import Project
from hive.usage_store import UsageStore

PARENT = ThreadId("01a00000-0000-7000-8000-000000000001")
CHILD = ThreadId("01a00000-0000-7000-8000-000000000002")


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def native_index(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE threads(id TEXT PRIMARY KEY,rollout_path TEXT,cwd TEXT,created_at_ms INTEGER,updated_at_ms INTEGER,source TEXT,title TEXT,agent_role TEXT,thread_source TEXT)"
        )


def transcript(path: Path, task: str, response: str, at: datetime) -> None:
    records = [
        {"type": "session_meta", "payload": {"id": task}},
        {
            "type": "turn_context",
            "payload": {"turn_id": "same-turn", "model": "gpt-6-astra"},
        },
        {
            "type": "token_usage_record",
            "payload": {
                "thread_id": task,
                "session_id": task,
                "turn_id": "same-turn",
                "response_id": response,
                "usage": counters(),
            },
        },
    ]
    path.write_text(
        "".join(json.dumps({**r, "timestamp": at.isoformat()}) + "\n" for r in records)
    )


def session(
    index: Path,
    path: Path,
    task: str,
    cwd: Path,
    at: datetime,
    parent: str | None = None,
    other: bool = False,
) -> None:
    source = (
        {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}
        if parent
        else ({"subagent": {"other": "guardian"}} if other else "cli")
    )
    with sqlite3.connect(index) as db:
        db.execute(
            "INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,NULL)",
            (
                task,
                str(path),
                str(cwd),
                int(at.timestamp() * 1000),
                int(at.timestamp() * 1000),
                json.dumps(source),
                "session",
                None,
            ),
        )


def launch(root: Path, repo: Path, beads: Path | None = None) -> LaunchContext:
    return LaunchContext(
        SourceCommit("source"),
        Path(__file__).resolve().parents[1],
        root / "state",
        beads or root / "missing-beads",
        0,
        repo,
        root / "claude",
        (Project("sample", repo, date(2026, 9, 24)),),
    )


class ProjectObservationTests(unittest.TestCase):
    def test_git_membership_cutoff_claude_and_spawned_folding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init", "-q")
            git(
                repo,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "initial",
            )
            external = root / "codex-worktree"
            git(repo, "worktree", "add", "--detach", str(external))
            index = root / "native.sqlite3"
            native_index(index)
            at = datetime(2026, 9, 25, tzinfo=UTC)
            (root / "repo2").mkdir()
            (root / "outside").mkdir()
            locations = [
                repo,
                repo / ".worktrees/removed",
                external,
                root / "repo2",
                root / "removed-external",
                repo,
                repo,
            ]
            ids: list[str] = []
            for n, location in enumerate(locations, 10):
                task = str(UUID(int=n))
                ids.append(task)
                path = root / f"{task}.jsonl"
                transcript(path, task, f"r-{n}", at)
                session(
                    index,
                    path,
                    task,
                    location,
                    at - timedelta(days=2) if n == 15 else at,
                    other=n == 16,
                )
            child_path = root / f"{CHILD}.jsonl"
            transcript(child_path, CHILD, "child-response", at)
            session(index, child_path, CHILD, root / "outside", at, parent=ids[0])
            claude = root / "claude/native"
            claude.mkdir(parents=True)
            claude_id = str(UUID(int=30))
            (claude / f"{claude_id}.jsonl").write_text(
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": claude_id,
                        "cwd": str(repo),
                        "timestamp": at.isoformat(),
                        "message": {"content": "fixture"},
                    }
                )
                + "\n"
            )
            context = launch(root, repo)
            result: dict[str, object] = {}
            for _ in range(5):
                result = sweep(context, index, 32)
                self.assertIsNone(result["discovery_error"], result)
                if result["discovery_behind"] is False:
                    break
            sweep(context, index, 32)
            store = UsageStore(context.state / "telemetry.sqlite3")
            self.assertEqual(store.report(ThreadId(ids[0]))["observed_responses"], 2)
            self.assertEqual(store.report(CHILD)["observed_responses"], 0)
            for task in [ids[1], ids[2], ids[6]]:
                self.assertEqual(store.report(ThreadId(task))["observed_responses"], 1)
            for task in [ids[3], ids[4], ids[5]]:
                self.assertEqual(store.report(ThreadId(task))["observed_responses"], 0)
            self.assertEqual(CollectionRegistry(store).status()["unresolved_cwd"], 1)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT task FROM collection_tasks WHERE task=?", (claude_id,)
                    ).fetchone(),
                    (claude_id,),
                )
                self.assertEqual(
                    db.execute(
                        "SELECT task,agent FROM responses WHERE response=?",
                        ("child-response",),
                    ).fetchone(),
                    (ids[0], CHILD),
                )
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM turn_models WHERE task=?", (ids[0],)
                    ).fetchone(),
                    (2,),
                )
            disabled = replace(context, projects=(Project("sample", repo),))
            sweep(disabled, index, 32)
            with sqlite3.connect(store.path) as db:
                self.assertIsNone(
                    db.execute(
                        "SELECT task FROM collection_tasks WHERE task=?", (claude_id,)
                    ).fetchone()
                )
            for _ in range(3):
                sweep(context, index, 32)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT task FROM collection_tasks WHERE task=?", (claude_id,)
                    ).fetchone(),
                    (claude_id,),
                )
            # A new child appears without its own independent card/task.
            self.assertTrue(result["event_retention_skipped"])

    def test_native_child_claim_overrides_root_and_migrates_retained_cost_once(
        self,
    ) -> None:
        with private_server() as (beads, _), tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            setup(root, beads)
            first = str(
                bd(
                    beads,
                    "create",
                    "Parent work",
                    "--metadata",
                    json.dumps({"hive_origin_thread": CHILD}),
                )["id"]
            )
            bd(beads, "--actor", PARENT, "update", first, "--claim")
            at = last(beads, first) + timedelta(milliseconds=1)
            index = root / "native.sqlite3"
            native_index(index)
            context = launch(root, root, beads.directory)
            # Missing native metadata must not permanently establish self-roots.
            sweep(context, index, 32)
            parent_path, child_path = root / "parent.jsonl", root / "child.jsonl"
            transcript(parent_path, PARENT, "parent-r", at)
            transcript(child_path, CHILD, "child-r", at)
            session(index, parent_path, PARENT, root, at)
            session(index, child_path, CHILD, root / "outside", at, parent=PARENT)
            # Existing stored child and its retained rates must move as one source.
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                CHILD,
                "--transcript",
                str(child_path),
            )
            before = command(root, "cost", "--task", CHILD)["priced_subset_usd"]
            context = launch(root, root, beads.directory)
            for _ in range(8):
                result = sweep(context, index, 32)
                if (
                    result["bead_events_caught_up"] is True
                    and result["discovery_behind"] is False
                ):
                    break
            first_cost = command(root, "cost", "--bead", first)
            self.assertEqual(first_cost["attributed_usd"], "0.002000000000")
            self.assertEqual(
                command(root, "cost", "--task", PARENT)["priced_subset_usd"],
                "0.002000000000",
            )
            self.assertEqual(before, "0.001000000000")
            second = str(
                bd(
                    beads,
                    "create",
                    "Child work",
                    "--metadata",
                    json.dumps({"hive_origin_thread": PARENT}),
                )["id"]
            )
            bd(beads, "--actor", CHILD, "update", second, "--claim")
            child_at = last(beads, second) + timedelta(milliseconds=1)
            extra = root / "extra.jsonl"
            transcript(extra, CHILD, "child-r2", child_at)
            with child_path.open("ab") as stream:
                stream.write(extra.read_bytes().splitlines(keepends=True)[-1])
            for _ in range(4):
                sweep(context, index, 32)
            self.assertEqual(
                command(root, "cost", "--bead", second)["attributed_usd"],
                "0.001000000000",
            )
            # A child with any own intervals no longer falls back outside them.
            self.assertEqual(
                command(root, "cost", "--bead", first)["attributed_usd"],
                "0.001000000000",
            )
            reconciled = command(root, "cost", "--reconcile")
            self.assertTrue(reconciled["balanced"])
            self.assertEqual(len(sequence(reconciled["threads"], "threads")), 1)
            copied = root / "copied.jsonl"
            transcript(copied, PARENT, "child-r", at)
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                PARENT,
                "--transcript",
                str(copied),
            )
            health = command(root, "telemetry", "usage", "--task", PARENT)
            self.assertIn(
                "Conflicting native response identity", str(health["recent_gaps"])
            )

    def test_changed_session_runs_ahead_of_one_hundred_idle_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = root / "native.sqlite3"
            native_index(index)
            at = datetime(2026, 9, 25, tzinfo=UTC)
            for n in range(100, 201):
                task = str(UUID(int=n))
                path = root / f"{task}.jsonl"
                transcript(path, task, f"r-{n}", at)
                session(index, path, task, root, at)
            context = launch(root, root)
            for _ in range(5):
                sweep(context, index, 64)
            task = ThreadId(str(UUID(int=200)))
            store = UsageStore(context.state / "telemetry.sqlite3")
            self.assertEqual(store.report(task)["observed_responses"], 1)
            extra = root / "extra.jsonl"
            transcript(extra, task, "changed-request", at + timedelta(seconds=1))
            with (root / f"{task}.jsonl").open("ab") as stream:
                stream.write(extra.read_bytes().splitlines(keepends=True)[-1])
            result = sweep(context, index, 1)
            self.assertEqual(result["attempted"], 1)
            self.assertEqual(store.report(task)["observed_responses"], 2)
            index.unlink()
            failure = sweep(context, index, 1)
            self.assertIsNotNone(failure["discovery_error"])
            self.assertTrue(failure["event_retention_skipped"])
            self.assertEqual(store.report(task)["observed_responses"], 2)
