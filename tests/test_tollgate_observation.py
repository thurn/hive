"""Native transcripts and a recorded CLI boundary drive candidate observation."""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from server_fixture import private_server
from test_bead_cost import command, last, setup
from test_diagnostics import native
from test_links import bd
from test_project_observation import PARENT, launch, native_index

from hive.collection import sweep
from hive.jsonvalue import record, sequence
from hive.tollgate_observation import candidates, poll
from hive.usage_store import UsageStore

REPO = "01a0cc79-6fa9-7be1-ac9c-3f2d23fad671"
CANDIDATE = "01a0d70d-2af4-72e1-83b6-323799e51a73"
FALLBACK = "01a0d723-0a34-7971-890c-63813ca198c7"
BAD = "01a0d723-0a34-7971-890c-63813ca198c8"
ATTEMPT = "01a0d70d-2cc8-7912-b101-8391d8be1af4"
SECRET = "PRIVATE_CI_LOG_MUST_NOT_PERSIST"


def date_array(at: datetime) -> list[int]:
    return [
        at.year,
        at.timetuple().tm_yday,
        at.hour,
        at.minute,
        at.second,
        at.microsecond * 1000,
        0,
        0,
        0,
    ]


def candidate(
    identity: str, branch: str, at: datetime, *, state: str = "promoted"
) -> dict[str, object]:
    result = record(
        dict(
            item=dict(
                id=identity,
                repository_id=REPO,
                state=state,
                terminal_reason=None,
                metadata=dict(
                    branch=branch,
                    subject="Fixture candidate",
                    approved_at=date_array(at),
                ),
            ),
            certificate=dict(created_at=date_array(at + timedelta(seconds=3))),
            attempts=[
                dict(
                    id=ATTEMPT,
                    attempt=1,
                    state="passed",
                    created_at=date_array(at),
                    started_at=date_array(at + timedelta(seconds=1)),
                    finished_at=date_array(at + timedelta(seconds=3)),
                    step_results=[
                        dict(
                            name="ci",
                            result_class="success",
                            exit_code=0,
                            elapsed_ms=2000,
                            diagnostics=[SECRET],
                        )
                    ],
                )
            ],
        )
    )
    return result


def fake(root: Path, values: dict[str, object]) -> Path:
    fixture = root / "tollgate.json"
    fixture.write_text(json.dumps(values))
    executable = root / "tg"
    executable.write_text("#!/usr/bin/env python3\n" + """import json,sys
from pathlib import Path
import time
root=Path(__file__).parent
with (root/'calls.jsonl').open('a') as stream: stream.write(json.dumps(sys.argv[1:])+'\\n')
assert '--json' in sys.argv and '--no-launch' in sys.argv
values=json.loads((root/'tollgate.json').read_text())
time.sleep(values.get('delay',0))
if values.get('unavailable'): raise SystemExit(1)
if sys.argv[-2:]==['repo','list']: print(json.dumps(values['repositories']))
else:
 assert '--repository' in sys.argv
 value=values.get(sys.argv[-1])
 if value is None:
  print(json.dumps({"error":{"code":"not-found"},"ok":False}))
  raise SystemExit(2)
 print(json.dumps(value))
""")
    executable.chmod(0o755)
    return fixture


