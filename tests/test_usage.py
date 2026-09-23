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

from cli_fixture import ROOT, cli_fixture
from server_fixture import private_server

from hive.identity import CodexTaskId
from hive.usage_store import UsageStore

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

    def test_fresh_cli_resumes_after_partial_writes_moves_and_provider_outage(
        self,
    ) -> None:
        with private_server() as (connection, _), cli_fixture(connection) as cli:
            path = connection.directory / "transcript.jsonl"
            original = response("response-1", counters())
            path.write_bytes(header() + original[:-8])
            # Collection and reporting remain usable even when Beads is down.
            metadata = connection.directory / ".beads/metadata.json"
            saved = metadata.read_text()
            metadata.write_text("{}")
            args = ("telemetry", "usage", "--task", TASK)
            self.assertIsNone(cli.call(*args)["known_tokens"])
            first = cli.call(
                "telemetry", "collect", "--task", TASK, "--transcript", str(path)
            )
            self.assertTrue(first["incomplete_tail"])
            self.assertIsNone(cli.call(*args)["known_tokens"])
            with path.open("ab") as stream:
                stream.write(original[-8:] + original + response("missing"))
                stream.write(
                    line(
                        "event_msg",
                        {
                            "type": "token_count",
                            "info": {"total_token_usage": counters(9000)},
                        },
                    )
                )
            cli.call("telemetry", "collect", "--task", TASK, "--transcript", str(path))
            report = cli.call(*args)
            self.assertEqual(report["observed_responses"], 2)
            self.assertEqual(report["responses_missing_usage"], 1)
            self.assertEqual(
                report["known_tokens"],
                {k: v for k, v in counters().items() if k != "total_tokens"},
            )
            self.assertIsNone(report["api_equivalent_usd"])
            archive = path.with_name("archived.jsonl")
            path.rename(archive)
            missing = cli.call(
                "telemetry", "collect", "--task", TASK, "--transcript", str(path)
            )
            self.assertIsInstance(missing["error"], str)
            self.assertIsNone(missing["remaining_bytes"])
            self.assertEqual(cli.call(*args)["observed_responses"], 2)
            with archive.open("ab") as stream:
                stream.write(response("missing", counters(0)) + b"{broken json}\n")
            moved = cli.call(
                "telemetry", "collect", "--task", TASK, "--transcript", str(archive)
            )
            self.assertIsNone(moved["error"])
            report = cli.call(*args)
            self.assertEqual(report["responses_missing_usage"], 0)
            self.assertEqual(report["parse_gaps"], 1)
            self.assertEqual(report["remaining_bytes"], 0)
            self.assertIsNone(report["source_error"])
            again = cli.call(
                "telemetry", "collect", "--task", TASK, "--transcript", str(archive)
            )
            self.assertEqual(again["read_bytes"], 0)
            # Observer corruption cannot block ordinary task filing or reads.
            metadata.write_text(saved)
            (cli.state / "telemetry.sqlite3").write_bytes(b"corrupt derived data")
            cli.call(*args, expected="ProviderUnavailable")
            bead = cli.add("Independent task")
            self.assertEqual(cli.call("task", "show", bead)["code"], "Task")

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
