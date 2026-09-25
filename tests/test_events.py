"""Captured OTLP shapes, public sweeps and cost commands exercise event coverage."""

import copy
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from test_claude import THREAD, assistant
from test_contention import ROOT, hive

from hive.event_ingest import ingest
from hive.jsonvalue import record, sequence, string
from hive.usage_store import UsageStore


def captured() -> dict[str, object]:
    return record(
        json.loads((ROOT / "tests/fixtures/claude-otlp-2.1.282.json").read_text())
    )


def logs(value: dict[str, object]) -> list[dict[str, object]]:
    resource = record(sequence(value["resourceLogs"], "resources")[0])
    scope = record(sequence(resource["scopeLogs"], "scopes")[0])
    return [record(item) for item in sequence(scope["logRecords"], "logs")]


def replace_logs(
    value: dict[str, object], records: list[dict[str, object]]
) -> dict[str, object]:
    result = copy.deepcopy(value)
    resource = record(sequence(result["resourceLogs"], "resources")[0])
    scope = record(sequence(resource["scopeLogs"], "scopes")[0])
    scope["logRecords"] = records
    resource["scopeLogs"] = [scope]
    result["resourceLogs"] = [resource]
    return result


def change(value: dict[str, object], **changes: object) -> dict[str, object]:
    result = copy.deepcopy(value)
    attributes = {
        string(record(item)["key"], "key"): record(item)["value"]
        for item in sequence(result["attributes"], "attributes")
    }
    for key, item in changes.items():
        if item is None:
            attributes.pop(key, None)
        else:
            attributes[key] = {
                "intValue" if isinstance(item, int) else "stringValue": item
            }
    result["attributes"] = [
        {"key": key, "value": item} for key, item in attributes.items()
    ]
    if "event.name" in changes:
        result["body"] = {"stringValue": "claude_code." + str(changes["event.name"])}
    return result


def spool(root: Path, value: object, name: str = "1-1.json") -> Path:
    parent = root / "state/otlp-spool"
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / name
    path.write_text(json.dumps(value))
    return path


def transcript(root: Path) -> Path:
    value = record(
        json.loads(
            assistant(
                "req_fixture_capture",
                4,
                usage_changes={
                    "input_tokens": 2,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 2489,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 0,
                        "ephemeral_1h_input_tokens": 2489,
                    },
                },
            )
        )
    )
    value["timestamp"] = "2026-09-25T01:05:59.079Z"
    total = {
        "type": "cost-state",
        "sessionId": THREAD,
        "startTime": 1790298356939,
        "totalDuration": 2185,
        "timestamp": "2026-09-25T01:05:59.124Z",
        "totalCostUSD": 0.02,
    }
    path = root / f"{THREAD}.jsonl"
    path.write_text(json.dumps(value) + "\n" + json.dumps(total) + "\n")
    return path