class TollgateTests(unittest.TestCase):
    def test_confirmed_mentions_ownership_branch_cache_and_outage(self) -> None:
        with private_server() as (beads, _), tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            setup(root, beads)
            bead = str(
                bd(
                    beads,
                    "create",
                    "Candidate work",
                    "--metadata",
                    json.dumps({"hive_origin_thread": PARENT}),
                )["id"]
            )
            bd(beads, "--actor", PARENT, "update", bead, "--claim")
            at = last(beads, bead) + timedelta(milliseconds=1)
            direct = candidate(CANDIDATE, "codex/unmatched", at)
            failed = record(
                json.loads(json.dumps(sequence(direct["attempts"], "attempts")[0]))
            )
            failed["state"] = "failed"
            failed["steps"] = []
            failed["step_results"] = [
                dict(
                    name="ci", result_class="exit-failure", exit_code=1, elapsed_ms=2000
                )
            ]
            passed = record(sequence(direct["attempts"], "attempts")[0])
            passed["id"] = BAD
            passed["attempt"] = 2
            direct["attempts"] = [failed, passed]
            branch = candidate(FALLBACK, bead + "-feature", at)
            values: dict[str, object] = {
                "repositories": [
                    dict(
                        state=dict(id=REPO, path=str(root)),
                        queue=[],
                        checks=[],
                        history_items=[branch],
                        history=[
                            dict(
                                kind="promotion.completed",
                                payload=dict(id=CANDIDATE),
                                created_at=date_array(at + timedelta(seconds=8)),
                            )
                        ],
                    )
                ],
                CANDIDATE: direct,
                FALLBACK: branch,
            }
            fixture = fake(root, values)
            path = root / "native.jsonl"
            data = native("session_meta", {"id": PARENT}, at)
            data += native(
                "response_item",
                {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "submit",
                    "input": 'text(await tools.exec_command({cmd: "tg --json candidate HEAD"}));',
                },
                at,
            )
            data += native(
                "response_item",
                {
                    "type": "custom_tool_call_output",
                    "call_id": "submit",
                    "output": "Script completed\nOutput:\n"
                    + json.dumps(
                        dict(
                            exit_code=0,
                            wall_time_seconds=0.1,
                            output=json.dumps(dict(item_id=CANDIDATE)),
                        )
                    ),
                },
                at + timedelta(seconds=1),
            )
            path.write_bytes(data)
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                PARENT,
                "--transcript",
                str(path),
            )
            # Upgrade an already scanned schema-14 transcript without explicit rewind.
            with sqlite3.connect(root / "state/telemetry.sqlite3") as db:
                from dashboard_fixture import remove_dashboard

                remove_dashboard(db)
                for table in (
                    "tollgate_promotions",
                    "tollgate_mapping_state",
                    "tollgate_repositories",
                    "tollgate_mentions",
                    "tollgate_pending",
                    "tollgate_candidates",
                    "tollgate_branch_matches",
                    "tollgate_health",
                ):
                    db.execute(f"DROP TABLE {table}")
                db.execute("PRAGMA user_version=14")
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                PARENT,
                "--transcript",
                str(path),
            )
            context = launch(root, root, beads.directory)
            index = root / "native.sqlite3"
            native_index(index)
            with patch.dict(
                os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}
            ):
                for _ in range(3):
                    sweep(replace(context, projects=()), index, 32)
                store = UsageStore(context.state / "telemetry.sqlite3")
                poll(store, context, time.monotonic() + 2)
                with store.connect(write=False) as db:
                    results = candidates(db)
                    self.assertEqual(len(results), 2, results)
                    self.assertEqual(results[0]["id"], CANDIDATE)
                    self.assertEqual(
                        results[0]["updated_at"],
                        (at + timedelta(seconds=8)).isoformat(),
                    )
                    by_id = {str(v["id"]): v for v in results}
                    self.assertEqual(
                        by_id[CANDIDATE]["promoted_at"],
                        (at + timedelta(seconds=8)).isoformat(),
                    )
                    links = sequence(by_id[CANDIDATE]["links"], "links")
                    self.assertEqual(record(links[0])["beads"], [bead])
                    self.assertEqual(record(links[0])["method"], "transcript")
                    self.assertEqual(
                        record(sequence(by_id[FALLBACK]["links"], "links")[0])[
                            "method"
                        ],
                        "branch",
                    )
                    attempt = record(
                        sequence(by_id[CANDIDATE]["attempts"], "attempts")[0]
                    )
                    self.assertEqual(
                        record(sequence(attempt["steps"], "steps")[0])["elapsed_ms"],
                        2000,
                    )
                    self.assertNotIn(SECRET, json.dumps(results))
                before = (root / "calls.jsonl").read_text()
                poll(store, context, time.monotonic() + 2)
                self.assertEqual((root / "calls.jsonl").read_text(), before)
                # A new proven mention triggers a native read while confirmed terminal rows stay cached.
                with path.open("ab") as stream:
                    stream.write(
                        native(
                            "response_item",
                            {
                                "type": "function_call",
                                "name": "Bash",
                                "call_id": "next",
                                "arguments": json.dumps(
                                    {"command": "tg status " + BAD}
                                ),
                            },
                            at + timedelta(seconds=4),
                        )
                    )
                command(
                    root,
                    "telemetry",
                    "collect",
                    "--task",
                    PARENT,
                    "--transcript",
                    str(path),
                )
                fixture.write_text(json.dumps({**values, "unavailable": True}))
                health = poll(store, context, time.monotonic() + 2)
                self.assertTrue(health["tollgate_unavailable"])
                with store.connect(write=False) as db:
                    self.assertEqual(len(candidates(db)), 2)
                self.assertNotIn(SECRET.encode(), store.path.read_bytes())

    def test_bad_timestamp_and_unproven_uuids_never_become_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            at = datetime.now(UTC)
            broken = candidate(BAD, "unmatched", at)
            record(broken["item"])  # Assert the fixture's boundary shape.
            item = record(broken["item"])
            metadata = record(item["metadata"])
            metadata["approved_at"] = [2026, 999]
            item["metadata"] = metadata
            broken["item"] = item
            fake(
                root,
                {
                    "repositories": [
                        dict(
                            state=dict(id=REPO, path=str(root)),
                            queue=[],
                            checks=[],
                            history_items=[],
                        )
                    ],
                    BAD: broken,
                    **{
                        f"01a0d723-0a34-7971-890c-63813ca198d{n}": candidate(
                            f"01a0d723-0a34-7971-890c-63813ca198d{n}",
                            "unmatched",
                            at,
                            state=state,
                        )
                        for n, state in enumerate(
                            (
                                "constructing",
                                "promoted-local-push-pending",
                                "externally-integrated",
                                "dependency-failed",
                                "infrastructure-exhausted",
                            )
                        )
                    },
                },
            )
            path = root / "native.jsonl"
            path.write_bytes(
                native("session_meta", {"id": PARENT}, at)
                + native(
                    "response_item",
                    {
                        "type": "function_call",
                        "name": "Read",
                        "call_id": "unproven",
                        "arguments": json.dumps({"path": CANDIDATE}),
                    },
                    at,
                )
                + native(
                    "response_item",
                    {
                        "type": "function_call",
                        "name": "Bash",
                        "call_id": "status",
                        "arguments": json.dumps(
                            {"command": "tg --repository " + REPO + " status " + BAD}
                        ),
                    },
                    at,
                )
            )
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                PARENT,
                "--transcript",
                str(path),
            )
            with path.open("ab") as stream:
                for n in range(5):
                    identity = f"01a0d723-0a34-7971-890c-63813ca198d{n}"
                    stream.write(
                        native(
                            "response_item",
                            {
                                "type": "function_call",
                                "name": "Bash",
                                "call_id": f"valid-{n}",
                                "arguments": json.dumps(
                                    {"command": "tg status " + identity}
                                ),
                            },
                            at,
                        )
                    )
            command(
                root,
                "telemetry",
                "collect",
                "--task",
                PARENT,
                "--transcript",
                str(path),
            )
            context = launch(root, root)
            store = UsageStore(context.state / "telemetry.sqlite3")
            with patch.dict(
                os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}
            ):
                health = poll(store, context, time.monotonic() + 2)
            self.assertEqual(health["tollgate_error"], "tollgate_gap")
            with store.connect(write=False) as db:
                self.assertEqual(len(candidates(db)), 5)
            self.assertNotIn(CANDIDATE, (root / "calls.jsonl").read_text())

    def test_native_timeout_respects_poll_allowance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake(root, {"repositories": [], "delay": 2})
            context = launch(root, root)
            store = UsageStore(context.state / "telemetry.sqlite3")
            with store.connect():
                pass
            with patch.dict(
                os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}
            ):
                started = time.monotonic()
                health = poll(store, context, started + 0.1)
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertTrue(health["tollgate_unavailable"])
