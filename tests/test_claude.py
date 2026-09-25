"""Claude collection through public commands and bounded sweeps."""

import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from test_contention import hive
from test_usage import line

from hive.collection import sweep
from hive.collection_registry import CollectionRegistry
from hive.identity import Host, SourceCommit, ThreadId
from hive.jsonvalue import integer, record
from hive.launch_context import LaunchContext
from hive.thread_links import ThreadLink
from hive.usage_store import UsageStore

THREAD = ThreadId("a521cf51-c055-4715-832b-fc19921ee482")
OTHER = ThreadId("a521cf51-c055-4715-832b-fc19921ee483")


def assistant(
    identity: str,
    output: int = 10,
    *,
    agent: str | None = None,
    thread: str = THREAD,
    complete: bool = True,
    usage_changes: dict[str, object] | None = None,
) -> bytes:
    usage: dict[str, object] = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 100,
        "output_tokens": output,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 10,
            "ephemeral_1h_input_tokens": 20,
        },
        "speed": "standard",
        "service_tier": "standard",
        "inference_geo": "not_available",
    }
    usage.update(usage_changes or {})
    value: dict[str, object] = {
        "type": "assistant",
        "timestamp": "2026-09-24T00:00:00Z",
        "sessionId": thread,
        "requestId": identity,
        "message": {
            "id": "msg_" + identity,
            "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "answer"}],
            "usage": usage,
            "stop_reason": "end_turn" if complete else None,
        },
    }
    if agent is not None:
        value.update(agentId=agent, isSidechain=True)
    return (json.dumps(value) + "\n").encode()


