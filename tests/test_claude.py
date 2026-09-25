"""Claude collection through public commands and bounded sweeps."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from test_contention import hive
from test_usage import line

from hive.collection import sweep
from hive.collection_registry import CollectionRegistry
from hive.identity import Host, SourceCommit, ThreadId
from hive.jsonvalue import integer, record
from hive.launch_context import LaunchContext
from hive.thread_links import ThreadLink
from hive.usage_store import UsageStore

THREAD = ThreadId("a521cf51-c055-4715-832b-fc19921ee482")
OTHER = ThreadId("a521cf51-c055-4715-832b-fc19921ee483")


def assistant(
    identity: str,
    output: int = 10,
    *,
    agent: str | None = None,
    thread: str = THREAD,
    complete: bool = True,
    usage_changes: dict[str, object] | None = None,
) -> bytes:
    usage: dict[str, object] = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 100,
        "output_tokens": output,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 10,
            "ephemeral_1h_input_tokens": 20,
        },
        "speed": "standard",
        "service_tier": "standard",
        "inference_geo": "not_available",
    }
    usage.update(usage_changes or {})
    value: dict[str, object] = {
        "type": "assistant",
        "timestamp": "2026-09-24T00:00:00Z",
        "sessionId": thread,
        "requestId": identity,
        "message": {
            "id": "msg_" + identity,
            "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "answer"}],
            "usage": usage,
            "stop_reason": "end_turn" if complete else None,
        },
    }
    if agent is not None:
        value.update(agentId=agent, isSidechain=True)
    return (json.dumps(value) + "\n").encode()


class ClaudeTests(unittest.TestCase):
    def test_commands_update_partial_requests_and_keep_each_file_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            path.write_bytes(
                b'{"type":"queue-operation"}\n'
                + assistant("req_main", 1, complete=False)
            )
            first = hive(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(path),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIsNone(record(json.loads(first.stdout))["error"])
            with path.open("ab") as stream:
                stream.write(assistant("req_main", 10))
            again = hive(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(path),
            )
            self.assertEqual(again.returncode, 0, again.stderr)
            child = root / THREAD / "subagents/agent-child.jsonl"
            child.parent.mkdir(parents=True)
            child.write_bytes(
                assistant("req_child", 5, agent="child")
                + assistant("req_main", 10, agent="child")
            )
            collected = hive(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(child),
            )
            self.assertEqual(collected.returncode, 0, collected.stderr)
            report = hive(root, "telemetry", "usage", "--task", THREAD)
            self.assertEqual(report.returncode, 0, report.stderr)
            result = record(json.loads(report.stdout))
            self.assertEqual(result["host"], "claude")
            self.assertEqual(result["observed_responses"], 2)
            self.assertEqual(result["parse_gaps"], 0)
            self.assertEqual(
                record(result["known_tokens"]),
                {
                    "input_tokens": 264,
                    "cached_input_tokens": 200,
                    "cache_write_input_tokens": 20,
                    "cache_write_1h_input_tokens": 40,
                    "output_tokens": 15,
                    "reasoning_output_tokens": 0,
                },
            )
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM sources").fetchone(), (2,)
                )
                self.assertEqual(
                    db.execute(
                        "SELECT complete FROM responses WHERE response='req_main'"
                    ).fetchone(),
                    (1,),
                )
                self.assertEqual(
                    db.execute(
                        "SELECT agent FROM responses WHERE response='req_main'"
                    ).fetchone(),
                    (None,),
                )
            self.assertEqual(
                record(
                    json.loads(
                        hive(
                            root,
                            "telemetry",
                            "collect",
                            "--task",
                            THREAD,
                            "--transcript",
                            str(path),
                        ).stdout
                    )
                )["read_bytes"],
                0,
            )

    def test_invalid_counters_and_iterations_do_not_hide_later_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            old = record(json.loads(assistant("req_old")))
            old_usage = record(record(old["message"])["usage"])
            old_usage.pop("cache_creation")
            record_message = record(old["message"])
            record_message["usage"] = old_usage
            old["message"] = record_message
            synthetic = record(
                json.loads(
                    assistant(
                        "synthetic",
                        0,
                        usage_changes={
                            "input_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    )
                )
            )
            message = record(synthetic["message"])
            message["model"] = "<synthetic>"
            synthetic["message"] = message
            path.write_bytes(
                assistant("req_bool", usage_changes={"input_tokens": True})
                + assistant("req_negative", usage_changes={"output_tokens": -1})
                + assistant(
                    "req_large", usage_changes={"cache_read_input_tokens": 2**63}
                )
                + assistant(
                    "req_bad_ttl", usage_changes={"cache_creation_input_tokens": 31}
                )
                + assistant(
                    "req_iteration",
                    usage_changes={"iterations": [{"type": "fallback"}]},
                )
                + (json.dumps(old) + "\n" + json.dumps(synthetic) + "\n").encode()
                + assistant("req_valid")
            )
            store = UsageStore(root / "state/telemetry.sqlite3")
            store.collect(THREAD, path)
            report = store.report(THREAD)
            self.assertEqual(report["observed_responses"], 3)
            self.assertEqual(report["parse_gaps"], 4)
            with sqlite3.connect(store.path) as db:
                flags = {
                    r[0]: json.loads(r[1])
                    for r in db.execute("SELECT response,flags FROM responses")
                }
            self.assertIn("ttl_assumed", flags["req_old"])
            self.assertIn("unsupported_iteration", flags["req_iteration"])

    def test_identity_pending_mismatch_and_symlinks_do_not_advance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            store = UsageStore(root / "usage.sqlite3")
            path.write_bytes(b'{"type":"queue-operation"}\n')
            pending = store.collect(THREAD, path)
            self.assertEqual(pending["position"], 0)
            self.assertIsNotNone(pending["error"])
            with path.open("ab") as stream:
                stream.write(assistant("req_wrong", thread=OTHER))
            self.assertEqual(store.collect(THREAD, path)["position"], 0)
            self.assertEqual(store.report(THREAD)["observed_responses"], 0)
            path.write_bytes(assistant("req_ok"))
            self.assertIsNone(store.collect(THREAD, path)["error"])
            alias = root / "alias" / f"{THREAD}.jsonl"
            alias.parent.mkdir()
            alias.symlink_to(path)
            self.assertIn("symlink", str(store.collect(THREAD, alias)["error"]))

    def test_sweep_finds_uuid4_codex_late_subagents_and_both_host_ambiguity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "projects"
            project = projects / "slug"
            project.mkdir(parents=True)
            main = project / f"{THREAD}.jsonl"
            main.write_bytes(assistant("req_main"))
            codex = root / "rollout.jsonl"
            codex.write_bytes(line("session_meta", {"id": OTHER}))
            index = root / "native.sqlite3"
            with sqlite3.connect(index) as db:
                db.execute(
                    "CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)"
                )
                db.execute("INSERT INTO threads VALUES (?,?)", (OTHER, str(codex)))
            context = LaunchContext(
                SourceCommit("fixture"),
                root,
                root / "state",
                root / "missing-beads",
                0,
                root,
                projects,
            )
            store = UsageStore(context.state / "telemetry.sqlite3")
            registry = CollectionRegistry(store)
            registry.refresh(
                (
                    ThreadLink(THREAD, "hv-test", "creator", False),
                    ThreadLink(OTHER, "hv-test", "executor", False),
                ),
                (),
                None,
            )
            result = sweep(context, index, 32)
            self.assertEqual(result["attempted"], 2)
            self.assertEqual(registry.cached_host(THREAD), Host.CLAUDE)
            self.assertEqual(registry.cached_host(OTHER), Host.CODEX)
            child = project / THREAD / "subagents/agent-late.jsonl"
            child.parent.mkdir(parents=True)
            child.write_bytes(assistant("req_late", agent="late"))
            child.with_suffix(".meta.json").write_text(
                json.dumps(
                    {"toolUseId": "tool_agent", "agentType": "test", "spawnDepth": 1}
                )
            )
            sweep(context, index, 32)
            self.assertEqual(store.report(THREAD)["observed_responses"], 2)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT agent,spawn_depth FROM claude_agents"
                    ).fetchone(),
                    ("late", 1),
                )
            with sqlite3.connect(index) as db:
                db.execute("INSERT INTO threads VALUES (?,?)", (THREAD, str(codex)))
            with main.open("ab") as stream:
                stream.write(assistant("req_ambiguous"))
            sweep(context, index, 32)
            self.assertIsNone(registry.cached_host(THREAD))
            self.assertEqual(store.report(THREAD)["observed_responses"], 2)

    def test_index_outage_requires_cached_host_and_duplicates_are_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "projects"
            project = projects / "slug"
            project.mkdir(parents=True)
            main = project / f"{THREAD}.jsonl"
            main.write_bytes(assistant("req_main"))
            context = LaunchContext(
                SourceCommit("fixture"),
                root,
                root / "state",
                root / "missing-beads",
                0,
                root,
                projects,
            )
            store = UsageStore(context.state / "telemetry.sqlite3")
            registry = CollectionRegistry(store)
            registry.refresh(
                (ThreadLink(THREAD, "hv-test", "creator", False),), (), None
            )
            sweep(context, root / "missing-index", 32)
            self.assertEqual(store.report(THREAD)["observed_responses"], 0)
            registry.attempted(THREAD, None, main, Host.CLAUDE)
            sweep(context, root / "missing-index", 32)
            self.assertEqual(store.report(THREAD)["observed_responses"], 1)
            second = projects / "other" / main.name
            second.parent.mkdir()
            second.write_bytes(main.read_bytes())
            sweep(context, root / "missing-index", 32)
            self.assertIsNone(registry.cached_host(THREAD))
            self.assertIn("multiple", str(registry.status()["recent_failures"]))

    def test_known_partial_replays_are_idempotent_but_new_decreases_are_gaps(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / f"{THREAD}.jsonl"
            main.write_bytes(
                assistant("req_stream", 1, complete=False) + assistant("req_stream", 10)
            )
            store = UsageStore(root / "usage.sqlite3")
            store.collect(THREAD, main)
            store.collect(THREAD, main, from_start=True)
            child = root / THREAD / "subagents/agent-copy.jsonl"
            child.parent.mkdir(parents=True)
            child.write_bytes(assistant("req_stream", 1, complete=False, agent="copy"))
            store.collect(THREAD, child)
            self.assertEqual(store.report(THREAD)["parse_gaps"], 0)
            with main.open("ab") as stream:
                stream.write(assistant("req_stream", 3, complete=False))
            store.collect(THREAD, main)
            self.assertEqual(store.report(THREAD)["parse_gaps"], 1)
            self.assertEqual(
                record(store.report(THREAD)["known_tokens"])["output_tokens"], 10
            )

    def test_mixed_file_failures_remain_visible_and_reads_stay_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "projects"
            main = projects / "slug" / f"{THREAD}.jsonl"
            main.parent.mkdir(parents=True)
            main.write_bytes(assistant("req_wrong", thread=OTHER))
            child = main.parent / THREAD / "subagents/agent-valid.jsonl"
            child.parent.mkdir(parents=True)
            child.write_bytes(
                b"".join(assistant(f"req_{i}", agent="valid") for i in range(3500))
            )
            index = root / "index.sqlite3"
            with sqlite3.connect(index) as db:
                db.execute(
                    "CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)"
                )
            context = LaunchContext(
                SourceCommit("test"),
                root,
                root / "state",
                root / "missing",
                0,
                root,
                projects,
            )
            store = UsageStore(context.state / "telemetry.sqlite3")
            registry = CollectionRegistry(store)
            registry.refresh(
                (ThreadLink(THREAD, "hv-test", "creator", False),), (), None
            )
            batch = sweep(context, index, 32)
            self.assertIn("another task", str(batch["results"]))
            self.assertIn("another task", str(registry.status()["recent_failures"]))
            self.assertGreater(
                integer(store.report(THREAD)["observed_responses"], "responses"), 0
            )
            self.assertLess(
                integer(store.report(THREAD)["observed_responses"], "responses"), 3500
            )
            for _ in range(5):
                result = store.collect(THREAD, child)
                self.assertLessEqual(integer(result["read_bytes"], "bytes"), 1048576)
                if result["remaining_bytes"] == 0:
                    break
            self.assertEqual(store.report(THREAD)["observed_responses"], 3500)

    def test_v1_upgrade_preserves_codex_cursor_and_adds_claude_collection(self) -> None:
        from test_usage import ROOT

        from hive.errors import HiveError

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "usage.sqlite3")
            with sqlite3.connect(store.path) as db:
                db.executescript((ROOT / "tests/fixtures/telemetry-v1.sql").read_text())
                db.execute(
                    "INSERT INTO collection_tasks(task,host) VALUES (?,?)",
                    (OTHER, "codex"),
                )
            with self.assertRaises(HiveError):
                store.report(THREAD)
            main = root / f"{THREAD}.jsonl"
            main.write_bytes(assistant("req_v2"))
            store.collect(THREAD, main)
            self.assertEqual(store.report(THREAD)["observed_responses"], 1)
            self.assertEqual(CollectionRegistry(store).cached_host(OTHER), Host.CODEX)
