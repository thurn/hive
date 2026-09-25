"""Real native events keep historical owners and expose incomplete evidence."""

import json
import subprocess
import tempfile
import time
import unittest
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from server_fixture import private_server
from test_contention import hive
from test_links import CREATOR, EXECUTOR, bd

from hive.bead_events import read
from hive.bead_history import refresh
from hive.bead_queries import FIRST, bead, page
from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.collection import sweep
from hive.collection_registry import CollectionRegistry
from hive.errors import ErrorCode, HiveError
from hive.identity import SourceCommit, ThreadId
from hive.launch_context import LaunchContext
from hive.usage_store import UsageStore

OTHER = "a521cf51-c055-4715-832b-fc19921ee482"


def sql(connection: BeadsConnection, query: str) -> object:
    result = subprocess.run(
        [
            "bd",
            "-C",
            str(connection.directory),
            "--sandbox",
            "--dolt-auto-commit",
            "off",
            "--json",
            "sql",
            query,
        ],
        cwd=connection.directory,
        env=connection.environment(),
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def uuid7(milliseconds: int, number: int = 0) -> str:
    return str(UUID(int=(milliseconds << 80) | (7 << 76) | (number << 64) | (2 << 62)))


class BeadHistoryTests(unittest.TestCase):
    def test_handoff_keeps_both_hosts_and_outage_preserves_registry(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            root = Path(temporary)
            first = bd(
                connection,
                "create",
                "Handoff",
                "--metadata",
                json.dumps({"hive_origin_thread": CREATOR}),
            )
            identity = str(first["id"])
            bd(connection, "--actor", EXECUTOR, "update", identity, "--claim")
            bd(connection, "update", identity, "--assignee", OTHER)
            context = LaunchContext(
                SourceCommit("fixture"),
                root,
                root / "state",
                connection.directory,
                -1,
                root,
                root / "claude",
            )
            result = sweep(context, root / "missing-index", 8)
            self.assertTrue(result["bead_events_caught_up"], result)
            registry = CollectionRegistry(UsageStore(root / "state/telemetry.sqlite3"))
            self.assertEqual(set(registry.next(8)), {CREATOR, EXECUTOR, OTHER})
            self.assertEqual(registry.status()["interval_unknown_beads"], 0)
            events = read(BeadsProcess(connection).bead_events(identity))
            self.assertTrue(all(event.error is None for event in events))
            self.assertEqual(events[1].old_assignee, "")
            # Schema drift disables attribution but never loses known owners.
            sql(
                connection,
                "ALTER TABLE events RENAME COLUMN old_value TO old_value_changed",
            )
            result = sweep(context, root / "missing-index", 8)
            self.assertFalse(result["bead_events_caught_up"])
            self.assertTrue(result["event_retention_skipped"])
            self.assertIn("old_value", str(result["bead_events_error"]))
            self.assertEqual(set(registry.next(8)), {CREATOR, EXECUTOR, OTHER})
            (root / "missing-beads").symlink_to(
                connection.directory, target_is_directory=True
            )
            reported = hive(
                root,
                "cost",
                "--task",
                EXECUTOR,
                "--native-index",
                str(root / "missing-index"),
            )
            self.assertEqual(reported.returncode, 0, reported.stderr)
            self.assertTrue(json.loads(reported.stdout)["association_stale"])
            self.assertEqual(set(registry.next(8)), {CREATOR, EXECUTOR, OTHER})
            sql(
                connection,
                "ALTER TABLE events RENAME COLUMN old_value_changed TO old_value",
            )
            recovered = sweep(context, root / "missing-index", 8)
            self.assertTrue(recovered["bead_events_caught_up"])
            self.assertEqual(registry.status()["interval_unknown_beads"], 0)

    def test_multi_page_cold_start_late_visibility_and_invalid_id(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            root = Path(temporary)
            identity = str(bd(connection, "create", "Paging")["id"])
            # More than two full pages; native created_at uses server-local time.
            milliseconds = int(time.time() * 1000)
            identities = [uuid7(milliseconds, index) for index in range(1001)]
            for start in range(0, len(identities), 200):
                values = ",".join(
                    f"('{item}','{identity}','updated','fixture','{{}}','{{}}',CURRENT_TIMESTAMP)"
                    for item in identities[start : start + 200]
                )
                sql(
                    connection,
                    "INSERT INTO events(id,issue_id,event_type,actor,old_value,new_value,created_at) VALUES "
                    + values,
                )
            store = UsageStore(root / "state/telemetry.sqlite3")
            process = BeadsProcess(connection)
            for _ in range(4):
                links, gaps = refresh(store, process, time.monotonic() + 2)
                registry = CollectionRegistry(store)
                registry.refresh(links, gaps, None)
                if registry.status()["bead_events_caught_up"]:
                    break
            self.assertTrue(registry.status()["bead_events_caught_up"])
            with store.connect(write=False) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM bead_events").fetchone(), (1002,)
                )
            late = uuid7(milliseconds - 5 * 60 * 1000)
            sql(
                connection,
                f"INSERT INTO events(id,issue_id,event_type,actor,old_value,new_value,created_at) VALUES ('{late}','{identity}','updated','fixture','{{}}','{{\"assignee\":\"{EXECUTOR}\"}}',DATE_SUB(CURRENT_TIMESTAMP, INTERVAL 5 MINUTE))",
            )
            invalid = "ffffffff-ffff-4fff-8fff-ffffffffffff"
            sql(
                connection,
                f"INSERT INTO events(id,issue_id,event_type,actor,old_value,new_value,created_at) VALUES ('{invalid}','{identity}','updated','fixture','{{}}','{{\"assignee\":\"{OTHER}\"}}',CURRENT_TIMESTAMP)",
            )
            with patch.object(
                BeadsProcess,
                "event_page",
                side_effect=HiveError(
                    ErrorCode.PROVIDER_UNAVAILABLE, "Transient native read failure"
                ),
            ):
                links, gaps = refresh(store, process, time.monotonic() + 2)
            registry.refresh(links, gaps, None)
            self.assertFalse(registry.status()["bead_events_caught_up"])
            links, gaps = refresh(store, process, time.monotonic() + 2)
            registry.refresh(links, gaps, None)
            self.assertTrue(registry.status()["bead_events_caught_up"])
            self.assertIn(ThreadId(EXECUTOR), registry.next(8))
            self.assertIn(ThreadId(OTHER), registry.next(8))
            self.assertEqual(registry.status()["interval_unknown_beads"], 1)
            with store.connect(write=False) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM bead_events").fetchone(), (1004,)
                )

    def test_uuid_time_handles_fall_back_and_explicit_null_assignee(self) -> None:
        with (
            private_server(timezone="America/Los_Angeles") as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            identity = str(bd(connection, "create", "Clock")["id"])
            ids = [
                uuid7(int(datetime.fromisoformat(at).timestamp() * 1000))
                for at in ("2026-11-01T08:30:00+00:00", "2026-11-01T09:30:00+00:00")
            ]
            for event_id in ids:
                sql(
                    connection,
                    f"INSERT INTO events(id,issue_id,event_type,actor,old_value,new_value,created_at) VALUES ('{event_id}','{identity}','updated','fixture','{{\"assignee\":null}}','{{\"assignee\":null}}','2026-11-01 01:30:00')",
                )
            values = read(BeadsProcess(connection).bead_events(identity))
            repeated_hour = [event for event in values if event.identity in ids]
            self.assertEqual(len(repeated_hour), 2)
            self.assertTrue(all(event.error is None for event in repeated_hour))
            self.assertTrue(
                all(
                    event.old_assignee == "" and event.new_assignee == ""
                    for event in repeated_hour
                )
            )
            self.assertNotEqual(repeated_hour[0].occurred, repeated_hour[1].occurred)
            bad = uuid7(int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000))
            sql(
                connection,
                f"INSERT INTO events(id,issue_id,event_type,actor,old_value,new_value,created_at) VALUES ('{bad}','{identity}','updated','fixture','{{}}','{{}}',CURRENT_TIMESTAMP)",
            )
            store = UsageStore(Path(temporary) / "state/telemetry.sqlite3")
            links, gaps = refresh(store, BeadsProcess(connection), time.monotonic() + 2)
            registry = CollectionRegistry(store)
            registry.refresh(links, gaps, None)
            self.assertEqual(registry.status()["interval_unknown_beads"], 1)

    def test_query_templates_are_exact_and_reject_injection(self) -> None:
        expected = (
            (Path(__file__).parent / "fixtures/bead-event-page.sql")
            .read_text()
            .rstrip()
        )
        self.assertEqual(page(FIRST), expected)
        self.assertEqual(
            bead("hv-a.1"),
            expected.replace(
                "WHERE id > '00000000-0000-7000-8000-000000000000' ORDER BY id LIMIT 500",
                "WHERE issue_id = 'hv-a.1' ORDER BY id",
            ),
        )
        for value in ("' OR 1=1 --", "hv-a; DELETE FROM events", "", "hv a"):
            with self.assertRaises(HiveError):
                bead(value)
        for value in (
            "' OR 1=1 --",
            "A0000000-0000-7000-8000-000000000000",
            "00000000-0000-4000-8000-000000000000",
        ):
            with self.assertRaises(HiveError):
                page(value)

    def test_legacy_rebuild_progress_survives_failure_and_later_sweeps(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            identities = sorted(
                str(bd(connection, "create", f"Legacy {number}")["id"])
                for number in range(3)
            )
            sql(connection, "DELETE FROM events")
            store = UsageStore(Path(temporary) / "state/telemetry.sqlite3")
            process = BeadsProcess(connection)
            native: Callable[[BeadsProcess, str], object] = BeadsProcess.bead_events
            calls: list[str] = []

            def interrupted(process: BeadsProcess, identity: str) -> object:
                calls.append(identity)
                if len(calls) == 2:
                    raise HiveError(
                        ErrorCode.PROVIDER_UNAVAILABLE, "Interrupted legacy rebuild"
                    )
                return native(process, identity)

            with patch.object(
                BeadsProcess, "bead_events", autospec=True, side_effect=interrupted
            ):
                refresh(store, process, time.monotonic() + 2)
                refresh(store, process, time.monotonic() + 2)
                refresh(store, process, time.monotonic() + 2)
            self.assertEqual(
                calls[:4], [identities[0], identities[1], identities[1], identities[2]]
            )
            status = CollectionRegistry(store).status()
            self.assertTrue(status["bead_events_caught_up"])
            self.assertEqual(status["interval_unknown_beads"], 3)
