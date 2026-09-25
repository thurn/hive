"""Native ownership and transcript journeys exercise public dashboard responses."""

import json
import sqlite3
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server_fixture import private_server
from test_bead_cost import claude, command, last, setup
from test_bead_cost import native as native_bd
from test_bead_history import sql
from test_claude import OTHER, THREAD, assistant
from test_contention import held
from test_diagnostics import SECRET, native
from test_links import bd
from test_project_observation import launch

from hive.bead_events import read as read_events
from hive.beads_process import BeadsProcess
from hive.dashboard_api import read
from hive.dashboard_summary import refresh
from hive.dashboard_values import picos
from hive.jsonvalue import record, sequence, string
from hive.usage_store import UsageStore


def settle(root: Path) -> None:
    store = UsageStore(root / "state/telemetry.sqlite3")
    for _ in range(10):
        result = refresh(store, launch(root, root), time.monotonic() + 2)
        if not result["summaries_behind"]:
            return
    raise AssertionError("Dashboard did not catch up")


def api(root: Path, *args: str) -> dict[str, object]:
    value = command(root, "dashboard", "api", *args)
    if value["code"] != "Dashboard":
        raise AssertionError(value)
    return value


def objects(value: object) -> list[dict[str, object]]:
    return [record(v) for v in sequence(value, "values")]


