"""Native claims, handoffs and repairs drive public cost reconciliation."""

import json
import sqlite3
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from server_fixture import private_server
from test_bead_history import sql, uuid7
from test_claude import OTHER, THREAD, assistant
from test_contention import held, hive
from test_events import captured, change, logs, replace_logs, spool
from test_links import CREATOR, EXECUTOR, bd
from test_usage import counters, header, line

from hive.bead_events import read
from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.collection import sweep
from hive.identity import SourceCommit, ThreadId
from hive.jsonvalue import record, sequence, string
from hive.launch_context import LaunchContext


def command(root: Path, *args: str) -> dict[str, object]:
    result = hive(root, *args)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return record(json.loads(result.stdout))


def native(connection: BeadsConnection, *args: str) -> None:
    result = subprocess.run(
        [
            "bd",
            "-C",
            str(connection.directory),
            "--sandbox",
            "--dolt-auto-commit",
            "off",
            "--json",
            *args,
        ],
        cwd=connection.directory,
        env=connection.environment(),
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)


def last(connection: BeadsConnection, bead: str) -> datetime:
    at = read(BeadsProcess(connection).bead_events(bead))[-1].occurred
    if at is None:
        raise AssertionError("Native event has no UTC time")
    return at


def claude(
    root: Path, task: ThreadId, identity: str, at: datetime, output: int = 10
) -> None:
    value = record(json.loads(assistant(identity, output, thread=task)))
    value["timestamp"] = at.isoformat()
    path = root / f"{task}.jsonl"
    with path.open("a") as stream:
        stream.write(json.dumps(value) + "\n")
    command(root, "telemetry", "collect", "--task", task, "--transcript", str(path))


def setup(root: Path, connection: BeadsConnection) -> None:
    (root / "missing-beads").symlink_to(connection.directory, target_is_directory=True)


def total(value: object) -> Decimal:
    return sum(
        (Decimal(str(record(item)["usd"])) for item in sequence(value, "breakdown")),
        Decimal(0),
    )