class EventTests(unittest.TestCase):
    def test_captured_export_joins_exactly_without_retaining_personal_attributes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = transcript(root)
            collected = hive(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(source),
            )
            self.assertEqual(collected.returncode, 0, collected.stderr)
            path = spool(root, captured())
            # A sweep ingests even with zero selected threads and unavailable Beads.
            swept = hive(
                root, "telemetry", "sweep", "--native-index", str(root / "missing")
            )
            self.assertEqual(swept.returncode, 0, swept.stderr)
            result = record(json.loads(swept.stdout))
            self.assertEqual(result["event_ingested_records"], 5)
            self.assertTrue(result["event_retention_skipped"])
            self.assertFalse(path.exists())
            reported = hive(root, "cost", "--task", THREAD)
            self.assertEqual(reported.returncode, 0, reported.stderr)
            cost = record(json.loads(reported.stdout))
            self.assertEqual(cost["host_reported_reason"], None)
            self.assertEqual(record(cost["host_reported"])["usd"], "0.02")
            self.assertEqual(cost["complete_estimate_usd"], "0.020000000000")
            self.assertEqual(cost["event_coverage"], 1.0)
            self.assertEqual(cost["event_sequence_gaps"], 0)
            self.assertEqual(cost["event_token_mismatches"], 0)
            self.assertEqual(cost["event_only_requests"], 0)
            detail = record(
                json.loads(hive(root, "cost", "--task", THREAD, "--requests").stdout)
            )
            request = record(sequence(detail["requests"], "requests")[0])
            self.assertEqual(request["source"], "both")
            self.assertEqual(request["query_source"], "sdk")
            self.assertEqual(request["host_cost_usd"], "0.020000000000")
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                dump = "\n".join(db.iterdump())
                self.assertNotIn("redacted@example.test", dump)
                self.assertNotIn("organization.id", dump)
                self.assertNotIn("user.email", dump)
            spool(root, captured(), "2-1.json")
            store = UsageStore(root / "state/telemetry.sqlite3")
            ingest(store, root / "state", time.monotonic() + 5)
            repeated = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(repeated["event_host_total_usd"], "0.020000000000")
            self.assertEqual(repeated["event_sequence_gaps"], 0)

    def test_events_before_transcripts_preserve_rate_evidence_and_never_double_count(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "state/telemetry.sqlite3")
            spool(root, captured())
            ingest(store, root / "state", time.monotonic() + 5)
            before = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(before["event_only_requests"], 1)
            self.assertEqual(before["event_only_usd"], "0.020000000000")
            self.assertIsNone(before["complete_estimate_usd"])
            # Change retained card evidence to an old, internally consistent
            # rate schedule; joining must preserve that card, not today's rates.
            with sqlite3.connect(store.path) as db:
                quoted = record(
                    json.loads(
                        db.execute("SELECT quote FROM response_estimates").fetchone()[0]
                    )
                )
                rates = record(quoted["rates_picos_per_token"])
                quoted["rates_picos_per_token"] = {
                    key: int(str(value)) * 2 for key, value in rates.items()
                }
                quoted["usd"] = "0.040000000000"
                quoted["price_observed"] = "2020-01-01"
                db.execute(
                    "UPDATE response_estimates SET quote=?", (json.dumps(quoted),)
                )
            path = transcript(root)
            store.collect(THREAD, path)
            after = hive(root, "cost", "--task", THREAD)
            self.assertEqual(after.returncode, 0, after.stderr)
            cost = record(json.loads(after.stdout))
            self.assertEqual(cost["event_only_requests"], 0)
            self.assertEqual(cost["priced_subset_usd"], "0.040000000000")
            self.assertEqual(cost["observed_responses"], 1)
            detail = record(
                json.loads(hive(root, "cost", "--task", THREAD, "--requests").stdout)
            )
            self.assertEqual(len(sequence(detail["requests"], "requests")), 1)

    def test_missing_malformed_conflicting_and_unjoinable_events_stay_visible(
        self,
    ) -> None:
        original = captured()
        values = logs(original)
        api = values[3]
        cases = (
            ("sequence_gap", values[:1] + values[2:], "event_sequence_gaps", 1),
            ("missing_zero", values[1:], "event_sequence_gaps", 1),
            (
                "malformed_request",
                values[:3] + [change(api, model=None)] + values[4:],
                "rejected_records",
                1,
            ),
            (
                "conflicting_duplicate",
                values + [change(api, output_tokens=5)],
                "rejected_records",
                1,
            ),
            (
                "unjoinable",
                values
                + [
                    change(
                        api,
                        **{
                            "request_id": None,
                            "client_request_id": "unjoinable-client",
                            "event.sequence": 5,
                        },
                    )
                ],
                "unjoinable_events",
                1,
            ),
            (
                "api_error",
                values
                + [change(api, **{"event.name": "api_error", "event.sequence": 5})],
                "api_errors",
                1,
            ),
        )
        for name, records, key, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                store = UsageStore(root / "state/telemetry.sqlite3")
                store.collect(THREAD, transcript(root))
                spool(root, replace_logs(original, records))
                ingested = ingest(store, root / "state", time.monotonic() + 5)
                self.assertIsNone(ingested["event_ingest_error"])
                completed = hive(root, "cost", "--task", THREAD)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = record(json.loads(completed.stdout))
                self.assertEqual(result[key], expected)
                self.assertEqual(result["priced_subset_usd"], "0.020000000000")
                self.assertEqual(result["event_only_requests"], 0)
                if name != "api_error":
                    self.assertIsNone(result["complete_estimate_usd"])
                else:
                    self.assertEqual(result["complete_estimate_usd"], "0.020000000000")
                if name == "unjoinable":
                    page = record(
                        json.loads(
                            hive(root, "cost", "--task", THREAD, "--requests").stdout
                        )
                    )
                    rows = [
                        record(value)
                        for value in sequence(page["requests"], "requests")
                    ]
                    unjoinable = next(
                        value for value in rows if value["source"] == "events"
                    )
                    self.assertIsNone(unjoinable["usd"])
                    self.assertEqual(unjoinable["unpriced_reason"], "unjoinable_event")

    def test_side_requests_derive_ttl_geo_and_disclose_fallbacks(self) -> None:
        for micros, model, flag, amount in (
            (20000, "claude-opus-5-5", "cache_ttl_derived", "0.020000000000"),
            (22000, "claude-opus-5-5", "geo_derived", "0.022000000000"),
            (1, "claude-opus-5-5", "cache_ttl_assumed", "0.020000000000"),
            (20000, "future-model", "cache_ttl_assumed", None),
        ):
            with (
                self.subTest(micros=micros, model=model),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                store = UsageStore(root / "state/telemetry.sqlite3")
                original = captured()
                records = logs(original)
                side = change(
                    records[3],
                    **{
                        "request_id": "side-request",
                        "event.sequence": 5,
                        "cost_usd_micros": micros,
                        "query_source": "compact",
                        "model": model,
                    },
                )
                spool(root, replace_logs(original, records + [side]))
                ingest(store, root / "state", time.monotonic() + 5)
                path = transcript(root)
                lines = path.read_text().splitlines()
                total = record(json.loads(lines[1]))
                total["totalCostUSD"] = 0.02 + micros / 1_000_000
                path.write_text(lines[0] + "\n" + json.dumps(total) + "\n")
                store.collect(THREAD, path)
                page = hive(root, "cost", "--task", THREAD, "--requests")
                self.assertEqual(page.returncode, 0, page.stderr)
                rows = [
                    record(value)
                    for value in sequence(
                        record(json.loads(page.stdout))["requests"], "requests"
                    )
                ]
                request = next(value for value in rows if value["source"] == "events")
                self.assertEqual(request["usd"], amount)
                self.assertIn(flag, sequence(request["flags"], "flags"))
                cost = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
                self.assertEqual(cost["event_only_requests"], 1)
                self.assertEqual(cost["event_coverage"], 1)
                if model == "future-model":
                    self.assertIsNone(cost["complete_estimate_usd"])
                    self.assertEqual(cost["event_only_unpriced_requests"], 1)
                else:
                    self.assertIsNotNone(cost["complete_estimate_usd"], cost)

    def test_transcript_wins_token_disagreement_and_rejection_blocks_completeness(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "state/telemetry.sqlite3")
            spool(root, captured())
            ingest(store, root / "state", time.monotonic() + 5)
            path = transcript(root)
            first, total = path.read_text().splitlines()
            value = record(json.loads(first))
            message = record(value["message"])
            usage = record(message["usage"])
            usage["output_tokens"] = 5
            message["usage"] = usage
            value["message"] = message
            path.write_text(json.dumps(value) + "\n" + total + "\n")
            store.collect(THREAD, path)
            cost = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(cost["event_token_mismatches"], 1)
            self.assertEqual(cost["priced_subset_usd"], "0.020020000000")
            self.assertIsNone(cost["complete_estimate_usd"])
            private = spool(root, {"unexpected": "private-body"}, "9-1.json")
            private.write_text('{"private": "not valid json')
            ingest(store, root / "state", time.monotonic() + 5)
            quarantine = root / "state/otlp-spool/rejected/9-1.json"
            self.assertTrue(quarantine.exists())
            self.assertNotIn("private", quarantine.read_text())
            self.assertEqual(quarantine.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(store.path) as db:
                self.assertNotIn("private-body", "\n".join(db.iterdump()))

    def test_retention_and_ingest_deadlines_do_not_drop_linked_evidence(self) -> None:
        from datetime import UTC, datetime, timedelta

        from hive.collection_registry import CollectionRegistry
        from hive.event_retention import retain
        from hive.thread_links import ThreadLink

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "state/telemetry.sqlite3")
            old = captured()
            old_time = (datetime.now(UTC) - timedelta(days=8)).isoformat()
            old = replace_logs(
                old,
                [change(value, **{"event.timestamp": old_time}) for value in logs(old)],
            )
            path = spool(root, old)
            untouched = ingest(store, root / "state", time.monotonic() - 1)
            self.assertEqual(untouched["event_ingested_bytes"], 0)
            self.assertTrue(path.exists())
            ingest(store, root / "state", time.monotonic() + 5)
            registry = CollectionRegistry(store)
            registry.refresh(
                (ThreadLink(THREAD, "hv-test", "executor", True),), (), None
            )
            retain(store, root / "state", time.monotonic() + 5, permitted=True)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM claude_request_events").fetchone(),
                    (1,),
                )
            registry.refresh((), (), None)
            retain(store, root / "state", time.monotonic() + 5, permitted=False)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM claude_request_events").fetchone(),
                    (1,),
                )
            retain(store, root / "state", time.monotonic() + 5, permitted=True)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM claude_request_events").fetchone(),
                    (0,),
                )
            path = spool(root, captured(), "expired-1.json")
            import os

            old_stamp = (datetime.now(UTC) - timedelta(days=8)).timestamp()
            os.utime(path, (old_stamp, old_stamp))
            retain(store, root / "state", time.monotonic() + 5, permitted=False)
            self.assertTrue(path.exists())
            result = retain(store, root / "state", time.monotonic() + 5, permitted=True)
            self.assertEqual(result["event_expired_files"], 1)
            self.assertFalse(path.exists())

    def test_cursor_failure_rolls_back_events_and_keeps_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "state/telemetry.sqlite3")
            with store.connect() as db:
                db.execute(
                    "CREATE TRIGGER fail_cursor BEFORE INSERT ON otlp_ingest_files BEGIN SELECT RAISE(ABORT, 'interrupted'); END"
                )
            path = spool(root, captured())
            with self.assertRaises(sqlite3.IntegrityError):
                ingest(store, root / "state", time.monotonic() + 5)
            self.assertTrue(path.exists())
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM claude_request_events").fetchone(),
                    (0,),
                )
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM claude_event_sequences"
                    ).fetchone(),
                    (0,),
                )

    def test_resumes_have_separate_sequence_origins_and_clear_separates_sessions(
        self,
    ) -> None:
        from datetime import timedelta

        from test_claude import OTHER

        from hive.usage import timestamp

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "state/telemetry.sqlite3")
            original = captured()
            spool(root, original)
            ingest(store, root / "state", time.monotonic() + 5)
            for index, task in ((1, THREAD), (2, OTHER)):
                resumed: list[dict[str, object]] = []
                for value in logs(original):
                    attrs = {
                        str(record(item)["key"]): record(record(item)["value"])
                        for item in sequence(value["attributes"], "attributes")
                    }
                    at = timestamp(attrs["event.timestamp"]["stringValue"]) + timedelta(
                        minutes=index
                    )
                    edits: dict[str, object] = {
                        "event.timestamp": at.isoformat(),
                        "session.id": task,
                    }
                    if "request_id" in attrs:
                        edits["request_id"] = f"request-{index}"
                    if "client_request_id" in attrs:
                        edits["client_request_id"] = f"client-{index}"
                    resumed.append(change(value, **edits))
                spool(root, replace_logs(original, resumed), f"{index + 1}-1.json")
                ingest(store, root / "state", time.monotonic() + 5)
            for task, count in ((THREAD, 2), (OTHER, 1)):
                completed = hive(root, "cost", "--task", task)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = record(json.loads(completed.stdout))
                self.assertEqual(result["event_sequence_gaps"], 0)
                self.assertEqual(result["conflicting_event_sequences"], 0)
                self.assertEqual(result["event_only_requests"], count)

    def test_v4_upgrade_recovers_actual_timestamp_less_cost_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "state/telemetry.sqlite3")
            path = transcript(root)
            records = [json.loads(line) for line in path.read_text().splitlines()]
            records[-1].pop("timestamp")
            path.write_text("".join(json.dumps(row) + "\n" for row in records))
            store.collect(THREAD, path)
            with sqlite3.connect(store.path) as db:
                db.execute("ALTER TABLE claude_cost_states DROP COLUMN timestamp_known")
                db.execute("DELETE FROM claude_cost_states")
                db.execute(
                    "INSERT INTO gaps(task,file,device,inode,position,detail) VALUES (?,'',0,0,0,?)",
                    (
                        THREAD,
                        "Invalid cost-state: observation timestamp must be a string",
                    ),
                )
                db.execute("DROP VIEW claude_agent_parents")
                db.execute("DROP TABLE claude_tool_owners")
                for table in (
                    "bead_replays",
                    "bead_intervals",
                    "bead_seen_owners",
                    "bead_events",
                    "bead_event_cursor",
                    "bead_snapshots",
                ):
                    db.execute("DROP TABLE " + table)
                db.execute("PRAGMA user_version=4")
            result = store.collect(THREAD, path)
            self.assertGreater(int(str(result["read_bytes"])), 0)
            cost = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(cost["parse_gaps"], 0)
            self.assertIsNone(cost["unrecorded_usd_lower_bound"])
            self.assertEqual(
                cost["host_reported_reason"], "cost_state_timestamp_unavailable"
            )
            self.assertEqual(cost["host_process_segments"], 1)

    def test_event_model_disagreement_cannot_suppress_transcript_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "state/telemetry.sqlite3")
            spool(root, captured())
            ingest(store, root / "state", time.monotonic() + 5)
            self.assertEqual(hive(root, "cost", "--task", THREAD).returncode, 0)
            path = transcript(root)
            path.write_text(
                path.read_text().replace("claude-opus-5-5", "claude-haiku-4-5")
            )
            store.collect(THREAD, path)
            result = hive(root, "cost", "--task", THREAD)
            self.assertEqual(result.returncode, 0, result.stderr)
            cost = record(json.loads(result.stdout))
            self.assertEqual(cost["priced_subset_usd"], "0.005000000000")
            self.assertEqual(cost["event_token_mismatches"], 1)
            self.assertIsNone(cost["complete_estimate_usd"])

    def test_unknown_transcript_model_replaces_conflicting_event_quote(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "state/telemetry.sqlite3")
            spool(root, captured())
            ingest(store, root / "state", time.monotonic() + 5)
            self.assertEqual(hive(root, "cost", "--task", THREAD).returncode, 0)
            path = transcript(root)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            rows[0]["message"]["model"] = "future-model"
            rows[0]["message"]["usage"]["inference_geo"] = "global"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            store.collect(THREAD, path)
            result = hive(root, "cost", "--task", THREAD)
            self.assertEqual(result.returncode, 0, result.stderr)
            cost = record(json.loads(result.stdout))
            self.assertIsNone(cost["priced_subset_usd"])
            self.assertEqual(cost["event_token_mismatches"], 1)
            self.assertIn("unknown_model_price", result.stdout)
            self.assertIsNone(cost["complete_estimate_usd"])

    def test_anonymous_gaps_only_affect_sessions_in_the_receipt_window(self) -> None:
        from datetime import UTC, datetime

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "state/telemetry.sqlite3")
            store.collect(THREAD, transcript(root))
            spool(root, captured())
            ingest(store, root / "state", time.monotonic() + 5)
            for name, at, expected in (
                ("old.json", "2020-01-01T00:00:00+00:00", 0),
                ("current.json", "2026-09-25T01:06:04+00:00", 1),
            ):
                path = spool(root, {}, name)
                when = datetime.fromisoformat(at).astimezone(UTC).timestamp()
                os.utime(path, (when, when))
                ingest(store, root / "state", time.monotonic() + 5)
                result = hive(root, "cost", "--task", THREAD)
                self.assertEqual(result.returncode, 0, result.stderr)
                cost = record(json.loads(result.stdout))
                self.assertEqual(cost["rejected_records"], expected)
                self.assertEqual(cost["complete_estimate_usd"] is None, bool(expected))
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM claude_event_gaps").fetchone(),
                    (2,),
                )
