"""Allocation is observed through collection and public reports, not private state."""

import json
import random
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from test_bead_cost import command, total
from test_claude import THREAD, assistant

from hive.jsonvalue import record, sequence
from hive.transcript_chunks import MAX_BATCH, MAX_LINE
from hive.usage_store import row


def encoded(value: dict[str, object]) -> bytes:
    return (json.dumps(value) + "\n").encode()


def response(
    identity: str,
    prompt: int,
    *,
    cached: int = 0,
    five: int = 0,
    hour: int = 0,
    output: int = 10,
    thinking: int | None = 0,
    minute: int = 0,
    blocks: list[dict[str, object]] | None = None,
) -> bytes:
    changes: dict[str, object] = {
        "input_tokens": prompt - cached - five - hour,
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": five + hour,
        "cache_creation": {
            "ephemeral_5m_input_tokens": five,
            "ephemeral_1h_input_tokens": hour,
        },
    }
    if thinking is not None:
        changes["output_tokens_details"] = {"thinking_tokens": thinking}
    value = record(json.loads(assistant(identity, output, usage_changes=changes)))
    value["uuid"] = "block-" + identity
    value["timestamp"] = (
        datetime(2026, 9, 24, tzinfo=UTC) + timedelta(minutes=minute)
    ).isoformat()
    if blocks is not None:
        # record() copies at the JSON trust boundary.
        message = record(value["message"])
        message["content"] = blocks
        value["message"] = message
    return encoded(value)


def user(blocks: object, **extra: object) -> bytes:
    return encoded(
        {"type": "user", "sessionId": THREAD, "message": {"content": blocks}, **extra}
    )


def collect(root: Path, path: Path, *, restart: bool = False) -> None:
    args = ("--from-start",) if restart else ()
    for _ in range(20):
        result = command(
            root,
            "telemetry",
            "collect",
            "--task",
            THREAD,
            "--transcript",
            str(path),
            *args,
        )
        args = ()
        if not result["remaining_bytes"]:
            return
    raise AssertionError("Collector failed to catch up")


