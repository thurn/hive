"""Public request pages reconcile with thread totals and persisted SQL evidence."""

import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from test_claude import THREAD, assistant
from test_contention import held, hive
from test_cost import context
from test_usage import TASK, counters, header, response

from hive.jsonvalue import record, sequence, string
from hive.usage_store import UsageStore


class RequestDetailTests(unittest.TestCase):
    def test_claude_components_partial_updates_and_unpriced_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            path.write_bytes(
                assistant("partial", 1, complete=False)
                + assistant("unknown", usage_changes={"speed": "warp"})
            )
            store = UsageStore(root / "state/telemetry.sqlite3")
            store.collect(THREAD, path)
            first = hive(root, "cost", "--task", THREAD, "--requests")
            self.assertEqual(first.returncode, 0, first.stderr)
            rows = [
                record(value)
                for value in sequence(
                    record(json.loads(first.stdout))["requests"], "requests"
                )
            ]
            partial, unknown = rows
            self.assertIn("possibly_partial", sequence(partial["flags"], "flags"))
            self.assertEqual(partial["uncached_input"], 2)
            self.assertEqual(partial["cache_write_1h"], 20)
            self.assertEqual(partial["usd_output"], "0.000020000000")
            self.assertIsNone(unknown["usd"])
            self.assertEqual(unknown["unpriced_reason"], "unknown_modifier")
            with path.open("ab") as stream:
                stream.write(assistant("partial", 10))
            store.collect(THREAD, path)
            after = hive(root, "cost", "--task", THREAD, "--requests")
            self.assertEqual(after.returncode, 0, after.stderr)
            detail = record(
                sequence(record(json.loads(after.stdout))["requests"], "requests")[0]
            )
            self.assertNotIn("possibly_partial", sequence(detail["flags"], "flags"))
            self.assertEqual(detail["usd_output"], "0.000200000000")
            components = sum(
                Decimal(str(value))
                for name, value in detail.items()
                if name.startswith("usd_")
            )
            total = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(components, Decimal(str(total["priced_subset_usd"])))
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT usd_output,usd FROM request_detail WHERE response='partial'"
                    ).fetchone(),
                    ("0.000200000000", "0.000438000000"),
                )
            # Upgrade a populated v3 store with old quote JSON: component
            # strings are recovered from retained evidence without rereading.
            with sqlite3.connect(store.path) as db:
                db.execute("DROP VIEW request_detail")
                db.execute(
                    "UPDATE response_estimates SET quote=json_remove(quote,'$.components_usd')"
                )
                db.execute("ALTER TABLE claude_cost_states DROP COLUMN timestamp_known")
                db.execute("DROP VIEW claude_agent_parents")
                db.execute("DROP TABLE claude_tool_owners")
                for table in (
                    "tool_oversized",
                    "allocation_seen",
                    "allocation_cursor",
                    "allocation_responses",
                    "pending_parts",
                    "segment_parts",
                    "response_blocks",
                    "bead_replays",
                    "bead_intervals",
                    "bead_seen_owners",
                    "bead_events",
                    "bead_event_cursor",
                    "bead_snapshots",
                ):
                    db.execute("DROP TABLE " + table)
                db.execute("PRAGMA user_version=3")
            store.collect(THREAD, path)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT usd_output,usd FROM request_detail WHERE response='partial'"
                    ).fetchone(),
                    ("0.000200000000", "0.000438000000"),
                )
            rejected = hive(
                root, "cost", "--task", THREAD, "--requests", "--tier", "standard"
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("InvalidInput", rejected.stderr)

    def test_pages_select_one_tier_and_preserve_large_integer_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "native.jsonl"
            path.write_bytes(
                header()
                + context()
                + b"".join(response(f"r-{i:04}", counters(2**62)) for i in range(1001))
            )
            store = UsageStore(root / "state/telemetry.sqlite3")
            store.collect(TASK, path)
            identities: list[str] = []
            cursor: str | None = None
            for expected in (500, 500, 1):
                args = () if cursor is None else ("--cursor", cursor)
                result = hive(
                    root, "cost", "--task", TASK, "--requests", "--tier", "fast", *args
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                page = record(json.loads(result.stdout))
                rows = [
                    record(value) for value in sequence(page["requests"], "requests")
                ]
                self.assertEqual(len(rows), expected)
                identities.extend(
                    string(value["response"], "response") for value in rows
                )
                self.assertTrue(all(value["tier"] == "fast" for value in rows))
                self.assertTrue(
                    all(
                        value["agent"] is None and value["skill"] is None
                        for value in rows
                    )
                )
                self.assertEqual(
                    rows[0]["usd_input"],
                    f"{(2**62 * 40_000_000)//10**12}.{(2**62 * 40_000_000)%10**12:012}",
                )
                next_cursor = page["next_cursor"]
                self.assertTrue(next_cursor is None or isinstance(next_cursor, str))
                cursor = next_cursor if isinstance(next_cursor, str) else None
                if expected == 500:
                    self.assertIsNotNone(cursor)
            self.assertIsNone(cursor)
            self.assertEqual(len(set(identities)), 1001)
            self.assertEqual(identities, sorted(identities))
            standard = hive(root, "cost", "--task", TASK, "--requests")
            self.assertEqual(standard.returncode, 0, standard.stderr)
            page = record(json.loads(standard.stdout))
            query_cursor = str(page["next_cursor"])
            for arguments in (
                ("--cursor", "garbage"),
                ("--cursor", query_cursor, "--tier", "fast"),
            ):
                invalid = hive(root, "cost", "--task", TASK, "--requests", *arguments)
                self.assertEqual(invalid.returncode, 1)
                self.assertIn("InvalidInput", invalid.stderr)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM request_detail WHERE tier='fast'"
                    ).fetchone(),
                    (1001,),
                )

    def test_contended_retention_is_visible_and_readers_do_not_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            path.write_bytes(assistant("request"))
            store = UsageStore(root / "state/telemetry.sqlite3")
            store.collect(THREAD, path)
            with held(store.path, "IMMEDIATE"):
                result = hive(root, "cost", "--task", THREAD, "--requests")
                self.assertEqual(result.returncode, 0, result.stderr)
                page = record(json.loads(result.stdout))
                self.assertEqual(page["unretained_estimates"], 1)
                self.assertIn(
                    "unretained",
                    sequence(
                        record(sequence(page["requests"], "requests")[0])["flags"],
                        "flags",
                    ),
                )
            result = hive(root, "cost", "--task", THREAD, "--requests")
            self.assertEqual(result.returncode, 0, result.stderr)
            page = record(json.loads(result.stdout))
            self.assertEqual(page["unretained_estimates"], 0)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute("SELECT usd FROM request_detail").fetchone(),
                    ("0.000438000000",),
                )
