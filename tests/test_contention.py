"""Telemetry database contention is a structured Busy outcome, never a crash."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from test_cost import context, observed
from test_usage import ROOT, TASK, counters, header

from hive.collection import sweep
from hive.errors import ErrorCode, HiveError
from hive.identity import SourceCommit
from hive.jsonvalue import parse, record
from hive.launch_context import LaunchContext
from hive.usage_store import UsageStore


@contextmanager
def held(path: Path, mode: str) -> Iterator[None]:
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute(f"BEGIN {mode}")
        yield
    finally:
        connection.rollback()
        connection.close()


def hive(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    guard = os.open(root, os.O_RDONLY)
    try:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from hive.cli import main; sys.exit(main())",
                *arguments,
                "--json",
            ],
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "HIVE_MUTATION_GUARD_FD": str(guard),
                "HIVE_SELECTED_COMMIT": "source",
                "HIVE_SELECTED_DIRECTORY": str(ROOT),
                "HIVE_STATE_DIRECTORY": str(root / "state"),
                "HIVE_BEADS_DIRECTORY": str(root / "missing-beads"),
                "HIVE_REPOSITORY_DIRECTORY": str(ROOT),
            },
            pass_fds=(guard,),
            capture_output=True,
            text=True,
            timeout=20,
        )
    finally:
        os.close(guard)


class ContentionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.transcript = self.root / "native.jsonl"
        self.transcript.write_bytes(
            header() + context() + observed("r-1", "turn-1", counters())
        )
        self.store = UsageStore(self.root / "state/telemetry.sqlite3")
        self.store.collect(TASK, self.transcript)
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "HIVE_PROJECTS": json.dumps(
                        [dict(id="sample", repository=str(ROOT))]
                    )
                },
            )
        )
        self.commands = {
            "outcomes": (
                "telemetry",
                "outcomes",
                "--project",
                "sample",
                "--start",
                "2026-09-25T00:00:00Z",
                "--end",
                "2026-09-26T00:00:00Z",
            ),
            "status": ("telemetry", "status"),
            "usage": ("telemetry", "usage", "--task", TASK),
            "cost": ("cost", "--task", TASK),
            "collect": (
                "telemetry",
                "collect",
                "--task",
                TASK,
                "--transcript",
                str(self.transcript),
            ),
            "sweep": (
                "telemetry",
                "sweep",
                "--native-index",
                str(self.root / "native.sqlite3"),
            ),
        }

    def assertBusy(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual(record(parse(completed.stderr))["code"], "Busy")

    def estimates(self) -> int:
        with sqlite3.connect(self.store.path) as connection:
            value: object = connection.execute(
                "SELECT COUNT(*) FROM response_estimates"
            ).fetchone()
        if not isinstance(value, tuple) or not isinstance(value[0], int):
            raise AssertionError("Missing estimate count")
        return value[0]

    def test_reports_read_beside_a_writer_and_defer_estimate_retention(
        self,
    ) -> None:
        hive(self.root, *self.commands["status"])
        with held(self.store.path, "IMMEDIATE"):
            for name in ("status", "usage", "cost", "outcomes"):
                with self.subTest(name):
                    completed = hive(self.root, *self.commands[name])
                    self.assertEqual(completed.returncode, 0, completed.stderr)
            cost = record(parse(hive(self.root, *self.commands["cost"]).stdout))
            self.assertEqual(cost["priced_responses"], 1)
            self.assertEqual(cost["unretained_estimates"], 1)
            self.assertTrue(cost["association_stale"])
            for name in ("collect", "sweep"):
                with self.subTest(name):
                    self.assertBusy(hive(self.root, *self.commands[name]))
        self.assertEqual(self.estimates(), 0)
        completed = hive(self.root, *self.commands["cost"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.estimates(), 1)
        self.assertEqual(record(parse(completed.stdout))["unretained_estimates"], 0)

    def test_exclusive_contention_is_busy_for_every_telemetry_command(self) -> None:
        hive(self.root, *self.commands["status"])
        with held(self.store.path, "EXCLUSIVE"):
            for name, arguments in self.commands.items():
                with self.subTest(name):
                    self.assertBusy(hive(self.root, *arguments))

    def test_sweep_contention_is_a_structured_busy_error(self) -> None:
        launch = LaunchContext(
            SourceCommit("source"),
            ROOT,
            self.root / "state",
            self.root / "missing-beads",
            0,
            ROOT,
        )
        with held(self.store.path, "IMMEDIATE"):
            with self.assertRaises(HiveError) as caught:
                sweep(launch, self.root / "native.sqlite3", 4)
        self.assertEqual(caught.exception.code, ErrorCode.BUSY)


if __name__ == "__main__":
    unittest.main()