class ClaudeTests(unittest.TestCase):
    def test_version_two_upgrade_collects_and_prices_claude(self) -> None:
        from test_contention import ROOT

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            with sqlite3.connect(state / "telemetry.sqlite3") as db:
                db.executescript((ROOT / "tests/fixtures/telemetry-v2.sql").read_text())
                db.execute("PRAGMA user_version=2")
            path = root / f"{THREAD}.jsonl"
            path.write_bytes(assistant("req_upgrade"))
            result = hive(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result = hive(root, "cost", "--task", THREAD)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                record(json.loads(result.stdout))["priced_subset_usd"], "0.000438000000"
            )
            with sqlite3.connect(state / "telemetry.sqlite3") as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (15,))

    def test_host_totals_replay_old_cursors_and_reject_late_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            total = {
                "type": "cost-state",
                "startTime": "2026-09-23T23:00:00Z",
                "timestamp": "2026-09-24T00:01:00Z",
                "totalCostUSD": 1,
            }
            path.write_bytes(
                assistant("req_partial", 1, complete=False)
                + (json.dumps(total) + "\n").encode()
            )
            collect = (
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(path),
            )
            self.assertEqual(hive(root, *collect).returncode, 0)
            # Construct the populated delivered v2 schema: it had consumed
            # these records but could not retain cost-state or latest time.
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                db.execute("DROP VIEW request_detail")
                db.execute("DROP TABLE claude_cost_states")
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
                db.execute("ALTER TABLE responses DROP COLUMN last_observed")
                db.execute("PRAGMA user_version=2")
            collected = hive(root, *collect)
            self.assertEqual(collected.returncode, 0, collected.stderr)
            self.assertEqual(
                record(json.loads(collected.stdout))["read_bytes"], path.stat().st_size
            )
            before = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertIsNone(before["host_reported_reason"])
            final = record(json.loads(assistant("req_partial", 10)))
            final["timestamp"] = "2026-09-24T00:02:00Z"
            with path.open("ab") as stream:
                stream.write((json.dumps(final) + "\n").encode())
            self.assertEqual(hive(root, *collect).returncode, 0)
            after = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(
                after["host_reported_reason"], "cost_state_before_last_request"
            )
            self.assertEqual(after["priced_subset_usd"], "0.000438000000")
            self.assertEqual(after["parse_gaps"], 0)
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                self.assertEqual(
                    db.execute(
                        "SELECT observed,last_observed FROM responses"
                    ).fetchone(),
                    ("2026-09-24T00:00:00+00:00", "2026-09-24T00:02:00+00:00"),
                )

    def test_host_total_comparison_and_resume_guards(self) -> None:
        def state(**changes: object) -> bytes:
            value: dict[str, object] = {
                "type": "cost-state",
                "startTime": "2026-09-23T23:59:00Z",
                "timestamp": "2026-09-24T00:01:00Z",
                "totalCostUSD": 0.001,
                "hasUnknownModelCost": False,
            }
            value.update(changes)
            return (json.dumps(value) + "\n").encode()

        cases = (
            ("single", state(), None),
            (
                "incremental",
                state(totalCostUSD=0.0001, timestamp="2026-09-24T00:00:10Z") + state(),
                None,
            ),
            ("epoch", state(startTime=1790207940000), None),
            ("offset", state(timestamp="2026-09-23T17:01:00-07:00"), None),
            ("unknown", state(hasUnknownModelCost=True), None),
            ("missing", b"", "no_cost_state"),
            (
                "resumed",
                state() + state(startTime="2026-09-24T00:00:30Z"),
                "multiple_process_segments",
            ),
            (
                "stale",
                state(timestamp="2026-09-24T00:00:00Z"),
                "cost_state_before_last_request",
            ),
            (
                "late_start",
                state(startTime="2026-09-24T00:00:30Z"),
                "process_starts_after_first_request",
            ),
            ("negative", state(totalCostUSD=0.000001), "negative_difference"),
            ("invalid", state(totalCostUSD=True), "invalid_cost_state"),
            ("no_timestamp", state(timestamp=None), "invalid_cost_state"),
            ("nonfinite", state(totalCostUSD=float("nan")), "invalid_cost_state"),
        )
        for name, states, reason in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / f"{THREAD}.jsonl"
                path.write_bytes(assistant("req_main") + states)
                command = (
                    "telemetry",
                    "collect",
                    "--task",
                    THREAD,
                    "--transcript",
                    str(path),
                )
                collected = hive(root, *command)
                self.assertEqual(collected.returncode, 0, collected.stderr)
                result = hive(root, "cost", "--task", THREAD)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = record(json.loads(result.stdout))
                self.assertEqual(report["host_reported_reason"], reason)
                if reason is not None:
                    self.assertIsNone(report["host_reported"])
                    self.assertIsNone(report["unrecorded_usd_lower_bound"])
                else:
                    self.assertEqual(record(report["host_reported"])["usd"], "0.001")
                    self.assertEqual(
                        record(report["host_reported"])["has_unknown_model_cost"],
                        name == "unknown",
                    )
                    self.assertEqual(report["priced_subset_usd"], "0.000438000000")
                    self.assertEqual(
                        report["unrecorded_usd_lower_bound"], "0.000562000000"
                    )
                    # A complete reread never adds cumulative process totals.
                    self.assertEqual(hive(root, *command, "--from-start").returncode, 0)
                    reread = record(
                        json.loads(hive(root, "cost", "--task", THREAD).stdout)
                    )
                    self.assertEqual(reread["host_reported"], report["host_reported"])
                    self.assertEqual(reread["host_process_segments"], 1)

    def test_host_total_must_cover_subagents_and_ignores_their_cost_states(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / f"{THREAD}.jsonl"
            total = {
                "type": "cost-state",
                "startTime": "2026-09-23T23:00:00Z",
                "timestamp": "2026-09-24T00:01:00Z",
                "totalCostUSD": 1,
            }
            main.write_bytes(
                assistant("req_main") + (json.dumps(total) + "\n").encode()
            )
            child = root / THREAD / "subagents/agent-child.jsonl"
            child.parent.mkdir(parents=True)
            request = record(json.loads(assistant("req_child", agent="child")))
            request["timestamp"] = "2026-09-24T00:02:00Z"
            total.update(
                sessionId=THREAD,
                agentId="child",
                isSidechain=True,
                timestamp="2026-09-24T00:03:00Z",
                startTime="2026-09-23T22:00:00Z",
            )
            child.write_bytes(
                (json.dumps(request) + "\n" + json.dumps(total) + "\n").encode()
            )
            for path in (main, child):
                self.assertEqual(
                    hive(
                        root,
                        "telemetry",
                        "collect",
                        "--task",
                        THREAD,
                        "--transcript",
                        str(path),
                    ).returncode,
                    0,
                )
            result = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(
                result["host_reported_reason"], "cost_state_before_last_request"
            )
            self.assertEqual(result["host_process_segments"], 1)
            self.assertIsNone(result["host_reported"])

    def test_partial_modifier_enrichment_and_migration_recover_native_stream(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            early = assistant(
                "enriched",
                3,
                complete=False,
                usage_changes={
                    "speed": None,
                    "service_tier": None,
                    "inference_geo": None,
                },
            )
            final = assistant(
                "enriched",
                283,
                usage_changes={"output_tokens_details": {"thinking_tokens": 85}},
            )
            path.write_bytes(early)
            store = UsageStore(root / "state/telemetry.sqlite3")
            store.collect(THREAD, path)
            before = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(before["unpriced_responses"], 1)
            with path.open("ab") as stream:
                stream.write(final)
            store.collect(THREAD, path)
            after = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(after["priced_responses"], 1)
            self.assertEqual(after["parse_gaps"], 0)
            self.assertEqual(after["possibly_partial_output"], 0)
            self.assertEqual(after["priced_subset_usd"], "0.005898000000")
            # Replay the early nulls without losing the final known modifiers.
            store.collect(THREAD, path, from_start=True)
            reread = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(reread["priced_subset_usd"], after["priced_subset_usd"])
            self.assertEqual(reread["parse_gaps"], 0)
            # Recreate the legacy rejected final record, then recover by upgrade.
            with sqlite3.connect(store.path) as db:
                db.execute(
                    "UPDATE responses SET modifiers=json_set(modifiers,'$.speed',NULL),modifier_key='unknown/standard/not_available',output=3,reasoning=0,complete=0,usage=json_set(usage,'$.output_tokens',3,'$.reasoning_output_tokens',0)"
                )
                db.execute("DELETE FROM response_estimates")
                db.execute(
                    "INSERT INTO gaps(task,file,device,inode,position,detail) SELECT task,file,device,inode,?,'Conflicting Claude response identity or usage' FROM sources",
                    (len(early),),
                )
                db.execute("PRAGMA user_version=11")
            store.collect(THREAD, path)
            upgraded = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(upgraded["priced_subset_usd"], after["priced_subset_usd"])
            self.assertEqual(upgraded["parse_gaps"], 0)
            with path.open("ab") as stream:
                stream.write(
                    assistant("enriched", 284, usage_changes={"speed": "fast"})
                )
            store.collect(THREAD, path)
            conflict = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
            self.assertEqual(conflict["priced_subset_usd"], after["priced_subset_usd"])
            self.assertEqual(conflict["parse_gaps"], 1)

    def test_modifier_migration_preserves_unrecoverable_conflict_evidence(self) -> None:
        for mutation in ("missing", "replaced", "truncated"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                path = root / f"{THREAD}.jsonl"
                valid = assistant("conflict", 10)
                path.write_bytes(
                    valid + assistant("conflict", 11, usage_changes={"speed": "fast"})
                )
                store = UsageStore(root / "state/telemetry.sqlite3")
                store.collect(THREAD, path)
                before = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
                self.assertEqual(before["parse_gaps"], 1)
                with sqlite3.connect(store.path) as db:
                    db.execute("PRAGMA user_version=11")
                if mutation == "missing":
                    path.unlink()
                elif mutation == "replaced":
                    replacement = root / "replacement"
                    replacement.write_bytes(valid)
                    replacement.replace(path)
                else:
                    path.write_bytes(valid)
                store.collect(THREAD, path)
                after = record(json.loads(hive(root, "cost", "--task", THREAD).stdout))
                self.assertGreaterEqual(int(str(after["parse_gaps"])), 1)
                self.assertIn(
                    "Conflicting Claude response identity or usage",
                    str(after["recent_gaps"]),
                )
                if mutation != "missing":
                    self.assertIn(
                        "Transcript replaced or truncated", str(after["recent_gaps"])
                    )

    def test_commands_update_partial_requests_and_keep_each_file_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            path.write_bytes(
                b'{"type":"queue-operation"}\n'
                + assistant("req_main", 1, complete=False)
            )
            first = hive(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(path),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIsNone(record(json.loads(first.stdout))["error"])
            with path.open("ab") as stream:
                stream.write(assistant("req_main", 10))
            again = hive(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(path),
            )
            self.assertEqual(again.returncode, 0, again.stderr)
            child = root / THREAD / "subagents/agent-child.jsonl"
            child.parent.mkdir(parents=True)
            child.write_bytes(
                assistant("req_child", 5, agent="child")
                + assistant("req_main", 10, agent="child")
            )
            collected = hive(
                root,
                "telemetry",
                "collect",
                "--task",
                THREAD,
                "--transcript",
                str(child),
            )
            self.assertEqual(collected.returncode, 0, collected.stderr)
            report = hive(root, "telemetry", "usage", "--task", THREAD)
            self.assertEqual(report.returncode, 0, report.stderr)
            result = record(json.loads(report.stdout))
            self.assertEqual(result["host"], "claude")
            self.assertEqual(result["observed_responses"], 2)
            self.assertEqual(result["parse_gaps"], 0)
            self.assertEqual(
                record(result["known_tokens"]),
                {
                    "input_tokens": 264,
                    "cached_input_tokens": 200,
                    "cache_write_input_tokens": 20,
                    "cache_write_1h_input_tokens": 40,
                    "output_tokens": 15,
                    "reasoning_output_tokens": 0,
                },
            )
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM sources").fetchone(), (2,)
                )
                self.assertEqual(
                    db.execute(
                        "SELECT complete FROM responses WHERE response='req_main'"
                    ).fetchone(),
                    (1,),
                )
                self.assertEqual(
                    db.execute(
                        "SELECT agent FROM responses WHERE response='req_main'"
                    ).fetchone(),
                    (None,),
                )
            self.assertEqual(
                record(
                    json.loads(
                        hive(
                            root,
                            "telemetry",
                            "collect",
                            "--task",
                            THREAD,
                            "--transcript",
                            str(path),
                        ).stdout
                    )
                )["read_bytes"],
                0,
            )

    def test_invalid_counters_and_iterations_do_not_hide_later_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            old = record(json.loads(assistant("req_old")))
            old_usage = record(record(old["message"])["usage"])
            old_usage.pop("cache_creation")
            record_message = record(old["message"])
            record_message["usage"] = old_usage
            old["message"] = record_message
            synthetic = record(
                json.loads(
                    assistant(
                        "synthetic",
                        0,
                        usage_changes={
                            "input_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    )
                )
            )
            message = record(synthetic["message"])
            message["model"] = "<synthetic>"
            synthetic["message"] = message
            path.write_bytes(
                assistant("req_bool", usage_changes={"input_tokens": True})
                + assistant("req_negative", usage_changes={"output_tokens": -1})
                + assistant(
                    "req_large", usage_changes={"cache_read_input_tokens": 2**63}
                )
                + assistant(
                    "req_bad_ttl", usage_changes={"cache_creation_input_tokens": 31}
                )
                + assistant(
                    "req_iteration",
                    usage_changes={"iterations": [{"type": "fallback"}]},
                )
                + (json.dumps(old) + "\n" + json.dumps(synthetic) + "\n").encode()
                + assistant("req_valid")
            )
            store = UsageStore(root / "state/telemetry.sqlite3")
            store.collect(THREAD, path)
            report = store.report(THREAD)
            self.assertEqual(report["observed_responses"], 3)
            self.assertEqual(report["parse_gaps"], 4)
            with sqlite3.connect(store.path) as db:
                flags = {
                    r[0]: json.loads(r[1])
                    for r in db.execute("SELECT response,flags FROM responses")
                }
            self.assertIn("ttl_assumed", flags["req_old"])
            self.assertIn("unsupported_iteration", flags["req_iteration"])

    def test_identity_pending_mismatch_and_symlinks_do_not_advance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            store = UsageStore(root / "usage.sqlite3")
            path.write_bytes(b'{"type":"queue-operation"}\n')
            pending = store.collect(THREAD, path)
            self.assertEqual(pending["position"], 0)
            self.assertIsNotNone(pending["error"])
            with path.open("ab") as stream:
                stream.write(assistant("req_wrong", thread=OTHER))
            self.assertEqual(store.collect(THREAD, path)["position"], 0)
            self.assertEqual(store.report(THREAD)["observed_responses"], 0)
            path.write_bytes(assistant("req_ok"))
            self.assertIsNone(store.collect(THREAD, path)["error"])
            alias = root / "alias" / f"{THREAD}.jsonl"
            alias.parent.mkdir()
            alias.symlink_to(path)
            self.assertIn("symlink", str(store.collect(THREAD, alias)["error"]))

    def test_sweep_finds_uuid4_codex_late_subagents_and_both_host_ambiguity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "projects"
            project = projects / "slug"
            project.mkdir(parents=True)
            main = project / f"{THREAD}.jsonl"
            main.write_bytes(assistant("req_main"))
            codex = root / "rollout.jsonl"
            codex.write_bytes(line("session_meta", {"id": OTHER}))
            index = root / "native.sqlite3"
            with sqlite3.connect(index) as db:
                db.execute(
                    "CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)"
                )
                db.execute("INSERT INTO threads VALUES (?,?)", (OTHER, str(codex)))
            context = LaunchContext(
                SourceCommit("fixture"),
                root,
                root / "state",
                root / "missing-beads",
                0,
                root,
                projects,
            )
            store = UsageStore(context.state / "telemetry.sqlite3")
            registry = CollectionRegistry(store)
            registry.refresh(
                (
                    ThreadLink(THREAD, "hv-test", "creator", False),
                    ThreadLink(OTHER, "hv-test", "executor", False),
                ),
                (),
                None,
            )
            result = sweep(context, index, 32)
            self.assertEqual(result["attempted"], 2)
            self.assertEqual(registry.cached_host(THREAD), Host.CLAUDE)
            self.assertEqual(registry.cached_host(OTHER), Host.CODEX)
            child = project / THREAD / "subagents/agent-late.jsonl"
            child.parent.mkdir(parents=True)
            child.write_bytes(assistant("req_late", agent="late"))
            child.with_suffix(".meta.json").write_text(
                json.dumps(
                    {"toolUseId": "tool_agent", "agentType": "test", "spawnDepth": 1}
                )
            )
            sweep(context, index, 32)
            self.assertEqual(store.report(THREAD)["observed_responses"], 2)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT agent,spawn_depth FROM claude_agents"
                    ).fetchone(),
                    ("late", 1),
                )
            with sqlite3.connect(index) as db:
                db.execute("INSERT INTO threads VALUES (?,?)", (THREAD, str(codex)))
            with main.open("ab") as stream:
                stream.write(assistant("req_ambiguous"))
            sweep(context, index, 32)
            self.assertIsNone(registry.cached_host(THREAD))
            self.assertEqual(store.report(THREAD)["observed_responses"], 2)

    def test_index_outage_requires_cached_host_and_duplicates_are_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "projects"
            project = projects / "slug"
            project.mkdir(parents=True)
            main = project / f"{THREAD}.jsonl"
            main.write_bytes(assistant("req_main"))
            context = LaunchContext(
                SourceCommit("fixture"),
                root,
                root / "state",
                root / "missing-beads",
                0,
                root,
                projects,
            )
            store = UsageStore(context.state / "telemetry.sqlite3")
            registry = CollectionRegistry(store)
            registry.refresh(
                (ThreadLink(THREAD, "hv-test", "creator", False),), (), None
            )
            sweep(context, root / "missing-index", 32)
            self.assertEqual(store.report(THREAD)["observed_responses"], 0)
            registry.attempted(THREAD, None, main, Host.CLAUDE)
            sweep(context, root / "missing-index", 32)
            self.assertEqual(store.report(THREAD)["observed_responses"], 1)
            second = projects / "other" / main.name
            second.parent.mkdir()
            second.write_bytes(main.read_bytes())
            sweep(context, root / "missing-index", 32)
            self.assertIsNone(registry.cached_host(THREAD))
            self.assertIn("multiple", str(registry.status()["recent_failures"]))

    def test_known_partial_replays_are_idempotent_but_new_decreases_are_gaps(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / f"{THREAD}.jsonl"
            main.write_bytes(
                assistant("req_stream", 1, complete=False) + assistant("req_stream", 10)
            )
            store = UsageStore(root / "usage.sqlite3")
            store.collect(THREAD, main)
            store.collect(THREAD, main, from_start=True)
            child = root / THREAD / "subagents/agent-copy.jsonl"
            child.parent.mkdir(parents=True)
            child.write_bytes(assistant("req_stream", 1, complete=False, agent="copy"))
            store.collect(THREAD, child)
            self.assertEqual(store.report(THREAD)["parse_gaps"], 0)
            with main.open("ab") as stream:
                stream.write(assistant("req_stream", 3, complete=False))
            store.collect(THREAD, main)
            self.assertEqual(store.report(THREAD)["parse_gaps"], 1)
            self.assertEqual(
                record(store.report(THREAD)["known_tokens"])["output_tokens"], 10
            )

    def test_mixed_file_failures_remain_visible_and_reads_stay_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = root / "projects"
            main = projects / "slug" / f"{THREAD}.jsonl"
            main.parent.mkdir(parents=True)
            main.write_bytes(assistant("req_wrong", thread=OTHER))
            child = main.parent / THREAD / "subagents/agent-valid.jsonl"
            child.parent.mkdir(parents=True)
            child.write_bytes(
                b"".join(assistant(f"req_{i}", agent="valid") for i in range(3500))
            )
            index = root / "index.sqlite3"
            with sqlite3.connect(index) as db:
                db.execute(
                    "CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)"
                )
            context = LaunchContext(
                SourceCommit("test"),
                root,
                root / "state",
                root / "missing",
                0,
                root,
                projects,
            )
            store = UsageStore(context.state / "telemetry.sqlite3")
            registry = CollectionRegistry(store)
            registry.refresh(
                (ThreadLink(THREAD, "hv-test", "creator", False),), (), None
            )
            batch = sweep(context, index, 32)
            self.assertIn("another task", str(batch["results"]))
            self.assertIn("another task", str(registry.status()["recent_failures"]))
            self.assertGreater(
                integer(store.report(THREAD)["observed_responses"], "responses"), 0
            )
            self.assertLess(
                integer(store.report(THREAD)["observed_responses"], "responses"), 3500
            )
            for _ in range(5):
                result = store.collect(THREAD, child)
                self.assertLessEqual(integer(result["read_bytes"], "bytes"), 1048576)
                if result["remaining_bytes"] == 0:
                    break
            self.assertEqual(store.report(THREAD)["observed_responses"], 3500)

    def test_v1_upgrade_preserves_codex_cursor_and_adds_claude_collection(self) -> None:
        from test_usage import ROOT

        from hive.errors import HiveError

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = UsageStore(root / "usage.sqlite3")
            with sqlite3.connect(store.path) as db:
                db.executescript((ROOT / "tests/fixtures/telemetry-v1.sql").read_text())
                db.execute(
                    "INSERT INTO collection_tasks(task,host) VALUES (?,?)",
                    (OTHER, "codex"),
                )
            with self.assertRaises(HiveError):
                store.report(THREAD)
            main = root / f"{THREAD}.jsonl"
            main.write_bytes(assistant("req_v2"))
            store.collect(THREAD, main)
            self.assertEqual(store.report(THREAD)["observed_responses"], 1)
            self.assertEqual(CollectionRegistry(store).cached_host(OTHER), Host.CODEX)


class ClaudePricingTests(unittest.TestCase):
    def test_each_model_and_modifier_is_exact_and_server_fees_are_separate(
        self,
    ) -> None:
        from hive.cost_report import report
        from hive.pricing import dollars

        # Hand-calculated for uncached=2, cw5=10, cw1h=20, cached=100, output=10.
        expected = (
            ("claude-fable-5-1", 1_070_000_000),
            ("claude-fable-5", 1_145_000_000),
            ("claude-opus-5-5", 438_000_000),
            ("claude-opus-5", 572_500_000),
            ("claude-opus-4-8", 572_500_000),
            ("claude-opus-4-7", 572_500_000),
            ("claude-opus-4-6", 572_500_000),
            ("claude-sonnet-5", 229_000_000),
            ("claude-sonnet-4-6", 343_500_000),
            ("claude-haiku-4-5", 114_500_000),
        )
        for model, base in expected:
            for speed in ("standard", "fast"):
                for geo in ("not_available", "global", "us"):
                    with (
                        self.subTest(model=model, speed=speed, geo=geo),
                        tempfile.TemporaryDirectory() as temporary,
                    ):
                        root = Path(temporary)
                        path = root / f"{THREAD}.jsonl"
                        raw = record(
                            json.loads(
                                assistant(
                                    "req_price",
                                    usage_changes={
                                        "speed": speed,
                                        "inference_geo": geo,
                                        "server_tool_use": {"web_search_requests": 2},
                                        "output_tokens_details": {"thinking_tokens": 6},
                                    },
                                )
                            )
                        )
                        message = record(raw["message"])
                        message["model"] = model
                        raw["message"] = message
                        path.write_text(json.dumps(raw) + "\n")
                        store = UsageStore(root / "usage.sqlite3")
                        store.collect(THREAD, path)
                        result = report(store, THREAD)
                        supported = (
                            speed == "standard"
                            or model
                            in {"claude-opus-5-5", "claude-opus-5", "claude-opus-4-8"}
                        ) and not (geo == "us" and model == "claude-haiku-4-5")
                        if supported:
                            amount = (
                                base
                                * (2 if speed == "fast" else 1)
                                * (11 if geo == "us" else 10)
                                // 10
                                + 20_000_000_000
                            )
                            self.assertEqual(
                                result["observed_estimate_usd"], dollars(amount)
                            )
                            self.assertEqual(
                                result["server_tool_fees_usd"], "0.020000000000"
                            )
                            self.assertEqual(report(store, THREAD), result)
                        else:
                            self.assertEqual(result["unknown_modifier"], 1)
                            self.assertIsNone(result["observed_estimate_usd"])
                        self.assertIsNone(result["pricing_tier_assumption"])
                        self.assertIn("lower bound", str(result["coverage"]))

    def test_unknowns_partial_streams_and_explicit_tiers_are_visible(self) -> None:
        from hive.cost_report import report

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            lines = [
                assistant("req_partial", complete=False),
                assistant("req_tier", usage_changes={"service_tier": "priority"}),
                assistant("req_speed", usage_changes={"speed": "future"}),
                assistant("req_geo", usage_changes={"inference_geo": "eu"}),
                assistant(
                    "req_iteration",
                    usage_changes={"iterations": [{"type": "fallback"}]},
                ),
            ]
            for model in (
                "claude-opus-5-5-20260101",
                "claude-opus-5-5[1m]",
                "bedrock/claude-opus-5-5",
            ):
                raw = record(json.loads(assistant(model)))
                message = record(raw["message"])
                message["model"] = model
                raw["message"] = message
                lines.append((json.dumps(raw) + "\n").encode())
            path.write_bytes(b"".join(lines))
            store = UsageStore(root / "state/telemetry.sqlite3")
            store.collect(THREAD, path)
            result = report(store, THREAD)
            self.assertEqual(result["priced_responses"], 1)
            self.assertEqual(result["possibly_partial_output"], 1)
            self.assertEqual(result["unknown_service_tier"], 1)
            self.assertEqual(result["unknown_modifier"], 2)
            self.assertEqual(result["unsupported_iteration"], 1)
            self.assertEqual(result["unknown_model_price"], 3)
            self.assertEqual(result["priced_subset_usd"], "0.000438000000")
            self.assertIsNone(result["observed_estimate_usd"])
            normal = hive(root, "cost", "--task", THREAD)
            self.assertEqual(normal.returncode, 0, normal.stderr)
            self.assertEqual(record(json.loads(normal.stdout))["host"], "claude")
            explicit = hive(root, "cost", "--task", THREAD, "--tier", "standard")
            self.assertEqual(explicit.returncode, 1)
            self.assertEqual(
                record(json.loads(explicit.stderr))["code"], "InvalidInput"
            )

    def test_retained_rate_evidence_survives_source_edit_and_later_output(self) -> None:
        import os
        import shutil
        import subprocess
        import sys

        from test_usage import ROOT

        from hive.cost_report import report
        from hive.pricing import Quote

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            path.write_bytes(assistant("req_retained", 1, complete=False))
            store = UsageStore(root / "usage.sqlite3")
            store.collect(THREAD, path)
            self.assertEqual(
                report(store, THREAD)["observed_estimate_usd"], "0.000258000000"
            )
            with sqlite3.connect(store.path) as db:
                old = Quote.read(
                    json.loads(
                        db.execute("SELECT quote FROM response_estimates").fetchone()[0]
                    )
                )
            self.assertEqual(Quote.read(old.value()), old)
            changed = root / "changed"
            shutil.copytree(ROOT / "src/hive", changed / "hive")
            pricing = changed / "hive/pricing.py"
            source = pricing.read_text()
            self.assertIn(
                "Rates(4_000_000, 200_000, 5_000_000, 20_000_000, 8_000_000)", source
            )
            pricing.write_text(
                source.replace(
                    "Rates(4_000_000, 200_000, 5_000_000, 20_000_000, 8_000_000)",
                    "Rates(40_000_000, 2_000_000, 50_000_000, 200_000_000, 80_000_000)",
                )
            )
            with path.open("ab") as stream:
                stream.write(assistant("req_retained", 10) + assistant("req_new", 10))
            script = "import json,sys; from pathlib import Path; from hive.usage_store import UsageStore; from hive.identity import ThreadId; from hive.cost_report import report; s=UsageStore(Path(sys.argv[1])); t=ThreadId(sys.argv[2]); s.collect(t,Path(sys.argv[3])); print(json.dumps(report(s,t)))"
            completed = subprocess.run(
                [sys.executable, "-c", script, str(store.path), THREAD, str(path)],
                env={**os.environ, "PYTHONPATH": str(changed)},
                cwd=changed,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                record(json.loads(completed.stdout))["observed_estimate_usd"],
                "0.004818000000",
            )
            with sqlite3.connect(store.path) as db:
                kept = Quote.read(
                    json.loads(
                        db.execute(
                            "SELECT quote FROM response_estimates WHERE response='req_retained'"
                        ).fetchone()[0]
                    )
                )
            with sqlite3.connect(store.path) as db:
                allocations = db.execute(
                    "SELECT response,usd FROM tool_allocation"
                ).fetchall()
            for identity, expected in (
                ("req_retained", "0.000438000000"),
                ("req_new", "0.004380000000"),
            ):
                self.assertEqual(
                    sum(
                        (
                            Decimal(amount)
                            for response, amount in allocations
                            if response == identity
                        ),
                        Decimal(0),
                    ),
                    Decimal(expected),
                )
            self.assertEqual(kept.rates, old.rates)
            self.assertEqual(kept.amount, 438_000_000)

    def test_late_quote_retention_uses_latest_committed_output_and_old_rates(
        self,
    ) -> None:
        from hive.claude_usage import Modifiers
        from hive.cost_report import retain
        from hive.identity import ModelId
        from hive.pricing import Quote, claude_quote
        from hive.usage import Tokens

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / f"{THREAD}.jsonl"
            store = UsageStore(root / "usage.sqlite3")
            path.write_bytes(assistant("req_race", 1, complete=False))
            store.collect(THREAD, path)
            pending = claude_quote(
                ModelId("claude-opus-5-5"),
                Modifiers("standard", "standard", "not_available", 0),
                Tokens(132, 100, 10, 1, 0, 20),
            )
            if pending is None:
                self.fail("Known quote missing")
            with path.open("ab") as stream:
                stream.write(assistant("req_race", 10))
            store.collect(THREAD, path)
            self.assertEqual(
                retain(
                    store,
                    [("req_race", pending.modifier_key, json.dumps(pending.value()))],
                ),
                0,
            )
            with sqlite3.connect(store.path) as db:
                value = Quote.read(
                    json.loads(
                        db.execute("SELECT quote FROM response_estimates").fetchone()[0]
                    )
                )
            self.assertEqual(value.amount, 438_000_000)
