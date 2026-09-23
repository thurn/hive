"""Observed usage, pricing evidence, coverage, and historical estimates end to end."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from cli_fixture import cli_fixture
from server_fixture import private_server
from test_source_selection import commit
from test_usage import TASK, counters, header, line, response

from hive.cost_report import report
from hive.errors import HiveError
from hive.identity import ModelId
from hive.identity import PricingTier as Tier
from hive.jsonvalue import parse, record, sequence, string
from hive.pricing import dollars, quote
from hive.usage import Tokens
from hive.usage_store import UsageStore


def context(model: str = "gpt-6-astra", turn: str = "turn-1") -> bytes:
    return line("turn_context", {"turn_id": turn, "model": model})


def observed(identity: str, turn: str, usage: object) -> bytes:
    return line(
        "token_usage_record",
        {
            "thread_id": TASK,
            "session_id": TASK,
            "turn_id": turn,
            "response_id": identity,
            "usage": usage,
        },
    )


class CostTests(unittest.TestCase):
    def test_cache_partitions_reasoning_and_full_request_context_prices(self) -> None:
        usage = Tokens(1000, 400, 200, 50, 20)
        value = quote(ModelId("gpt-6-astra"), Tier.STANDARD, usage)
        self.assertIsNotNone(value)
        if value is None:
            raise AssertionError("Known price was missing")
        self.assertEqual(dollars(value.amount), "0.009400000000")
        self.assertEqual(
            quote(ModelId("gpt-6-astra-preview"), Tier.STANDARD, usage), None
        )
        with self.assertRaises(HiveError):
            Tokens(100, 60, 60, 0, 0)
        below = quote(
            ModelId("gpt-6-astra"), Tier.STANDARD, Tokens(272000, 272000, 0, 2, 1)
        )
        above = quote(
            ModelId("gpt-6-astra"), Tier.STANDARD, Tokens(272001, 272000, 0, 2, 1)
        )
        if below is None or above is None:
            raise AssertionError("Known context rates missing")
        self.assertEqual(dollars(below.amount), "0.272100000000")
        self.assertEqual(dollars(above.amount), "0.544170000000")
        for tier, expected in (
            (Tier.FAST, "0.018800000000"),
            (Tier.FLEX, "0.004700000000"),
            (Tier.BATCH, "0.004700000000"),
        ):
            priced = quote(ModelId("gpt-6-astra"), tier, usage)
            self.assertIsNotNone(priced)
            if priced is not None:
                self.assertEqual(dollars(priced.amount), expected)

    def test_unknown_usage_models_and_conflicts_cannot_become_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "usage.sqlite3")
            self.assertIsNone(report(store, TASK, Tier.STANDARD)["priced_subset_usd"])
            path = root / "transcript.jsonl"
            path.write_bytes(header() + context() + response("zero", counters(0)))
            store.collect(TASK, path)
            zero = report(store, TASK, Tier.STANDARD)
            self.assertEqual(zero["observed_estimate_usd"], "0.000000000000")
            self.assertIsNone(zero["observed_service_tier"])
            with path.open("ab") as stream:
                stream.write(response("missing"))
                stream.write(context("future-model", "future"))
                stream.write(observed("future", "future", counters()))
                stream.write(observed("no-model", "without-context", counters()))
                stream.write(context("gpt-6-sol", "changed"))
                stream.write(observed("changed", "changed", counters()))
            store.collect(TASK, path)
            partial = report(store, TASK, Tier.STANDARD)
            self.assertEqual(partial["observed_responses"], 5)
            self.assertEqual(partial["priced_responses"], 2)
            self.assertEqual(partial["missing_usage"], 1)
            self.assertEqual(partial["missing_model_context"], 1)
            self.assertEqual(partial["unknown_model_price"], 1)
            self.assertEqual(partial["priced_subset_usd"], "0.000200000000")
            self.assertIsNone(partial["observed_estimate_usd"])
            with path.open("ab") as stream:
                stream.write(context("gpt-6-astra", "changed"))
            store.collect(TASK, path)
            conflict = report(store, TASK, Tier.STANDARD)
            self.assertEqual(conflict["conflicting_model_context"], 1)
            self.assertEqual(conflict["priced_responses"], 1)
            self.assertEqual(conflict["priced_subset_usd"], "0.000000000000")
            store.collect(TASK, path, from_start=True)
            again = report(store, TASK, Tier.STANDARD)
            self.assertEqual(again["observed_responses"], 5)
            self.assertEqual(again["conflicting_model_context"], 1)

    def test_fresh_cost_cli_preserves_rate_evidence_across_live_catalog_edits(
        self,
    ) -> None:
        with private_server() as (connection, _), cli_fixture(connection) as cli:
            path = connection.directory / "transcript.jsonl"
            path.write_bytes(header() + context() + response("first", counters()))
            collect = (
                "telemetry",
                "collect",
                "--task",
                TASK,
                "--transcript",
                str(path),
            )
            cli.call(*collect)
            # Simulate an earlier collector that retained usage without context.
            with sqlite3.connect(cli.state / "telemetry.sqlite3") as db:
                db.execute("DELETE FROM turn_models")
            args = ("cost", "--task", TASK)
            self.assertIsNone(cli.call(*args)["observed_estimate_usd"])
            cli.call(*collect, "--from-start")
            before = cli.call(*args)
            self.assertEqual(before["observed_estimate_usd"], "0.001000000000")
            configuration = record(
                parse(Path(cli.environment["HIVE_BOOTSTRAP_CONFIG"]).read_text())
            )
            repository = Path(string(configuration["repository"], "repository"))
            pricing = repository / "src/hive/pricing.py"
            pricing.write_text(
                pricing.read_text()
                .replace("Rates(10_000_000,", "Rates(20_000_000,")
                .replace('"2026-09-23",', '"2026-09-24",')
            )
            commit(repository, "test: change observed price schedule")
            self.assertEqual(cli.call(*args), before)
            with path.open("ab") as stream:
                stream.write(response("second", counters()))
            cli.call(*collect)
            after = cli.call(*args)
            self.assertEqual(after["observed_estimate_usd"], "0.003000000000")
            self.assertEqual(len(sequence(after["rate_groups"], "rate groups")), 2)
            self.assertEqual(after["unattributed_responses"], 2)
            self.assertEqual(
                cli.call(*args, "--tier", "fast")["observed_estimate_usd"],
                "0.008000000000",
            )
            # Neither provider outage nor collection gaps should masquerade as a
            # billing total. Retained estimates remain usable with source health.
            path.unlink()
            cli.call(*collect)
            metadata = connection.directory / ".beads/metadata.json"
            metadata.write_text("{}")
            unavailable = cli.call(*args)
            self.assertEqual(unavailable["observed_estimate_usd"], "0.003000000000")
            self.assertIsNotNone(unavailable["source_error"])
            self.assertIsNone(unavailable["remaining_bytes"])

    def test_large_totals_stay_exact_and_context_rolls_back_with_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "usage.sqlite3")
            path = root / "transcript.jsonl"
            store.report(TASK)
            path.write_bytes(header() + context() + response("first", counters()))
            with sqlite3.connect(store.path) as db:
                db.execute(
                    "CREATE TRIGGER fail_cursor BEFORE INSERT ON sources BEGIN SELECT RAISE(ABORT, 'interrupted'); END"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                store.collect(TASK, path)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM turn_models").fetchone(), (0,)
                )
                db.execute("DROP TRIGGER fail_cursor")
            amount = 2**62
            path.write_bytes(
                header()
                + context()
                + b"".join(response(str(i), counters(amount)) for i in range(1025))
            )
            store.collect(TASK, path)
            result = report(store, TASK, Tier.STANDARD)
            expected_picos = amount * 20_000_000 * 1025
            self.assertEqual(result["observed_estimate_usd"], dollars(expected_picos))
            self.assertEqual(result["observed_responses"], 1025)
            self.assertEqual(report(store, TASK, Tier.STANDARD), result)
