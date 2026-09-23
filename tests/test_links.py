"""Native bead links include historical assignees and do not multiply cost."""

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from server_fixture import private_server
from test_usage import counters, line

from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.collection import sweep
from hive.collection_registry import CollectionRegistry
from hive.identity import CodexTaskId, SourceCommit
from hive.jsonvalue import record, sequence
from hive.launch_context import LaunchContext
from hive.thread_links import decode, read
from hive.usage_store import UsageStore

CREATOR = "019a6191-60a3-7f2d-b05a-6ef901203040"
EXECUTOR = "019a6191-60a4-7f2d-b05a-6ef901203040"


def bd(connection: BeadsConnection, *arguments: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            "bd",
            "-C",
            str(connection.directory),
            "--sandbox",
            "--dolt-auto-commit",
            "off",
            "--json",
            *arguments,
        ],
        cwd=connection.directory,
        env=connection.environment(),
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode:
        raise AssertionError(completed.stdout + completed.stderr)
    value: object = json.loads(completed.stdout)
    if isinstance(value, list):
        return record(sequence(value, "native reply")[0])
    return record(value)


class BeadLinkTests(unittest.TestCase):
    def test_closed_beads_and_multiple_roles_deduplicate_thread_total(self) -> None:
        with private_server() as (connection, _):
            first = bd(
                connection,
                "create",
                "First",
                "--metadata",
                json.dumps({"hive_project": "sample", "hive_origin_thread": CREATOR}),
            )
            second = bd(
                connection,
                "create",
                "Second",
                "--metadata",
                json.dumps({"hive_project": "sample", "hive_origin_thread": CREATOR}),
            )
            first_id = str(first["id"])
            second_id = str(second["id"])
            bd(connection, "--actor", EXECUTOR, "update", first_id, "--claim")
            bd(
                connection,
                "update",
                first_id,
                "--set-metadata",
                "hive_resolution=completed",
            )
            bd(connection, "close", first_id)
            bd(connection, "--actor", EXECUTOR, "update", second_id, "--claim")
            links, gaps = read(BeadsProcess(connection))
            self.assertEqual({link.task for link in links}, {CREATOR, EXECUTOR})
            self.assertEqual(len(links), 4)
            self.assertEqual(gaps, ())
            with tempfile.TemporaryDirectory() as temporary:
                registry = CollectionRegistry(
                    UsageStore(Path(temporary) / "telemetry.sqlite3")
                )
                registry.refresh(links, gaps, None)
                self.assertEqual(registry.status()["linked_threads"], 2)
                self.assertEqual(len(registry.associations(CodexTaskId(EXECUTOR))), 2)
                registry.refresh(None, None, "Beads offline")
                self.assertEqual(len(registry.associations(CodexTaskId(CREATOR))), 2)
                self.assertIsNotNone(registry.status()["registry_error"])

    def test_sweep_uses_links_and_survives_beads_outage(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            bd(
                connection,
                "create",
                "Observed",
                "--metadata",
                json.dumps({"hive_project": "sample", "hive_origin_thread": CREATOR}),
            )
            root = Path(temporary)
            transcript = root / "transcript.jsonl"
            transcript.write_bytes(
                line("session_meta", {"id": CREATOR})
                + line(
                    "token_usage_record",
                    {
                        "thread_id": CREATOR,
                        "session_id": CREATOR,
                        "turn_id": "turn-1",
                        "response_id": "response-1",
                        "usage": counters(),
                    },
                )
            )
            index = root / "native.sqlite3"
            with sqlite3.connect(index) as database:
                database.execute(
                    "CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)"
                )
                database.execute(
                    "INSERT INTO threads VALUES (?,?)", (CREATOR, str(transcript))
                )
            context = LaunchContext(
                SourceCommit("source"),
                root,
                root / "state",
                connection.directory,
                0,
                root,
            )
            first = sweep(context, index, 32)
            self.assertEqual(first["attempted"], 1)
            usage = UsageStore(context.state / "telemetry.sqlite3")
            self.assertEqual(
                usage.report(CodexTaskId(CREATOR))["observed_responses"], 1
            )
            metadata = connection.directory / ".beads/metadata.json"
            original = metadata.read_text()
            metadata.write_text("{}")
            try:
                cached = sweep(context, index, 32)
                self.assertIsNotNone(cached["registry_error"])
                self.assertEqual(
                    usage.report(CodexTaskId(CREATOR))["observed_responses"], 1
                )
            finally:
                metadata.write_text(original)

    def test_invalid_metadata_is_a_gap_beside_valid_links(self) -> None:
        links, gaps = decode(
            [
                {"id": "hv-invalid", "metadata": "broken", "assignee": "legacy"},
                {
                    "id": "hv-valid",
                    "metadata": {"hive_origin_thread": CREATOR},
                    "assignee": None,
                },
            ]
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].bead, "hv-valid")
        self.assertIn("hv-invalid: invalid metadata", gaps)

    def test_missing_and_legacy_references_are_visible_gaps(self) -> None:
        with private_server() as (connection, _):
            bead = bd(connection, "create", "Legacy")
            bd(connection, "update", str(bead["id"]), "--assignee", "shared-executor")
            links, gaps = read(BeadsProcess(connection))
            self.assertFalse(links)
            self.assertEqual(len(gaps), 2)