class DashboardTests(unittest.TestCase):
    def test_native_shared_costs_stable_tail_details_and_read_only_reads(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            root = Path(temporary)
            setup(root, connection)
            now = datetime.now(UTC) - timedelta(minutes=1)
            claude(root, THREAD, "before", now, 100000)
            claude(root, OTHER, "never-owned", now, 10)
            first = str(
                bd(
                    connection,
                    "create",
                    "Dashboard A",
                    "--metadata",
                    json.dumps({"hive_project": "sample"}),
                )["id"]
            )
            second = str(
                bd(
                    connection,
                    "create",
                    "Dashboard B",
                    "--metadata",
                    json.dumps({"hive_project": "sample"}),
                )["id"]
            )
            # Establish the initial agent key before native ownership is assigned.
            command(root, "cost", "--reconcile")
            settle(root)
            before = api(root, "feed", "--older-completed")
            key = "session:" + THREAD
            initial = next(v for v in objects(before["cards"]) if v["key"] == key)
            self.assertEqual(initial["kind"], "agent")
            for bead in (first, second):
                bd(connection, "--actor", THREAD, "update", bead, "--claim")
            at = last(connection, second) + timedelta(milliseconds=1)
            claude(root, THREAD, "shared", at, 100000)
            report = command(root, "cost", "--reconcile")
            self.assertTrue(report["balanced"])
            settle(root)
            feed = api(root, "feed", "--older-completed")
            cards = objects(feed["cards"])
            self.assertEqual(next(v for v in cards if v["key"] == key)["kind"], "tail")
            total = sum(int(string(v["amount_picos"], "amount")) for v in cards)
            self.assertEqual(total, picos(report["thread_total_usd"]))
            detail = api(root, "bead", first)
            expected = int(string(detail["amount_picos"], "amount"))
            self.assertGreater(expected, 0)
            for dimension in record(detail["breakdown"]).values():
                self.assertEqual(
                    sum(
                        int(string(v["amount_picos"], "amount"))
                        for v in objects(dimension)
                    ),
                    expected,
                )
            self.assertEqual(record(detail["beads"])["source"], "live")
            self.assertIn(
                "'<actor>'",
                string(record(record(detail["beads"])["commands"])["show"], "command"),
            )
            request_page = command(root, "cost", "--bead", first, "--requests")
            self.assertEqual(
                sum(
                    int(string(v["share_picos"], "share"))
                    for v in objects(request_page["requests"])
                ),
                expected,
            )
            # An already-open writer cannot make dashboard readers request a lock.
            db = root / "state/telemetry.sqlite3"
            with held(db, "IMMEDIATE"):
                again = api(root, "feed", "--older-completed")
            self.assertEqual(
                again["revision"], api(root, "feed", "--older-completed")["revision"]
            )
            reader = read(db)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    reader.execute("DELETE FROM card_summaries")
            finally:
                reader.close()
            # Cached native details remain useful when the native provider goes away.
            (root / "missing-beads").unlink()
            cached = record(api(root, "bead", first)["beads"])
            self.assertEqual(cached["source"], "cache")
            self.assertEqual(record(cached["bead"])["title"], "Dashboard A")

    def test_small_tails_unknown_and_deleted_beads_reconcile(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            root = Path(temporary)
            setup(root, connection)
            claude(
                root, THREAD, "small-tail", datetime.now(UTC) - timedelta(minutes=1), 10
            )
            bead = str(
                bd(
                    connection,
                    "create",
                    "Ledger owner",
                    "--metadata",
                    json.dumps({"hive_project": "sample"}),
                )["id"]
            )
            bd(connection, "--actor", THREAD, "update", bead, "--claim")
            at = last(connection, bead) + timedelta(milliseconds=1)
            claude(root, THREAD, "owned", at, 100000)
            command(root, "cost", "--reconcile")
            settle(root)
            cards = objects(api(root, "feed", "--older-completed")["cards"])
            self.assertIn("ledger:small_tails:Other", [v["key"] for v in cards])
            self.assertNotIn("session:" + THREAD, [v["key"] for v in cards])
            # Missing native history moves dollars to the uncertainty ledger,
            # without trusting the current assignee as historical evidence.
            bd(connection, "update", bead, "--assignee", OTHER)
            event_id = read_events(BeadsProcess(connection).bead_events(bead))[
                -1
            ].identity
            bd(
                connection,
                "update",
                bead,
                "--assignee",
                "00000000-0000-0000-0000-000000000001",
            )
            sql(connection, f"DELETE FROM events WHERE id='{event_id}'")
            command(root, "cost", "--bead", bead)
            report = command(root, "cost", "--reconcile")
            settle(root)
            cards = objects(api(root, "feed", "--older-completed")["cards"])
            ledger = next(
                v for v in cards if v["key"] == "ledger:unattributable:sample"
            )
            self.assertEqual(
                int(string(ledger["amount_picos"], "amount")),
                picos(report["unattributable_usd"]),
            )
            self.assertEqual(
                sum(int(string(v["amount_picos"], "amount")) for v in cards),
                picos(report["thread_total_usd"]),
            )
            uncertain = next(v for v in cards if v["key"] == "bead:" + bead)
            self.assertEqual(uncertain["state"], "Needs attention")
            # A separate known bead retains its price and an attention marker after deletion.
            known = str(bd(connection, "create", "Deleted anchor")["id"])
            bd(connection, "--actor", OTHER, "update", known, "--claim")
            claude(
                root,
                OTHER,
                "deleted-owner",
                last(connection, known) + timedelta(milliseconds=1),
                10000,
            )
            command(root, "cost", "--bead", known)
            native_bd(connection, "delete", known, "--force")
            report = command(root, "cost", "--bead", known)
            self.assertTrue(report["deleted"])
            settle(root)
            deleted = next(
                v
                for v in objects(api(root, "feed", "--older-completed")["cards"])
                if v["key"] == "bead:" + known
            )
            self.assertEqual(deleted["state"], "Needs attention")
            self.assertEqual(deleted["usd"], report["attributed_usd"])

    def test_excerpts_identity_privacy_replacement_and_unmigrated_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = command(root, "dashboard", "api", "feed")
            self.assertEqual(missing["code"], "RecoveryRequired")
            self.assertEqual(missing["schema"], 1)
            self.assertFalse((root / "state/telemetry.sqlite3").exists())
            path = root / "native.jsonl"
            at = datetime.now(UTC)
            path.write_bytes(
                native("session_meta", {"id": THREAD}, at)
                + native(
                    "response_item",
                    {
                        "type": "function_call",
                        "name": "Read",
                        "call_id": "read",
                        "arguments": json.dumps({"path": SECRET}),
                    },
                    at,
                )
                + native(
                    "response_item",
                    {
                        "type": "function_call_output",
                        "call_id": "read",
                        "output": SECRET,
                    },
                    at + timedelta(seconds=1),
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
            value = api(root, "excerpt", "--thread", THREAD, "--call", "read")
            self.assertIn(SECRET, string(value["input_summary"], "summary"))
            self.assertEqual(value["result"], SECRET)
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                dumped = "\n".join(db.iterdump())
            self.assertNotIn(SECRET, dumped)
            replacement = root / "replacement"
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
            unavailable = command(
                root,
                "dashboard",
                "api",
                "excerpt",
                "--thread",
                THREAD,
                "--call",
                "read",
            )
            self.assertEqual(unavailable["code"], "excerpt_unavailable")
            self.assertIn("replaced", string(unavailable["reason"], "reason"))

    def test_paging_filters_exact_large_amounts_and_scale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "claude.jsonl"
            path.write_bytes(assistant("seed"))
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(path),
            )
            # A synthetic warehouse fixture scales the public read path without
            # launching 6,000 native sessions or asserting implementation helpers.
            db = root / "state/telemetry.sqlite3"
            now = datetime.now(UTC).isoformat()
            amount = 10**24 + 7
            with sqlite3.connect(db) as c:
                for n in range(6000):
                    key = f"session:fixture-{n:05}"
                    c.execute(
                        "INSERT INTO card_summaries VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            key,
                            "agent",
                            "sample",
                            str(amount),
                            0,
                            0,
                            now,
                            '{"executor":"' + str(amount) + '"}',
                            "{}",
                        ),
                    )
                for n in range(1000):
                    c.execute(
                        "INSERT INTO bead_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            f"bd-{n}",
                            "sample",
                            "open",
                            f"Bead {n}",
                            now,
                            now,
                            None,
                            None,
                            0,
                            None,
                            None,
                            "",
                            "{}",
                            now,
                        ),
                    )
                    c.execute(
                        "INSERT INTO card_summaries VALUES (?,?,?,?,?,?,?,?,?)",
                        (f"bead:bd-{n}", "bead", "sample", "0", 0, 0, now, "{}", "{}"),
                    )
            start = time.monotonic()
            first = api(root, "feed", "--project", "sample", "--role", "executor")
            elapsed = time.monotonic() - start
            self.assertLess(elapsed, 0.5)
            self.assertEqual(len(objects(first["cards"])), 50)
            self.assertEqual(objects(first["cards"])[0]["amount_picos"], str(amount))
            second = api(
                root,
                "feed",
                "--project",
                "sample",
                "--role",
                "executor",
                "--cursor",
                string(first["next_cursor"], "cursor"),
            )
            self.assertFalse(
                {v["key"] for v in objects(first["cards"])}
                & {v["key"] for v in objects(second["cards"])}
            )
            self.assertEqual(
                objects(api(root, "feed", "--q=fixture-00004")["cards"])[0]["key"],
                "session:fixture-00004",
            )
            invalid = command(
                root,
                "dashboard",
                "api",
                "feed",
                "--cursor",
                string(first["next_cursor"], "cursor"),
            )
            self.assertEqual(invalid["code"], "InvalidInput")
