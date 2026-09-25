"""Public breakdowns partition exact prices while preserving missing evidence."""

import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from test_claude import THREAD, assistant
from test_contention import hive
from test_events import captured, spool

from hive.jsonvalue import record, sequence


def request(
    identity: str,
    *,
    agent: str | None = None,
    skill: str | None = None,
    tool: str | None = None,
    unknown: bool = False,
) -> bytes:
    raw = record(json.loads(assistant(identity, agent=agent)))
    raw["attributionSkill"] = skill
    message = record(raw["message"])
    if tool:
        message["content"] = [{"type": "tool_use", "id": tool, "name": "Agent"}]
    if unknown:
        message["model"] = "future-model"
    raw["message"] = message
    return (json.dumps(raw) + "\n").encode()


def collect(root: Path, path: Path) -> None:
    result = hive(
        root, "telemetry", "collect", "--task", THREAD, "--transcript", str(path)
    )
    if result.returncode:
        raise AssertionError(result.stderr)


def cost(root: Path, *args: str) -> dict[str, object]:
    result = hive(root, "cost", "--task", THREAD, *args)
    if result.returncode:
        raise AssertionError(result.stderr)
    return record(json.loads(result.stdout))


class BreakdownTests(unittest.TestCase):
    def test_nested_and_inline_agents_skills_and_event_only_stay_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / f"{THREAD}.jsonl"
            main.write_bytes(
                request("main", skill="executor", tool="spawn-child")
                + request("inline", agent="inline", skill="executor")
                + request("unknown", agent="missing", unknown=True)
            )
            folder = root / THREAD / "subagents"
            folder.mkdir(parents=True)
            child = folder / "agent-child.jsonl"
            child.write_bytes(request("child", agent="child", tool="spawn-grand"))
            child.with_suffix(".meta.json").write_text(
                json.dumps(
                    {
                        "toolUseId": "spawn-child",
                        "agentType": "reviewer",
                        "description": "Review",
                    }
                )
            )
            grand = folder / "agent-grand.jsonl"
            grand.write_bytes(request("grand", agent="grand", skill="warden"))
            grand.with_suffix(".meta.json").write_text(
                json.dumps(
                    {
                        "toolUseId": "spawn-grand",
                        "agentType": "research",
                        "spawnDepth": 2,
                    }
                )
            )
            # Child-first collection and repeated metadata reads converge.
            for path in (grand, child, main, child):
                collect(root, path)
            spool(root, captured())
            swept = hive(
                root, "telemetry", "sweep", "--native-index", str(root / "missing")
            )
            self.assertEqual(swept.returncode, 0, swept.stderr)
            report = cost(root)
            agents = [record(item) for item in sequence(report["by_agent"], "agents")]
            indexed = {
                item.get("agent"): item
                for item in agents
                if item["label"] != "event_only"
            }
            self.assertEqual(indexed["child"]["parent_agent"], None)
            self.assertEqual(indexed["child"]["agent_type"], "reviewer")
            self.assertIsNone(indexed["child"]["spawn_depth"])
            self.assertEqual(indexed["grand"]["parent_agent"], "child")
            self.assertEqual(indexed["grand"]["spawn_depth"], 2)
            self.assertEqual(indexed["inline"]["parent_agent"], "unknown")
            self.assertEqual(indexed["missing"]["unpriced_responses"], 1)
            self.assertIsNone(indexed["missing"]["usd"])
            for name in ("by_agent", "by_skill"):
                rows = [record(item) for item in sequence(report[name], name)]
                total = sum(
                    Decimal(str(item["usd"]))
                    for item in rows
                    if item.get("label") != "event_only" and item["usd"] is not None
                )
                self.assertEqual(total, Decimal(str(report["priced_subset_usd"])))
                side = next(item for item in rows if item.get("label") == "event_only")
                self.assertEqual(side["usd"], report["event_only_usd"])
                self.assertEqual(side["responses"], 1)
                self.assertEqual(
                    record(sequence(side["by_query_source"], "sources")[0])[
                        "query_source"
                    ],
                    "sdk",
                )
            skills = [record(item) for item in sequence(report["by_skill"], "skills")]
            self.assertEqual(
                next(item for item in skills if item.get("skill") == "executor")[
                    "responses"
                ],
                2,
            )
            details = [
                record(item)
                for item in sequence(cost(root, "--requests")["requests"], "requests")
            ]
            self.assertEqual(
                next(item for item in details if item["response"] == "grand")[
                    "parent_agent"
                ],
                "child",
            )
            self.assertIsNone(
                next(item for item in details if item["response"] == "child")[
                    "parent_agent"
                ]
            )
            self.assertEqual(
                next(item for item in details if item["response"] == "inline")[
                    "parent_agent"
                ],
                "unknown",
            )
            # Removing metadata loses the parent claim without losing cost.
            child.with_suffix(".meta.json").unlink()
            collect(root, child)
            rows = [record(item) for item in sequence(cost(root)["by_agent"], "agents")]
            self.assertEqual(
                next(item for item in rows if item.get("agent") == "child")[
                    "parent_agent"
                ],
                "unknown",
            )

    def test_ambiguous_spawn_identity_is_unknown_and_v5_replays_tool_index(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / f"{THREAD}.jsonl"
            main.write_bytes(request("main", tool="spawn"))
            child = root / THREAD / "subagents/agent-child.jsonl"
            child.parent.mkdir(parents=True)
            child.write_bytes(request("child", agent="child"))
            child.with_suffix(".meta.json").write_text(
                json.dumps({"toolUseId": "spawn"})
            )
            collect(root, main)
            collect(root, child)
            before = cost(root)
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                db.execute("DROP VIEW request_detail")
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
                db.execute("PRAGMA user_version=5")
            collect(root, main)
            after = cost(root)
            self.assertEqual(before["priced_subset_usd"], after["priced_subset_usd"])
            rows = [record(item) for item in sequence(after["by_agent"], "agents")]
            self.assertIsNone(
                next(item for item in rows if item.get("agent") == "child")[
                    "parent_agent"
                ]
            )
            with main.open("ab") as stream:
                stream.write(request("collision", agent="other", tool="spawn"))
            collect(root, main)
            rows = [record(item) for item in sequence(cost(root)["by_agent"], "agents")]
            self.assertEqual(
                next(item for item in rows if item.get("agent") == "child")[
                    "parent_agent"
                ],
                "unknown",
            )