class BeadCostTests(unittest.TestCase):
    def test_mixed_host_handoff_reopen_and_unowned_time_reconcile(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            root = Path(temporary)
            setup(root, connection)
            bead = str(
                bd(
                    connection,
                    "create",
                    "Mixed handoff",
                    "--metadata",
                    json.dumps({"hive_origin_thread": CREATOR}),
                )["id"]
            )
            bd(connection, "--actor", EXECUTOR, "update", bead, "--claim")
            first = last(connection, bead) + timedelta(milliseconds=1)
            bd(connection, "update", bead, "--assignee", THREAD)
            second = last(connection, bead) + timedelta(milliseconds=1)
            bd(connection, "close", bead)
            bd(connection, "reopen", bead)
            unowned = last(connection, bead) + timedelta(milliseconds=1)
            bd(connection, "update", bead, "--status", "in_progress")
            third = last(connection, bead) + timedelta(milliseconds=1)
            bd(connection, "close", bead)
            codex = root / "codex.jsonl"
            response = record(
                json.loads(
                    line(
                        "token_usage_record",
                        {
                            "thread_id": EXECUTOR,
                            "session_id": EXECUTOR,
                            "turn_id": "turn",
                            "response_id": "codex",
                            "usage": counters(100),
                        },
                    )
                )
            )
            response["timestamp"] = first.isoformat()
            codex.write_bytes(
                header(EXECUTOR)
                + line("turn_context", {"turn_id": "turn", "model": "gpt-6-astra"})
                + (json.dumps(response) + "\n").encode()
            )
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                EXECUTOR,
                "--transcript",
                str(codex),
            )
            for identity, at in (
                ("claude-owned", second),
                ("claude-open", unowned),
                ("claude-resumed", third),
            ):
                claude(root, THREAD, identity, at)
            report = command(root, "cost", "--bead", bead)
            self.assertEqual(report["interval_status"], "known", report)
            self.assertEqual(len(sequence(report["intervals"], "intervals")), 3)
            self.assertEqual(report["creator_threads"], [CREATOR])
            self.assertEqual(report["near_boundary_requests"], 3)
            for dimension in (
                "host",
                "model",
                "hour",
                "agent",
                "skill",
                "query_source",
                "tool",
            ):
                self.assertEqual(
                    total(report["by_" + dimension]),
                    Decimal(str(report["attributed_usd"])),
                )
            balance = command(root, "cost", "--reconcile")
            self.assertTrue(balance["balanced"])
            self.assertEqual(balance["unowned_usd"], "0.000438000000")
            self.assertEqual(balance["unattributable_usd"], "0.000000000000")
            self.assertEqual(balance["attributed_usd"], report["attributed_usd"])
            fast = command(root, "cost", "--bead", bead, "--tier", "fast")
            self.assertGreater(
                Decimal(str(fast["attributed_usd"])),
                Decimal(str(report["attributed_usd"])),
            )

    def test_shared_picodollar_remainders_event_time_and_contended_retention(
        self,
    ) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            root = Path(temporary)
            setup(root, connection)
            beads = []
            for number in range(3):
                bead = str(bd(connection, "create", f"Overlap {number}")["id"])
                bd(connection, "--actor", THREAD, "update", bead, "--claim")
                beads.append(bead)
            at = last(connection, beads[-1]) + timedelta(milliseconds=1)
            claude(root, THREAD, "shared", at, 11)
            export = captured()
            spool(
                root,
                replace_logs(
                    export,
                    [
                        change(
                            value,
                            **{
                                "event.timestamp": (
                                    at + timedelta(milliseconds=1)
                                ).isoformat()
                            },
                        )
                        for value in logs(export)
                    ],
                ),
            )
            command(
                root,
                "telemetry",
                "sweep",
                "--native-index",
                str(root / "missing-index"),
            )
            expected, remainder = divmod(458_000_000 + 20_000_000_000, 3)
            for index, bead in enumerate(sorted(beads)):
                report = command(root, "cost", "--bead", bead)
                picos = int(Decimal(str(report["attributed_usd"])) * 10**12)
                # Remainders are distributed separately for each request.
                self.assertEqual(
                    picos,
                    expected + (2 if index < 2 else 0) - (1 if remainder == 1 else 0),
                )
                self.assertEqual(report["shared_requests"], 2)
                self.assertEqual(
                    total(report["by_query_source"]),
                    Decimal(str(report["attributed_usd"])),
                )
            balance = command(root, "cost", "--reconcile")
            self.assertTrue(balance["balanced"])
            self.assertEqual(balance["unowned_usd"], "0.000000000000")
            self.assertEqual(balance["thread_total_usd"], "0.020458000000")
            claude(root, THREAD, "unretained", at + timedelta(milliseconds=2))
            with held(root / "state/telemetry.sqlite3", "IMMEDIATE"):
                deferred = command(root, "cost", "--reconcile")
            self.assertIsNone(deferred["balanced"])
            self.assertGreater(int(str(deferred["unretained_estimates"])), 0)

    def test_event_timestamp_controls_detail_handoff_hour_and_upgrade(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            root = Path(temporary)
            setup(root, connection)
            first = str(bd(connection, "create", "Before hour")["id"])
            bd(connection, "--actor", THREAD, "update", first, "--claim")
            bd(connection, "close", first)
            boundary = last(connection, first).replace(
                minute=0, second=0, microsecond=0
            )
            close_id = read(BeadsProcess(connection).bead_events(first))[-1].identity
            second = str(bd(connection, "create", "After hour")["id"])
            bd(connection, "--actor", THREAD, "update", second, "--claim")
            claim_id = read(BeadsProcess(connection).bead_events(second))[-1].identity
            for bead_number, bead in enumerate((first, second)):
                for index, event in enumerate(
                    read(BeadsProcess(connection).bead_events(bead))
                ):
                    event_time = (
                        boundary
                        if event.identity in (close_id, claim_id)
                        else boundary - timedelta(seconds=4 - index)
                    )
                    sql(
                        connection,
                        "UPDATE events SET id='"
                        + uuid7(
                            int(event_time.timestamp() * 1000), bead_number * 10 + index
                        )
                        + "',created_at='"
                        + event_time.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                        + "' WHERE id='"
                        + event.identity
                        + "'",
                    )
            at = boundary + timedelta(milliseconds=1)
            export = captured()
            spool(
                root,
                replace_logs(
                    export,
                    [
                        change(
                            value,
                            **{"event.timestamp": at.isoformat(), "duration_ms": 1591},
                        )
                        for value in logs(export)
                    ],
                ),
            )
            command(root, "telemetry", "sweep", "--native-index", str(root / "missing"))
            page = command(root, "cost", "--task", THREAD, "--requests")
            detail = record(sequence(page["requests"], "requests")[0])
            self.assertEqual(datetime.fromisoformat(str(detail["observed_at"])), at)
            self.assertEqual(
                command(root, "cost", "--bead", first)["attributed_usd"],
                "0.000000000000",
            )
            charged = command(root, "cost", "--bead", second)
            self.assertEqual(charged["attributed_usd"], detail["usd"], charged)
            self.assertEqual(charged["near_boundary_requests"], 1)
            self.assertIn(boundary.strftime("%Y-%m-%dT%H"), str(charged["by_hour"]))
            balance = command(root, "cost", "--reconcile")
            self.assertTrue(balance["balanced"])
            database = root / "state/telemetry.sqlite3"
            with sqlite3.connect(database) as db:
                prices = db.execute(
                    "SELECT * FROM response_estimates ORDER BY response,tier"
                ).fetchall()
                db.execute(
                    "UPDATE claude_request_events SET observed=?",
                    ((at - timedelta(milliseconds=1591)).isoformat(),),
                )
                db.execute("PRAGMA user_version=9")
            # The collector migrates stored timestamps without rereading the export.
            for path in (root / "state/otlp-spool").glob("*.json"):
                path.unlink()
            command(root, "telemetry", "sweep", "--native-index", str(root / "missing"))
            migrated = command(root, "cost", "--task", THREAD, "--requests")
            self.assertEqual(migrated["requests"], page["requests"])
            self.assertEqual(
                command(root, "cost", "--reconcile")["thread_total_usd"],
                balance["thread_total_usd"],
            )
            self.assertEqual(
                command(root, "cost", "--bead", second)["by_hour"], charged["by_hour"]
            )
            with sqlite3.connect(database) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT * FROM response_estimates ORDER BY response,tier"
                    ).fetchall(),
                    prices,
                )

    def test_native_labels_preserve_assigned_creation_and_owned_cost(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            root = Path(temporary)
            setup(root, connection)
            bead = str(bd(connection, "create", "Labelled", "--assignee", THREAD)["id"])

            label_connection: BeadsConnection = connection
            label_bead: str = bead

            def label_event(action: str, name: str) -> None:
                native(label_connection, "label", action, label_bead, name)
                # bd 1.2.2's label command does not write history; replay the
                # historical native label event shape retained in the live store.
                at = datetime.now(UTC)
                identity = uuid7(int(at.timestamp() * 1000))
                kind = "label_added" if action == "add" else "label_removed"
                sql(
                    label_connection,
                    "INSERT INTO events(id,issue_id,event_type,actor,old_value,new_value,created_at) VALUES ('"
                    + identity
                    + "','"
                    + label_bead
                    + "','"
                    + kind
                    + "','fixture',NULL,'"
                    + name
                    + "','"
                    + at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                    + "')",
                )

            label_event("add", "before")
            bd(connection, "update", bead, "--status", "in_progress")
            at = last(connection, bead) + timedelta(milliseconds=1)
            label_event("add", "during")
            label_event("remove", "before")
            bd(connection, "close", bead)
            label_event("remove", "during")
            claude(root, THREAD, "label-owned", at)
            report = command(root, "cost", "--bead", bead)
            self.assertEqual(report["interval_status"], "known", report)
            self.assertEqual(report["attributed_usd"], "0.000438000000")
            self.assertEqual(len(sequence(report["intervals"], "intervals")), 1)
            self.assertTrue(command(root, "cost", "--reconcile")["balanced"])
            label = next(
                event
                for event in read(BeadsProcess(connection).bead_events(bead))
                if event.kind == "label_added"
            )
            # Unknown event kinds and malformed labels must still fail closed.
            for assignment, expected in (
                ("event_type='future_event'", "Unsupported ownership event"),
                (
                    "event_type='label_added',new_value='{\"status\":\"closed\"}'",
                    "Label event unexpectedly changes ownership",
                ),
                (
                    "event_type='label_added',old_value='{\"assignee\":null,\"status\":\"\"}',new_value='during'",
                    "Label event unexpectedly changes ownership",
                ),
                (
                    "old_value=NULL,new_value='during',created_at='2020-01-01 00:00:00'",
                    "Beads event time disagrees",
                ),
            ):
                sql(
                    connection,
                    "UPDATE events SET "
                    + assignment
                    + " WHERE id='"
                    + label.identity
                    + "'",
                )
                for _ in range(4):
                    report = command(root, "cost", "--bead", bead)
                    if report["bead_events_caught_up"]:
                        break
                self.assertEqual(report["interval_status"], "unknown", report)
                self.assertIn(expected, str(report["interval_reason"]))
                self.assertEqual(report["attributed_usd"], "0.000000000000")

    def test_missing_middle_event_is_unknown_until_rebuild_recovers(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            root = Path(temporary)
            setup(root, connection)
            bead = str(bd(connection, "create", "Missing handoff")["id"])
            bd(connection, "--actor", THREAD, "update", bead, "--claim")
            at = last(connection, bead) + timedelta(milliseconds=1)
            bd(connection, "update", bead, "--assignee", OTHER)
            event_id = read(BeadsProcess(connection).bead_events(bead))[-1].identity
            saved = record(
                sequence(
                    sql(
                        connection,
                        f"SELECT id,issue_id,event_type,actor,old_value,new_value,created_at FROM events WHERE id='{event_id}'",
                    ),
                    "event",
                )[0]
            )
            bd(connection, "update", bead, "--assignee", EXECUTOR)
            sql(connection, f"DELETE FROM events WHERE id='{event_id}'")
            claude(root, THREAD, "uncertain", at)
            first = command(root, "cost", "--bead", bead)
            self.assertEqual(first["interval_status"], "unknown")
            second = command(root, "cost", "--bead", bead)
            self.assertEqual(second["interval_status"], "unknown")
            self.assertIn("before-state", str(second["interval_reason"]))
            balance = command(root, "cost", "--reconcile")
            self.assertTrue(balance["balanced"])
            self.assertEqual(balance["unattributable_usd"], "0.000438000000")
            values = [
                string(saved[name], name).replace("'", "''")
                for name in (
                    "id",
                    "issue_id",
                    "event_type",
                    "actor",
                    "old_value",
                    "new_value",
                )
            ]
            created = (
                string(saved["created_at"], "created")
                .removesuffix("Z")
                .replace("T", " ")
            )
            sql(
                connection,
                "INSERT INTO events(id,issue_id,event_type,actor,old_value,new_value,created_at) VALUES ("
                + ",".join("'" + value + "'" for value in [*values, created])
                + ")",
            )
            restored = command(root, "cost", "--bead", bead)
            self.assertEqual(restored["interval_status"], "known", restored)
            self.assertEqual(restored["attributed_usd"], "0.000438000000")
            bd(connection, "update", bead, "--assignee", "")
            cleared_id = read(BeadsProcess(connection).bead_events(bead))[-1].identity
            sql(
                connection,
                f"UPDATE events SET new_value='{{\"assignee\":null}}' WHERE id='{cleared_id}'",
            )
            cleared = command(root, "cost", "--bead", bead)
            self.assertEqual(cleared["interval_status"], "known")
            self.assertEqual(cleared["attributed_usd"], restored["attributed_usd"])

    def test_assigned_creation_rename_prefix_and_delete_preserve_charges(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            root = Path(temporary)
            setup(root, connection)
            bead = str(
                bd(connection, "create", "Assigned creation", "--assignee", THREAD)[
                    "id"
                ]
            )
            bd(connection, "update", bead, "--status", "in_progress")
            at = last(connection, bead) + timedelta(milliseconds=1)
            bd(connection, "close", bead)
            claude(root, THREAD, "retained", at)
            before = command(root, "cost", "--bead", bead)
            self.assertEqual(before["interval_status"], "known", before)
            # Existing old unlinked events must survive an incomplete interval replay.
            export = captured()
            old = (datetime.now(UTC) - timedelta(days=8)).isoformat()
            spool(
                root,
                replace_logs(
                    export,
                    [
                        change(value, **{"session.id": OTHER, "event.timestamp": old})
                        for value in logs(export)
                    ],
                ),
            )
            command(
                root,
                "telemetry",
                "sweep",
                "--native-index",
                str(root / "missing-index"),
            )
            native_list: Callable[[BeadsProcess], object] = BeadsProcess.list_all
            race_connection: BeadsConnection = connection
            race_bead: str = bead

            def racing(process: BeadsProcess) -> object:
                listed = native_list(process)
                native(race_connection, "reopen", race_bead)
                return listed

            context = LaunchContext(
                SourceCommit("fixture"),
                root,
                root / "state",
                connection.directory,
                -1,
                root,
                root / "claude",
            )
            with patch.object(
                BeadsProcess, "list_all", autospec=True, side_effect=racing
            ):
                raced = sweep(context, root / "missing-index", 8)
            self.assertFalse(raced["bead_events_caught_up"])
            self.assertTrue(raced["event_retention_skipped"])
            other = command(root, "cost", "--task", OTHER)
            self.assertEqual(other["event_only_requests"], 1)
            native(connection, "close", bead)
            command(root, "cost", "--bead", bead)
            native(connection, "rename", bead, "hv-renamed")
            renamed = command(root, "cost", "--bead", "hv-renamed")
            self.assertEqual(renamed["attributed_usd"], before["attributed_usd"])
            old = command(root, "cost", "--bead", bead)
            self.assertEqual(old["renamed_to"], "hv-renamed")
            self.assertEqual(old["attributed_usd"], "0.000000000000")
            native(connection, "rename-prefix", "nx-")
            command(root, "telemetry", "reset-bead-events")
            prefixed = command(root, "cost", "--bead", "nx-renamed")
            self.assertEqual(prefixed["attributed_usd"], before["attributed_usd"])
            native(connection, "delete", "nx-renamed", "--force")
            deleted = command(root, "cost", "--bead", "nx-renamed")
            self.assertTrue(deleted["deleted"])
            self.assertEqual(deleted["attributed_usd"], before["attributed_usd"])
            balance = command(root, "cost", "--reconcile")
            self.assertEqual(balance["attributed_usd"], before["attributed_usd"])
            self.assertTrue(balance["balanced"])

            command(root, "telemetry", "reset-bead-events")
            for _ in range(2):
                retained = command(root, "cost", "--bead", "nx-renamed")
                self.assertEqual(retained["attributed_usd"], before["attributed_usd"])
                self.assertEqual(retained["interval_status"], "known")

    def test_rename_after_unobserved_close_replaces_open_interval(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            root = Path(temporary)
            setup(root, connection)
            bead = str(bd(connection, "create", "Rename after close")["id"])
            bd(connection, "--actor", THREAD, "update", bead, "--claim")
            at = last(connection, bead) + timedelta(milliseconds=1)
            claude(root, THREAD, "owned", at)
            initial = command(root, "cost", "--bead", bead)
            bd(connection, "close", bead)
            after = last(connection, bead) + timedelta(milliseconds=1)
            native(connection, "rename", bead, "hv-closed-renamed")
            claude(root, THREAD, "unowned", after)
            renamed = command(root, "cost", "--bead", "hv-closed-renamed")
            self.assertEqual(renamed["attributed_usd"], initial["attributed_usd"])
            old = command(root, "cost", "--bead", bead)
            self.assertEqual(old["renamed_to"], "hv-closed-renamed")
            self.assertEqual(old["attributed_usd"], "0.000000000000")
            balance = command(root, "cost", "--reconcile")
            self.assertEqual(balance["unowned_usd"], initial["attributed_usd"])
            self.assertEqual(balance["attributed_usd"], initial["attributed_usd"])
            self.assertTrue(balance["balanced"])