def allocation_rows(root: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
        fetched: object = db.execute(
            "SELECT response,bucket,tool_use_id,tool_name,component,tokens,usd,method FROM tool_allocation ORDER BY response,ordinal"
        ).fetchall()
    return tuple(row(value, 8) for value in sequence(fetched, "allocations"))


def report(root: Path) -> dict[str, object]:
    result = command(root, "cost", "--task", THREAD)
    rows = allocation_rows(root)
    page = command(root, "cost", "--task", THREAD, "--requests")
    for raw in sequence(page["requests"], "requests"):
        request = record(raw)
        charges = [value for value in rows if value[0] == request["response"]]
        if request["source"] == "events" or request["usd"] is None:
            if charges:
                raise AssertionError("Event-only or unpriced request has allocation")
            continue
        for component, field in (
            ("input", "uncached_input"),
            ("cache_read", "cache_read"),
            ("cache_write_5m", "cache_write_5m"),
            ("cache_write_1h", "cache_write_1h"),
            ("output", "output"),
            ("server_tools", None),
        ):
            matching = [value for value in charges if value[4] == component]
            count = sum(int(str(value[5])) for value in matching)
            amount = sum((Decimal(str(value[6])) for value in matching), Decimal(0))
            if count != (0 if field is None else request[field]):
                raise AssertionError((request["response"], component, count, request))
            if amount != Decimal(str(request["usd_" + component])):
                raise AssertionError((request["response"], component, amount, request))
        if sum((Decimal(str(value[6])) for value in charges), Decimal(0)) != Decimal(
            str(request["usd"])
        ):
            raise AssertionError("Allocation differs from request")
    if sum((Decimal(str(value[6])) for value in rows), Decimal(0)) != Decimal(
        str(result["priced_subset_usd"])
    ):
        raise AssertionError("Allocation differs from thread total")
    return result


class ToolAllocationTests(unittest.TestCase):
    def test_query_rows_refresh_downstream_streams_and_migrate_retained_evidence(
        self,
    ) -> None:
        from test_events import captured, spool

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            early = response("a", 100, output=1, blocks=[{"type": "text", "text": "a"}])
            later = response("b", 200, cached=100, output=0, minute=1, blocks=[])
            path.write_bytes(
                early
                + later
                + assistant(
                    "fee", usage_changes={"server_tool_use": {"web_search_requests": 2}}
                )
            )
            collect(root, path)
            report(root)
            before = allocation_rows(root)
            with path.open("ab") as stream:
                stream.write(
                    response(
                        "a",
                        100,
                        output=10,
                        blocks=[
                            {
                                "type": "tool_use",
                                "id": "call",
                                "name": "Read",
                                "input": {},
                            }
                        ],
                    )
                )
            collect(root, path)
            # Query immediately after collection, without a report to repair rows.
            after = allocation_rows(root)
            self.assertNotEqual(after, before)
            self.assertTrue(
                any(
                    value[0] == "b" and value[2] == "call" and value[3] == "Read"
                    for value in after
                )
            )
            self.assertEqual(
                next(value[5:7] for value in after if value[1] == "server_tool_fees"),
                (0, "0.020000000000"),
            )
            report(root)
            collect(root, path, restart=True)
            self.assertEqual(allocation_rows(root), after)
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                db.execute(
                    "CREATE TRIGGER reject_idle_replay BEFORE DELETE ON tool_allocation BEGIN SELECT RAISE(ABORT,'Idle allocation replay'); END"
                )
            collect(root, path)
            self.assertEqual(allocation_rows(root), after)
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                db.execute("DROP TRIGGER reject_idle_replay")
                quotes = db.execute(
                    "SELECT * FROM response_estimates ORDER BY response,tier"
                ).fetchall()
                db.execute("DROP TABLE tool_allocation")
                db.execute("PRAGMA user_version=10")
            # Migration uses saved byte facts and retained rates, even with no file.
            path.unlink()
            command(root, "telemetry", "sweep", "--native-index", str(root / "missing"))
            self.assertEqual(allocation_rows(root), after)
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                self.assertEqual(
                    db.execute(
                        "SELECT * FROM response_estimates ORDER BY response,tier"
                    ).fetchall(),
                    quotes,
                )
            spool(root, captured())
            command(root, "telemetry", "sweep", "--native-index", str(root / "missing"))
            report(root)
            self.assertEqual(allocation_rows(root), after)

    def test_positional_bands_output_ties_thinking_and_exact_totals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            path.write_bytes(
                response(
                    "first",
                    100,
                    hour=100,
                    output=6,
                    thinking=1,
                    blocks=[
                        {
                            "type": "thinking",
                            "thinking": "",
                            "signature": "not a token weight",
                        },
                        {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                        {"type": "tool_use", "id": "t2", "name": "Bash", "input": {}},
                    ],
                )
                + user(
                    [{"type": "tool_result", "tool_use_id": "t1", "content": "abcd"}],
                    toolUseResult="ignored" * 10000,
                )
                + response(
                    "second",
                    110,
                    cached=100,
                    hour=3,
                    five=2,
                    output=0,
                    minute=1,
                    blocks=[],
                )
            )
            collect(root, path)
            result = report(root)
            tools = {
                str(record(value)["tool"]): record(value)
                for value in sequence(result["by_tool"], "tools")
            }
            self.assertEqual(tools["Read"]["invocation_usd"], "0.000060000000")
            self.assertEqual(tools["Bash"]["invocation_usd"], "0.000040000000")
            self.assertEqual(tools["Read"]["carrying_usd"], "0.000038000000")
            self.assertEqual(tools["Bash"]["carrying_usd"], "0.000012000000")
            self.assertEqual(result["unallocated_usd"], "0.000000000000")
            self.assertIsNone(result["allocation_error"])
            self.assertEqual(
                total(result["allocation_buckets"]),
                Decimal(str(result["priced_subset_usd"])),
            )
            self.assertEqual(result["context_resets"], 0)
            self.assertEqual(result["suspected_prefix_rewrite"], 0)
            self.assertEqual(result["thinking_unmeasured"], 0)
            before = result["allocation_buckets"]
            collect(root, path, restart=True)
            self.assertEqual(report(root)["allocation_buckets"], before)
            # Neither the full tool result nor any prompt text is copied.
            contents = (root / "state/telemetry.sqlite3").read_bytes()
            self.assertNotIn(b"not a token weight", contents)
            self.assertNotIn(b"ignoredignored", contents)

    def test_random_segments_and_bands_reconcile_without_float_rounding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            rng = random.Random(1645)
            data = bytearray()
            prompt = 0
            for index in range(150):
                prompt += rng.randrange(1, 500)
                cached = rng.randrange(prompt + 1)
                hour = rng.randrange(prompt - cached + 1)
                five = rng.randrange(prompt - cached - hour + 1)
                output = rng.randrange(200)
                data.extend(
                    user(
                        [
                            {
                                "type": "tool_result",
                                "tool_use_id": f"t{index-1}",
                                "content": "x" * rng.randrange(300),
                            }
                        ]
                    )
                )
                data.extend(
                    response(
                        str(index),
                        prompt,
                        cached=cached,
                        hour=hour,
                        five=five,
                        output=output,
                        thinking=rng.randrange(output + 1),
                        minute=index * 70,
                        blocks=[
                            {"type": "thinking", "signature": "opaque"},
                            {"type": "text", "text": "text" * rng.randrange(10)},
                            {
                                "type": "tool_use",
                                "id": f"t{index}",
                                "name": "Read",
                                "input": {"n": index},
                            },
                        ],
                    )
                )
            path.write_bytes(data)
            collect(root, path)
            result = report(root)
            self.assertEqual(result["priced_responses"], 150)
            self.assertEqual(result["unallocated_usd"], "0.000000000000")
            self.assertEqual(result["context_resets"], 0)
            self.assertEqual(result["suspected_prefix_rewrite"], 0)
            self.assertEqual(
                total(result["allocation_buckets"]),
                Decimal(str(result["priced_subset_usd"])),
            )

    def test_resets_cache_expiry_and_unknown_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            path.write_bytes(
                response("a", 100, five=100)
                + response("b", 110, cached=50, five=60, minute=1)
                + response("c", 120, five=120, minute=20)
                + encoded(
                    {
                        "type": "system",
                        "sessionId": THREAD,
                        "subtype": "microcompact_boundary",
                    }
                )
                + response("d", 130, cached=120, minute=21)
                + response("e", 50, cached=50, minute=22)
                + encoded(
                    {
                        "type": "attachment",
                        "sessionId": THREAD,
                        "attachment": {"type": "future-kind", "rendered": "visible"},
                    }
                )
                + encoded(
                    {
                        "type": "file-history-snapshot",
                        "snapshot": {"text": "invisible" * 1000},
                    }
                )
                + response("f", 60, cached=50, minute=23)
            )
            collect(root, path)
            result = report(root)
            self.assertEqual(result["context_resets"], 2)
            self.assertEqual(result["suspected_prefix_rewrite"], 1)
            self.assertEqual(result["unknown_attachment_kinds"], 1)
            self.assertEqual(result["unallocated_usd"], "0.000000000000")
            buckets = {
                str(record(v)["bucket"])
                for v in sequence(result["allocation_buckets"], "buckets")
            }
            self.assertIn("rewritten_context", buckets)
            self.assertIn("reminders_and_attachments", buckets)

    def test_oversized_parts_cross_chunks_and_migration_replays_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            oversized = user("x" * (MAX_BATCH + MAX_LINE))
            path.write_bytes(
                response("a", 100, blocks=[])
                + oversized
                + response("b", 200, cached=100, minute=1)
            )
            collect(root, path)
            result = report(root)
            self.assertEqual(result["parse_gaps"], 1)
            self.assertEqual(result["unallocated_usd"], "0.000000000000")
            self.assertIn("bytes_with_oversized", record(result["allocation_methods"]))
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                self.assertEqual(
                    db.execute(
                        "SELECT SUM(bytes) FROM segment_parts WHERE kind='oversized'"
                    ).fetchone(),
                    (len(oversized) - 1,),
                )
                for table in (
                    "tool_oversized",
                    "allocation_seen",
                    "allocation_cursor",
                    "allocation_responses",
                    "pending_parts",
                    "segment_parts",
                    "response_blocks",
                ):
                    db.execute("DROP TABLE " + table)
                db.execute("PRAGMA user_version=8")
            collect(root, path)
            migrated = report(root)
            self.assertEqual(
                migrated["allocation_buckets"], result["allocation_buckets"]
            )
            self.assertEqual(migrated["observed_responses"], 2)
            self.assertEqual(migrated["parse_gaps"], 1)

    def test_single_part_accuracy_has_sample_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            data = bytearray(response("base", 100, output=0, blocks=[]))
            for index in range(21):
                data.extend(user("abcd" * (index + 1)))
                data.extend(
                    response(
                        str(index),
                        100 + (index + 1) * (index + 2) // 2,
                        cached=100 + index * (index + 1) // 2,
                        output=0,
                        minute=index + 1,
                        blocks=[],
                    )
                )
            path.write_bytes(data)
            collect(root, path)
            result = report(root)
            error = record(result["byte_split_error"])
            self.assertEqual(error["samples"], 21)
            self.assertEqual(error["median_relative_error"], 0)
            self.assertEqual(error["p90_relative_error"], 0)

    def test_streamed_block_sizes_and_final_thinking_match_final_only(self) -> None:
        cases: tuple[tuple[list[dict[str, object]], int], ...] = (
            (
                [
                    {"type": "text", "text": "a" * 100},
                    {"type": "tool_use", "id": "t", "name": "Read", "input": {}},
                ],
                0,
            ),
            (
                [
                    {"type": "thinking", "signature": "opaque"},
                    {"type": "tool_use", "id": "t", "name": "Read", "input": {}},
                ],
                90,
            ),
        )
        for final_blocks, measured in cases:
            with (
                tempfile.TemporaryDirectory() as streamed,
                tempfile.TemporaryDirectory() as complete,
            ):
                root = Path(streamed)
                fresh = Path(complete)
                path = root / f"{THREAD}.jsonl"
                other = fresh / f"{THREAD}.jsonl"
                first = response(
                    "a",
                    100,
                    output=1,
                    thinking=None,
                    blocks=[{"type": "text", "text": "a"}],
                )
                final = response(
                    "a", 100, output=100, thinking=measured, blocks=final_blocks
                )
                later = response("b", 250, cached=100, minute=1, output=0, blocks=[])
                path.write_bytes(first)
                collect(root, path)
                report(root)
                with path.open("ab") as stream:
                    stream.write(final + later)
                collect(root, path)
                other.write_bytes(final + later)
                collect(fresh, other)
                observed = report(root)
                expected = report(fresh)
                self.assertEqual(observed["by_tool"], expected["by_tool"])
                self.assertEqual(
                    observed["allocation_buckets"], expected["allocation_buckets"]
                )
                self.assertEqual(observed["thinking_unmeasured"], 0)
                collect(root, path, restart=True)
                self.assertEqual(
                    report(root)["allocation_buckets"], expected["allocation_buckets"]
                )

    def test_inode_replacement_does_not_requeue_historical_parts(self) -> None:
        with (
            tempfile.TemporaryDirectory() as streamed,
            tempfile.TemporaryDirectory() as complete,
        ):
            root = Path(streamed)
            fresh = Path(complete)
            path = root / f"{THREAD}.jsonl"
            other = fresh / f"{THREAD}.jsonl"
            prefix = (
                response("a", 100, output=0, blocks=[])
                + user("x" * 100)
                + response("b", 110, cached=100, output=0, minute=1, blocks=[])
            )
            suffix = user(
                [{"type": "tool_result", "tool_use_id": "t", "content": "x" * 100}]
            ) + response("c", 120, cached=110, output=0, minute=2, blocks=[])
            path.write_bytes(prefix)
            collect(root, path)
            replacement = root / "replacement"
            replacement.write_bytes(prefix)
            replacement.replace(path)
            collect(root, path)
            with path.open("ab") as stream:
                stream.write(suffix)
            collect(root, path)
            other.write_bytes(prefix + suffix)
            collect(fresh, other)
            self.assertEqual(
                report(root)["allocation_buckets"], report(fresh)["allocation_buckets"]
            )

    def test_oversized_reread_with_different_fragment_boundaries_is_idempotent(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as streamed,
            tempfile.TemporaryDirectory() as complete,
        ):
            root = Path(streamed)
            fresh = Path(complete)
            path = root / f"{THREAD}.jsonl"
            other = fresh / f"{THREAD}.jsonl"
            first = response("a", 100, output=0, blocks=[])
            giant = user("x" * 600000)
            next_request = response("b", 200, cached=100, output=0, minute=1, blocks=[])
            suffix = user(
                [{"type": "tool_result", "tool_use_id": "t", "content": "x" * 100}]
            ) + response("c", 300, cached=200, output=0, minute=2, blocks=[])
            path.write_bytes(first + giant[:300000])
            collect(root, path)
            with path.open("ab") as stream:
                stream.write(giant[300000:] + next_request)
            collect(root, path)
            collect(root, path, restart=True)
            with path.open("ab") as stream:
                stream.write(suffix)
            collect(root, path)
            other.write_bytes(first + giant + next_request + suffix)
            collect(fresh, other)
            self.assertEqual(
                report(root)["allocation_buckets"], report(fresh)["allocation_buckets"]
            )

    def test_migration_keeps_earlier_distinct_streaming_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            early = record(
                json.loads(
                    response(
                        "a", 100, output=1, blocks=[{"type": "text", "text": "a" * 100}]
                    )
                )
            )
            early["uuid"] = "part-1"
            final = record(
                json.loads(
                    response(
                        "a",
                        100,
                        output=100,
                        blocks=[
                            {"type": "tool_use", "id": "t", "name": "Read", "input": {}}
                        ],
                    )
                )
            )
            final["uuid"] = "part-2"
            path.write_bytes(
                encoded(early)
                + encoded(final)
                + response("b", 250, cached=100, minute=1, output=0, blocks=[])
            )
            collect(root, path)
            before = report(root)
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                for table in (
                    "tool_oversized",
                    "allocation_seen",
                    "allocation_cursor",
                    "allocation_responses",
                    "pending_parts",
                    "segment_parts",
                    "response_blocks",
                ):
                    db.execute("DROP TABLE " + table)
                db.execute("PRAGMA user_version=8")
            collect(root, path)
            after = report(root)
            self.assertEqual(after["allocation_buckets"], before["allocation_buckets"])
            self.assertEqual(after["by_tool"], before["by_tool"])
