"""Real transcript, SQLite, and fresh-command journeys for derived usage."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hive.identity import CodexTaskId
from hive.jsonvalue import integer
from hive.usage_store import UsageStore

ROOT: Path = Path(__file__).resolve().parents[1]
TASK: CodexTaskId = CodexTaskId("native-task")


def line(kind: str, payload: dict[str, object]) -> bytes:
    return (
        json.dumps(
            {"type": kind, "timestamp": "2026-09-23T08:00:00Z", "payload": payload}
        )
        + "\n"
    ).encode()


def header(task: str = TASK) -> bytes:
    return line("session_meta", {"id": task})


def response(identity: str, usage: object = None) -> bytes:
    return line(
        "token_usage_record",
        {
            "thread_id": TASK,
            "session_id": TASK,
            "turn_id": "turn-1",
            "response_id": identity,
            "usage": usage,
        },
    )


def counters(amount: int = 100) -> dict[str, object]:
    return {
        "input_tokens": amount,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": amount,
    }


class UsageTests(unittest.TestCase):
    def test_invalid_native_range_cannot_stall_later_records_or_overflow_reports(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "native.jsonl"
            path.write_bytes(
                header()
                + response("invalid", counters(2**63))
                + response("normal", counters())
                + response("large-1", counters(2**62))
                + response("large-2", counters(2**62))
            )
            store = UsageStore(root / "observations.sqlite3")
            store.collect(TASK, path)
            report = store.report(TASK)
            self.assertEqual(report["observed_responses"], 3)
            self.assertEqual(report["parse_gaps"], 1)
            self.assertEqual(
                report["known_tokens"],
                {k: v for k, v in counters(2**63 + 100).items() if k != "total_tokens"},
            )
            self.assertEqual(store.collect(TASK, path)["read_bytes"], 0)
            self.assertEqual(store.report(TASK)["known_tokens"], report["known_tokens"])

    def test_bounded_chunks_skip_oversized_records_and_surface_conflicting_usage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "native.jsonl"
            path.write_bytes(
                header()
                + b"x" * 800_000
                + b"\n"
                + response("a", counters(0))
                + response("b")
                + response("a", counters(1))
                + response("bool", {**counters(), "input_tokens": True})
                + response("bad-cache", {**counters(), "cached_input_tokens": 101})
            )
            store = UsageStore(root / "observations.sqlite3")
            amounts: list[int] = []
            for _ in range(4):
                result = store.collect(TASK, path, budget=300_000)
                read_bytes = result["read_bytes"]
                self.assertIsInstance(read_bytes, int)
                if isinstance(read_bytes, int):
                    amounts.append(read_bytes)
            self.assertTrue(all(amount <= 300_000 for amount in amounts))
            report = store.report(TASK)
            self.assertEqual(report["observed_responses"], 2)
            self.assertEqual(report["responses_missing_usage"], 1)
            self.assertEqual(report["parse_gaps"], 4)
            self.assertEqual(
                report["known_tokens"],
                {k: v for k, v in counters(0).items() if k != "total_tokens"},
            )
            # An archived copy with a different inode is re-read without recounting.
            copied = root / "copy.jsonl"
            copied.write_bytes(header() + response("a", counters(0)) + response("b"))
            store.collect(TASK, copied)
            self.assertEqual(store.report(TASK)["observed_responses"], 2)
            self.assertEqual(store.report(TASK)["parse_gaps"], 5)
            # Filename is never authority for session identity.
            wrong = root / "wrong.jsonl"
            wrong.write_bytes(header("another-task") + response("bad", counters()))
            self.assertIsInstance(store.collect(TASK, wrong)["error"], str)
            self.assertEqual(store.report(TASK)["observed_responses"], 2)

    def test_batch_boundary_is_not_mistaken_for_an_incomplete_native_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "native.jsonl"
            path.write_bytes(
                header() + b"".join(response(f"r-{i}", counters()) for i in range(3000))
            )
            store = UsageStore(root / "observations.sqlite3")
            for _ in range(20):
                result = store.collect(TASK, path, budget=300_000)
                self.assertFalse(result["incomplete_tail"])
                if result["remaining_bytes"] == 0:
                    break
            else:
                self.fail("Bounded collection stopped making progress")
            self.assertEqual(store.report(TASK)["observed_responses"], 3000)
            # In-place truncation is also visible and cannot recount retained IDs.
            path.write_bytes(header() + response("r-0", counters()))
            store.collect(TASK, path)
            report = store.report(TASK)
            self.assertEqual(report["observed_responses"], 3000)
            self.assertEqual(report["parse_gaps"], 1)

    def test_batch_failure_rolls_back_responses_and_cursor_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "native.jsonl"
            path.write_bytes(header() + response("a", counters()))
            store = UsageStore(root / "observations.sqlite3")
            store.report(TASK)
            with sqlite3.connect(store.path) as connection:
                connection.execute(
                    "CREATE TRIGGER fail_cursor BEFORE INSERT ON sources BEGIN SELECT RAISE(ABORT, 'interrupted batch'); END"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                store.collect(TASK, path)
            self.assertEqual(store.report(TASK)["observed_responses"], 0)
            self.assertIsNone(store.report(TASK)["last_scan"])
            with sqlite3.connect(store.path) as connection:
                connection.execute("DROP TRIGGER fail_cursor")
            store.collect(TASK, path)
            self.assertEqual(store.report(TASK)["observed_responses"], 1)

    def test_both_hosts_resume_replace_and_rollback_one_file_lifecycle(self) -> None:
        from test_claude import THREAD, assistant

        from hive.identity import Host

        for host in (Host.CODEX, Host.CLAUDE):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                task = TASK if host == Host.CODEX else THREAD
                path = root / (
                    "native.jsonl" if host == Host.CODEX else f"{task}.jsonl"
                )
                store = UsageStore(root / "usage.sqlite3")
                prefix = (
                    header()
                    if host == Host.CODEX
                    else (
                        json.dumps({"type": "user", "sessionId": task}) + "\n"
                    ).encode()
                )

                def request(identity: str, host: Host = host) -> bytes:
                    return (
                        response(identity, counters())
                        if host == Host.CODEX
                        else assistant(identity)
                    )

                padding = (
                    json.dumps({"type": "padding", "text": "x" * 140_000}) + "\n"
                ).encode()
                tail = request("tail")
                path.write_bytes(prefix + padding * 3 + request("first") + tail[:-12])
                positions: list[int] = []
                for _ in range(5):
                    batch = store.collect(task, path, budget=300_000)
                    self.assertIsNone(batch["error"])
                    self.assertLessEqual(integer(batch["read_bytes"], "bytes"), 300_000)
                    positions.append(integer(batch["position"], "position"))
                    if batch["incomplete_tail"]:
                        break
                self.assertGreater(len(positions), 1)
                self.assertEqual(positions, sorted(positions))
                self.assertTrue(store.report(task)["incomplete_tail"])
                self.assertEqual(store.report(task)["observed_responses"], 1)
                with path.open("ab") as stream:
                    stream.write(tail[-12:])
                self.assertFalse(store.collect(task, path)["incomplete_tail"])
                self.assertEqual(store.report(task)["observed_responses"], 2)
                replacement = root / "replacement"
                replacement.write_bytes(
                    prefix + request("tail") + request("replacement")
                )
                replacement.replace(path)
                store.collect(task, path)
                self.assertEqual(store.report(task)["observed_responses"], 3)
                self.assertIn(
                    "replaced or truncated", str(store.report(task)["recent_gaps"])
                )
                before = store.report(task)
                with path.open("ab") as stream:
                    stream.write(request("after_failure"))
                with sqlite3.connect(store.path) as db:
                    db.execute(
                        "CREATE TRIGGER fail_cursor BEFORE INSERT ON sources BEGIN SELECT RAISE(ABORT, 'interrupted batch'); END"
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    store.collect(task, path)
                self.assertEqual(store.report(task)["observed_responses"], 3)
                self.assertEqual(store.report(task)["last_scan"], before["last_scan"])
                with sqlite3.connect(store.path) as db:
                    db.execute("DROP TRIGGER fail_cursor")
                store.collect(task, path)
                store.collect(task, path, from_start=True)
                self.assertEqual(store.report(task)["observed_responses"], 4)

    def test_independent_collectors_do_not_double_count_responses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root: Path = Path(temporary)
            path: Path = root / "native.jsonl"
            path.write_bytes(
                header() + b"".join(response(f"r-{i}", counters()) for i in range(10))
            )
            script: str = (
                "import sys; from pathlib import Path; from hive.usage_store import UsageStore; "
                "from hive.identity import CodexTaskId; "
                "UsageStore(Path(sys.argv[1])).collect(CodexTaskId('native-task'),Path(sys.argv[2]))"
            )

            def collect(_: int) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        script,
                        str(root / "observations.sqlite3"),
                        str(path),
                    ],
                    env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(collect, range(4)))
            for result in results:
                self.assertEqual(result.returncode, 0, result.stderr)
            store = UsageStore(root / "observations.sqlite3")
            self.assertEqual(store.report(TASK)["observed_responses"], 10)


class SchemaMigrationTests(unittest.TestCase):
    def legacy_store(self, root: Path) -> UsageStore:
        store = UsageStore(root / "usage.sqlite3")
        with sqlite3.connect(store.path) as db:
            db.executescript((ROOT / "tests/fixtures/telemetry-v0.sql").read_text())
            db.execute(
                "INSERT INTO responses VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "old",
                    TASK,
                    "turn-1",
                    "2026-09-23T00:00:00Z",
                    json.dumps(counters()),
                    100,
                    0,
                    0,
                    0,
                    0,
                ),
            )
            db.execute(
                "INSERT INTO turn_models VALUES (?,?,?,?,?)",
                (TASK, "turn-1", "gpt-6-astra", "2026-09-23T00:00:00Z", 0),
            )
            db.execute(
                "INSERT INTO collection_tasks VALUES (?,NULL,NULL,?)",
                (TASK, "native.jsonl"),
            )
        return store

    def test_collector_migrates_old_usage_without_repricing_or_recounting(self) -> None:
        from hive.cost_report import report
        from hive.errors import ErrorCode, HiveError
        from hive.identity import ModelId, PricingTier
        from hive.pricing import quote
        from hive.usage import Tokens

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self.legacy_store(root)
            # Historical rate evidence differs from the current rate card.
            evidence = quote(
                ModelId("gpt-6-astra"), PricingTier.STANDARD, Tokens(100, 0, 0, 0, 0)
            )
            self.assertIsNotNone(evidence)
            if evidence is None:
                raise AssertionError("Missing historical quote")
            value = {**evidence.value(), "usd": "0.123456789012"}
            with sqlite3.connect(store.path) as db:
                db.execute(
                    "INSERT INTO response_estimates VALUES (?,?,?)",
                    ("old", "standard", json.dumps(value)),
                )
            before = store.path.read_bytes()
            with self.assertRaises(HiveError) as caught:
                report(store, TASK, PricingTier.STANDARD)
            self.assertEqual(caught.exception.code, ErrorCode.INVALID_RECORD)
            self.assertIn("not yet migrated", str(caught.exception))
            self.assertEqual(store.path.read_bytes(), before)
            path = root / "native.jsonl"
            path.write_bytes(header() + response("old", counters()))
            store.collect(TASK, path, from_start=True)
            result = report(store, TASK, PricingTier.STANDARD)
            self.assertEqual(result["observed_responses"], 1)
            self.assertEqual(result["parse_gaps"], 0)
            self.assertEqual(result["observed_estimate_usd"], "0.123456789012")
            with sqlite3.connect(store.path) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (17,))
                self.assertEqual(
                    db.execute("SELECT host,cache_write_1h FROM responses").fetchone(),
                    ("codex", 0),
                )
                self.assertEqual(
                    db.execute(
                        "SELECT validated_source FROM collection_tasks"
                    ).fetchone(),
                    (None,),
                )
                usage = json.loads(
                    db.execute("SELECT usage FROM responses").fetchone()[0]
                )
                self.assertEqual(usage["cache_write_1h_input_tokens"], 0)

    def test_migration_failure_rolls_back_schema_and_data(self) -> None:
        from hive.errors import HiveError

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self.legacy_store(root)
            with sqlite3.connect(store.path) as db:
                db.execute("UPDATE responses SET usage='{}'")
            path = root / "native.jsonl"
            path.write_bytes(header())
            with self.assertRaises(HiveError):
                store.collect(TASK, path)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (0,))
                self.assertEqual(
                    db.execute("SELECT usage FROM responses").fetchone(), ("{}",)
                )
                self.assertEqual(
                    [r[1] for r in db.execute("PRAGMA table_info(sources)")],
                    [
                        "task",
                        "path",
                        "device",
                        "inode",
                        "position",
                        "skipping",
                        "scanned",
                        "remaining",
                        "incomplete",
                        "error",
                    ],
                )
                self.assertEqual(
                    db.execute(
                        "SELECT name FROM sqlite_master WHERE name LIKE 'legacy_%'"
                    ).fetchall(),
                    [],
                )

    def test_newer_schema_refuses_reads_and_writes_without_changes(self) -> None:
        from hive.errors import ErrorCode, HiveError

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self.legacy_store(root)
            with sqlite3.connect(store.path) as db:
                db.execute("PRAGMA user_version=999")
            before = store.path.read_bytes()
            for write in (False, True):
                with self.assertRaises(HiveError) as caught:
                    with store.connect(write=write):
                        self.fail("Newer schema was accepted")
                self.assertEqual(caught.exception.code, ErrorCode.INVALID_RECORD)
                self.assertIn("newer schema", str(caught.exception))
            self.assertEqual(before, store.path.read_bytes())
