"""Diagnostic metadata follows native transcript journeys without storing text."""

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from test_bead_cost import command
from test_claude import THREAD, assistant
from test_project_observation import CHILD, PARENT, launch, native_index, session

from hive.collection import sweep
from hive.diagnostic_report import events, tools
from hive.diagnostic_roles import at as role_at
from hive.identity import ThreadId
from hive.jsonvalue import record, sequence
from hive.usage_store import UsageStore

SECRET = "PRIVATE_TRANSCRIPT_SENTINEL_NEVER_STORE"


def native(kind: str, payload: dict[str, object], at: datetime) -> bytes:
    return (
        json.dumps({"type": kind, "payload": payload, "timestamp": at.isoformat()})
        + "\n"
    ).encode()


class DiagnosticTests(unittest.TestCase):
    def test_codex_chunks_waits_retries_roles_and_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "native.jsonl"
            start = datetime.now(UTC) - timedelta(hours=3)
            data = native("session_meta", {"id": PARENT}, start)
            data += native(
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "$executor " + SECRET}],
                },
                start,
            )
            for n in range(21):
                at = start + timedelta(seconds=100 * n)
                data += native(
                    "response_item",
                    {
                        "type": "function_call",
                        "name": "Read",
                        "call_id": f"read-{n}",
                        "arguments": json.dumps({"path": SECRET}),
                    },
                    at,
                )
                data += native(
                    "response_item",
                    {
                        "type": "function_call_output",
                        "call_id": f"read-{n}",
                        "output": SECRET,
                    },
                    at + timedelta(seconds=70 if n == 20 else 1),
                )
            at = start + timedelta(seconds=2200)
            for n in range(3):
                data += native(
                    "response_item",
                    {
                        "type": "custom_tool_call",
                        "name": "exec",
                        "call_id": f"failure-{n}",
                        "input": 'text(await tools.exec_command({cmd: "false '
                        + SECRET
                        + '"}));',
                    },
                    at + timedelta(seconds=n * 3),
                )
                data += native(
                    "response_item",
                    {
                        "type": "custom_tool_call_output",
                        "call_id": f"failure-{n}",
                        "output": [
                            {
                                "type": "input_text",
                                "text": "Script completed\nWall time 1 seconds\nOutput:\n",
                            },
                            {
                                "type": "input_text",
                                "text": json.dumps(
                                    {
                                        "exit_code": 1,
                                        "wall_time_seconds": 1,
                                        "output": SECRET,
                                    }
                                ),
                            },
                        ],
                    },
                    at + timedelta(seconds=n * 3 + 1),
                )
            at += timedelta(seconds=30)
            data += native(
                "response_item",
                {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "sleep",
                    "input": 'text(await tools.exec_command({cmd: "tg --json --repository hive approve candidate --wait"}));',
                },
                at,
            )
            data += native(
                "response_item",
                {
                    "type": "custom_tool_call_output",
                    "call_id": "sleep",
                    "output": "Script completed\nWall time 70 seconds\nOutput:\n"
                    + json.dumps(
                        {"exit_code": 0, "wall_time_seconds": 70, "output": SECRET}
                    ),
                },
                at + timedelta(seconds=70),
            )
            data += native(
                "response_item",
                {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "bad",
                    "input": SECRET,
                },
                at + timedelta(seconds=80),
            )
            data += native(
                "response_item",
                {
                    "type": "custom_tool_call_output",
                    "call_id": "bad",
                    "output": "unexpected " + SECRET,
                },
                at + timedelta(seconds=81),
            )
            data += native(
                "event_msg", {"type": "turn_aborted"}, at + timedelta(seconds=82)
            )
            data += native(
                "event_msg", {"type": "context_compacted"}, at + timedelta(seconds=83)
            )
            data += native(
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "$warden " + SECRET}],
                },
                at + timedelta(minutes=10),
            )
            path.write_bytes(data)
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                PARENT,
                "--transcript",
                str(path),
            )
            store = UsageStore(root / "state/telemetry.sqlite3")
            with store.connect(write=False) as db:
                calls = tools(db, PARENT)
                by_id = {str(c["call_id"]): c for c in calls}
                self.assertTrue(by_id["read-20"]["slow"])
                self.assertFalse(by_id["read-19"]["slow"])
                self.assertTrue(by_id["sleep"]["waiting"])
                self.assertFalse(by_id["sleep"]["slow"])
                self.assertTrue(
                    all(by_id[f"failure-{n}"]["retry_loop"] for n in range(3))
                )
                self.assertEqual(by_id["bad"]["status"], "unparsed")
                self.assertEqual(by_id["sleep"]["role"], "executor")
                kinds = {e["kind"] for e in events(db, PARENT)}
                self.assertTrue(
                    {"interrupt", "compaction", "idle", "role_change"} <= kinds
                )
                self.assertEqual(
                    role_at(db, PARENT, "", (at + timedelta(minutes=11)).isoformat()),
                    ("warden", False),
                )
                self.assertTrue(
                    record(sequence(by_id["sleep"]["commands"], "commands")[0])[
                        "waiting"
                    ]
                )
                self.assertTrue(
                    record(sequence(by_id["failure-2"]["commands"], "commands")[0])[
                        "retry_loop"
                    ]
                )
                count = len(calls)
            # A schema-13 store already at EOF must replay metadata during upgrade.
            with sqlite3.connect(store.path) as db:
                from dashboard_fixture import remove_dashboard

                remove_dashboard(db)
                for table in (
                    "tollgate_promotions",
                    "tollgate_mapping_state",
                    "tollgate_repositories",
                    "tollgate_mentions",
                    "tollgate_pending",
                    "tollgate_candidates",
                    "tollgate_branch_matches",
                    "tollgate_health",
                    "tool_calls",
                    "tool_commands",
                    "session_events",
                    "role_spans",
                    "diagnostic_titles",
                    "diagnostic_sessions",
                    "diagnostic_replays",
                ):
                    db.execute(f"DROP TABLE {table}")
                db.execute("PRAGMA user_version=13")
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                PARENT,
                "--transcript",
                str(path),
            )
            with store.connect(write=False) as db:
                self.assertEqual(len(tools(db, PARENT)), count)
            child = root / "child.jsonl"
            child.write_bytes(
                native("session_meta", {"id": CHILD}, start + timedelta(seconds=10))
            )
            store.collect(PARENT, child, agent=CHILD)
            with store.connect(write=False) as db:
                self.assertEqual(
                    role_at(
                        db, PARENT, CHILD, (at + timedelta(minutes=11)).isoformat()
                    ),
                    ("executor", True),
                )
            with child.open("ab") as stream:
                stream.write(
                    native(
                        "response_item",
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "$warden review"}
                            ],
                        },
                        at + timedelta(minutes=12),
                    )
                )
            store.collect(PARENT, child, agent=CHILD)
            with store.connect(write=False) as db:
                self.assertEqual(
                    role_at(
                        db, PARENT, CHILD, (at + timedelta(minutes=13)).isoformat()
                    ),
                    ("warden", False),
                )
            self.assertNotIn(SECRET.encode(), store.path.read_bytes())

    def test_claude_errors_human_wait_and_cached_prefix_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            start = datetime.now(UTC) - timedelta(hours=3)
            records: list[dict[str, object]] = [
                {
                    "type": "user",
                    "message": {
                        "content": "<command-name>/executor</command-name> " + SECRET
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "ask",
                                "name": "AskUserQuestion",
                                "input": {"question": SECRET},
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "ask",
                                "content": SECRET,
                            }
                        ]
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "denied",
                                "name": "Bash",
                                "input": {"command": SECRET},
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "denied",
                                "content": "Permission denied: " + SECRET,
                                "is_error": True,
                            }
                        ]
                    },
                },
                {"type": "system", "subtype": "api_error"},
                {"type": "system", "subtype": "compact_boundary"},
                {
                    "type": "user",
                    "message": {"content": "[Request interrupted by user] " + SECRET},
                },
            ]
            for ordinal, item in enumerate(records):
                if item.get("type") == "assistant":
                    base = record(json.loads(assistant(f"tool-request-{ordinal}", 10)))
                    observed_message = record(base["message"])
                    observed_message["content"] = record(item["message"])["content"]
                    base["message"] = observed_message
                    records[ordinal] = base
            path.write_text(
                "".join(
                    json.dumps(
                        {
                            **r,
                            "sessionId": THREAD,
                            "timestamp": (
                                start + timedelta(seconds=n * 10)
                            ).isoformat(),
                        }
                    )
                    + "\n"
                    for n, r in enumerate(records)
                )
            )
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(path),
            )
            store = UsageStore(root / "state/telemetry.sqlite3")
            with store.connect(write=False) as db:
                calls = tools(db, THREAD)
                self.assertEqual(
                    {c["call_id"]: c["status"] for c in calls},
                    {"ask": "ok", "denied": "error"},
                )
                kinds = {e["kind"] for e in events(db, THREAD)}
                self.assertTrue(
                    {
                        "human_wait",
                        "permission_denied",
                        "api_error",
                        "compaction",
                        "interrupt",
                        "role_change",
                    }
                    <= kinds
                )
            with path.open("a") as stream:
                stream.write(
                    json.dumps(
                        {
                            "type": "custom-title",
                            "sessionId": THREAD,
                            "customTitle": "📖 Sage fallback",
                        }
                    )
                    + "\n"
                )
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(path),
                "--from-start",
            )
            with store.connect(write=False) as db:
                self.assertEqual(
                    role_at(db, THREAD, "", (start + timedelta(minutes=1)).isoformat()),
                    ("executor", False),
                )
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(path),
                "--from-start",
            )
            with store.connect(write=False) as db:
                self.assertEqual(
                    role_at(db, THREAD, "", (start + timedelta(minutes=1)).isoformat()),
                    ("executor", False),
                )
            # Use real Claude request shapes to expose an expired rewritten cache.
            first = record(json.loads(assistant("request-1", 10)))
            second = record(json.loads(assistant("request-2", 20)))
            for n, value in enumerate((first, second)):
                value["timestamp"] = (start + timedelta(minutes=2 + n * 10)).isoformat()
                message = record(value["message"])
                message["usage"] = {
                    **record(message["usage"]),
                    "input_tokens": 10,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 100,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 100,
                        "ephemeral_1h_input_tokens": 0,
                    },
                    "output_tokens": 10,
                }
                value["message"] = message
                with path.open("a") as stream:
                    stream.write(json.dumps(value) + "\n")
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(path),
            )
            command(root, "cost", "--task", THREAD)
            with store.connect(write=False) as db:
                rewritten = [
                    e for e in events(db, THREAD) if e["kind"] == "cache_rewrite"
                ]
                self.assertEqual(len(rewritten), 1)
                self.assertGreater(int(str(rewritten[0]["amount_picos"])), 0)
            self.assertNotIn(SECRET.encode(), store.path.read_bytes())

    def test_late_parent_discovery_preserves_diagnostics_and_nested_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            start = datetime.now(UTC) - timedelta(hours=3)
            grandchild = ThreadId("01a00000-0000-7000-8000-000000000003")
            for task, role in (
                (PARENT, "executor"),
                (CHILD, "warden"),
                (grandchild, ""),
            ):
                path = root / f"rollout-{task}.jsonl"
                data = native("session_meta", {"id": task}, start)
                if role:
                    data += native(
                        "response_item",
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "$" + role}],
                        },
                        start,
                    )
                data += native(
                    "response_item",
                    {
                        "type": "function_call",
                        "name": "Read",
                        "call_id": "unfinished",
                        "arguments": "{}",
                    },
                    start + timedelta(seconds=1),
                )
                data += native(
                    "event_msg", {"type": "turn_aborted"}, start + timedelta(seconds=2)
                )
                path.write_bytes(data)
            child_path = root / f"rollout-{CHILD}.jsonl"
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                CHILD,
                "--transcript",
                str(child_path),
            )
            index = root / "native.sqlite3"
            native_index(index)
            for task, parent in ((PARENT, None), (CHILD, PARENT), (grandchild, CHILD)):
                session(
                    index,
                    root / f"rollout-{task}.jsonl",
                    task,
                    root,
                    start,
                    parent=parent,
                )
            context = launch(root, root)
            for _ in range(3):
                sweep(context, index, 32)
            store = UsageStore(context.state / "telemetry.sqlite3")
            with store.connect(write=False) as db:
                self.assertEqual(tools(db, CHILD), [])
                calls = tools(db, PARENT)
                self.assertEqual(len(calls), 3)
                self.assertTrue(all(c["status"] == "orphaned" for c in calls))
                self.assertEqual(
                    role_at(
                        db,
                        PARENT,
                        grandchild,
                        (start + timedelta(seconds=10)).isoformat(),
                    ),
                    ("warden", True),
                )
                self.assertEqual(
                    len([e for e in events(db, PARENT) if e["kind"] == "interrupt"]), 3
                )
            store.collect(PARENT, child_path, agent=CHILD, from_start=True)
            with store.connect(write=False) as db:
                self.assertEqual(
                    len([e for e in events(db, PARENT) if e["kind"] == "interrupt"]), 3
                )
