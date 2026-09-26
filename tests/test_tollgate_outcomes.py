"""Recorded native repository history reconciles without branches or mentions."""

import json
import os
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from test_bead_cost import command
from test_project_observation import launch
from test_tollgate_observation import (
    BAD,
    CANDIDATE,
    FALLBACK,
    REPO,
    candidate,
    date_array,
    fake,
)

from hive.errors import HiveError
from hive.jsonvalue import record, sequence
from hive.tollgate_observation import candidates, poll
from hive.tollgate_report import report
from hive.usage_store import UsageStore


class OutcomesTests(unittest.TestCase):
    def test_recorded_native_history_reconciles_and_survives_rollover(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = launch(root, root)
            store = UsageStore(context.state / "telemetry.sqlite3")
            items = sequence(
                json.loads(
                    (
                        Path(__file__).parent / "fixtures/tollgate-outcomes.json"
                    ).read_text()
                ),
                "items",
            )
            repo = record(record(items[0])["item"])["repository_id"]
            snapshot = dict(
                state=dict(id=repo, path=str(root)),
                history_items=items,
                queue=[items[0]],
                checks=[],
            )
            fixture = fake(root, {"repositories": [snapshot]})
            with store.connect():
                pass
            with patch.dict(
                os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}
            ):
                health = poll(store, context, time.monotonic() + 2)
                self.assertIsNone(health["tollgate_error"])
                self.assertFalse(health["tollgate_behind"])
                with store.connect(write=False) as db:
                    result = report(
                        db, "sample", "2026-09-25T00:00:00Z", "2026-09-26T00:00:00Z"
                    )
                    self.assertEqual(result["unique_candidates"], 24)
                    self.assertEqual(
                        result["counts"],
                        dict(
                            promoted=19,
                            conflict=2,
                            validation_failed=2,
                            canceled=1,
                            other_terminal=0,
                            in_progress=0,
                        ),
                    )
                    self.assertFalse(record(result["coverage"])["complete"])
                    retained = {str(c["id"]): c for c in candidates(db)}
                    missing = retained["01a0d978-1703-7ee1-b26d-5f5608d2c328"]
                    self.assertEqual(missing["links"], [])
                    self.assertEqual(
                        missing["diagnostic_command"],
                        ["tg", "--repository", repo, "status", missing["id"], "--json"],
                    )
                    self.assertNotIn("produced merge conflicts", json.dumps(retained))
                self.assertEqual(
                    len((root / "calls.jsonl").read_text().splitlines()), 1
                )
                with patch.dict(
                    os.environ,
                    {
                        "HIVE_PROJECTS": json.dumps(
                            [dict(id="sample", repository=str(root))]
                        )
                    },
                ):
                    cli = command(
                        root,
                        "telemetry",
                        "outcomes",
                        "--project",
                        "sample",
                        "--start",
                        "2026-09-25T00:00:00Z",
                        "--end",
                        "2026-09-26T00:00:00Z",
                    )
                    self.assertEqual(cli["counts"], result["counts"])
                # Expire the native snapshot, then simulate its bounded history rolling over.
                with store.connect() as db:
                    db.execute(
                        "UPDATE tollgate_mapping_state SET refreshed='2000-01-01'"
                    )
                fixture.write_text(
                    json.dumps(
                        {
                            "repositories": [
                                {**snapshot, "history_items": [], "queue": []}
                            ]
                        }
                    )
                )
                poll(store, context, time.monotonic() + 2)
                with store.connect(write=False) as db:
                    self.assertEqual(len(candidates(db)), 24)
                    result = report(
                        db, "sample", "2026-09-25T00:00:00Z", "2026-09-26T00:00:00Z"
                    )
                    self.assertEqual(
                        record(result["coverage"])["history_candidates"], 0
                    )
                    self.assertEqual(result["unique_candidates"], 24)
                # A malformed next response rolls back mapping and candidate changes.
                with store.connect() as db:
                    db.execute("DELETE FROM tollgate_mapping_state")
                fixture.write_text(
                    json.dumps({"repositories": [{**snapshot, "history_items": [{}]}]})
                )
                self.assertEqual(
                    poll(store, context, time.monotonic() + 2)["tollgate_error"],
                    "tollgate_gap",
                )
                with store.connect(write=False) as db:
                    self.assertEqual(len(candidates(db)), 24)
                    self.assertIn(
                        "tollgate_gap",
                        sequence(
                            record(
                                report(
                                    db,
                                    "sample",
                                    "2026-09-25T00:00:00Z",
                                    "2026-09-26T00:00:00Z",
                                )["coverage"]
                            )["gaps"],
                            "gaps",
                        ),
                    )

    def test_events_retries_checks_window_and_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = launch(root, root)
            store = UsageStore(context.state / "telemetry.sqlite3")
            at = datetime.now(UTC) - timedelta(minutes=1)
            original = candidate(CANDIDATE, "unmatched", at)
            retry = candidate(FALLBACK, "unmatched", at + timedelta(seconds=1))
            item = record(retry["item"])
            item["retry_of_item_id"] = CANDIDATE
            retry["item"] = item
            attempt = record(sequence(retry["attempts"], "attempts")[0])
            retry["attempts"] = [attempt, {**attempt, "id": BAD, "attempt": 2}]
            check = candidate(BAD, "unmatched", at)
            check["item"] = {
                **record(check["item"]),
                "kind": "check",
                "state": "check-passed",
            }
            fake(
                root,
                {
                    "repositories": [
                        dict(
                            state=dict(id=REPO, path=str(root)),
                            history_items=[retry],
                            queue=[retry],
                            checks=[check],
                            history=[
                                dict(
                                    kind="candidate.created",
                                    payload=dict(id=CANDIDATE),
                                    created_at=date_array(at),
                                )
                            ],
                        )
                    ],
                    CANDIDATE: original,
                },
            )
            with store.connect():
                pass
            with patch.dict(
                os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}
            ):
                poll(store, context, time.monotonic() + 2)
            with store.connect() as db:
                db.execute(
                    "UPDATE tollgate_candidates SET payload=json_remove(payload,'$.kind','$.retry_of','$.diagnostic_command') WHERE candidate=?",
                    (CANDIDATE,),
                )
                db.execute(
                    "UPDATE tollgate_pending SET checked=NULL WHERE candidate=?",
                    (CANDIDATE,),
                )
                before = report(
                    db,
                    "sample",
                    at.isoformat(),
                    (at + timedelta(seconds=2)).isoformat(),
                )
                self.assertEqual(before["unique_candidates"], 1)
                self.assertEqual(before["legacy_candidates"], 1)
                # v18 considered its branch-only snapshot fresh; migration must invalidate it.
                db.execute("DROP TABLE tollgate_coverage")
                db.execute(
                    "INSERT OR REPLACE INTO tollgate_mapping_state VALUES (1,?,?)",
                    (
                        json.dumps(
                            [(p.id, str(p.repository)) for p in context.projects]
                        ),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                db.execute("PRAGMA user_version=18")
            with store.connect():
                pass
            with patch.dict(
                os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}
            ):
                poll(store, context, time.monotonic() + 2)
            with store.connect(write=False) as db:
                result = report(
                    db,
                    "sample",
                    at.isoformat(),
                    (at + timedelta(seconds=2)).isoformat(),
                )
                self.assertEqual(result["unique_candidates"], 2)
                self.assertEqual(result["retry_candidates"], 1)
                self.assertEqual(result["legacy_candidates"], 0)
                self.assertEqual(result["validation_attempts"], 3)
                self.assertEqual(result["additional_validation_attempts"], 1)
                self.assertEqual(result["excluded_checks"], 1)
                self.assertEqual(record(result["coverage"])["unresolved_candidates"], 0)
                self.assertEqual(
                    report(
                        db,
                        "sample",
                        at.isoformat(),
                        (at + timedelta(seconds=1)).isoformat(),
                    )["unique_candidates"],
                    1,
                )
                for start, end in (
                    (at.isoformat(), at.isoformat()),
                    ("2026-09-25", "2026-09-26"),
                ):
                    with self.assertRaises(HiveError):
                        report(db, "sample", start, end)
                self.assertIn(
                    "repository_not_observed",
                    sequence(
                        record(
                            report(
                                db,
                                "unknown",
                                at.isoformat(),
                                (at + timedelta(seconds=2)).isoformat(),
                            )["coverage"]
                        )["gaps"],
                        "gaps",
                    ),
                )

    def test_legacy_check_with_unavailable_details_stays_out_of_denominator(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = launch(root, root)
            store = UsageStore(context.state / "telemetry.sqlite3")
            at = datetime.now(UTC) - timedelta(seconds=10)
            check = candidate(BAD, "unmatched", at, state="check-passed")
            check["item"] = {**record(check["item"]), "kind": "check"}
            snapshot = dict(state=dict(id=REPO, path=str(root)), checks=[check])
            fixture = fake(root, {"repositories": [snapshot]})
            with store.connect():
                pass
            with patch.dict(
                os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}
            ):
                poll(store, context, time.monotonic() + 2)
                with store.connect() as db:
                    db.execute(
                        "UPDATE tollgate_candidates SET payload=json_remove(payload,'$.kind','$.retry_of')"
                    )
                    db.execute("DROP TABLE tollgate_coverage")
                    db.execute("PRAGMA user_version=18")
                fixture.write_text(
                    json.dumps({"repositories": [{**snapshot, "checks": []}]})
                )
                with store.connect():
                    pass
                poll(store, context, time.monotonic() + 2)
            with store.connect(write=False) as db:
                result = report(
                    db,
                    "sample",
                    at.isoformat(),
                    (at + timedelta(seconds=20)).isoformat(),
                )
                self.assertEqual(result["unique_candidates"], 0)
                self.assertEqual(result["legacy_candidates"], 1)
                self.assertEqual(result["excluded_checks"], 0)
                self.assertIn(
                    "legacy_candidate_metadata",
                    sequence(record(result["coverage"])["gaps"], "gaps"),
                )
                self.assertEqual(len(candidates(db)), 1)
