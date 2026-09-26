"""Large active roots keep progressing without partial publication or starvation."""

import sqlite3
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from test_bead_cost import claude, command
from test_claude import OTHER, THREAD
from test_dashboard_api import api, objects, settle
from test_project_observation import launch

from hive.dashboard_summary import refresh
from hive.dashboard_values import rows
from hive.jsonvalue import record, string
from hive.usage_store import UsageStore


class DashboardBudgetTests(unittest.TestCase):
    def test_active_root_stages_atomically_then_applies_one_request_delta(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            claude(root, THREAD, "seed", datetime.now(UTC))
            claude(root, OTHER, "small", datetime.now(UTC))
            command(root, "cost", "--task", THREAD)
            command(root, "cost", "--task", OTHER)
            settle(root)
            path = root / "state/telemetry.sqlite3"
            with sqlite3.connect(path) as connection:
                names = [
                    string(v["name"], "column")
                    for v in rows(connection, "PRAGMA table_info(responses)")
                ]
                fields = ",".join(
                    "'copy-'||printf('%07d',n)" if name == "response" else name
                    for name in names
                )
                connection.execute(
                    f"WITH RECURSIVE n(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM n WHERE n<5000) INSERT INTO responses SELECT {fields} FROM responses,n WHERE response='seed'"
                )
                connection.execute(
                    "INSERT INTO allocation_responses(response,task,agent,previous,reset) "
                    "SELECT response,task,'',NULL,0 FROM responses WHERE response LIKE 'copy-%' ORDER BY response"
                )
                connection.execute(
                    "INSERT INTO response_estimates SELECT r.response,e.tier,e.quote FROM responses r JOIN response_estimates e ON e.response='seed' WHERE r.response<>'seed' AND r.task=?",
                    (THREAD,),
                )
            store = UsageStore(path)
            published = False
            for _ in range(200):
                # Streaming metadata changes used to restart the prefix forever.
                with sqlite3.connect(path) as connection:
                    connection.execute(
                        "UPDATE responses SET last_observed=? WHERE response='seed'",
                        (datetime.now(UTC).isoformat(),),
                    )
                start = time.monotonic()
                refresh(store, launch(root, root), start + 0.2)
                self.assertLess(time.monotonic() - start, 0.35)
                detail = api(root, "ledger", "unattributable", "Other")
                contributors = {
                    v["thread"]: v["amount_picos"]
                    for v in objects(detail["contributors"])
                }
                amount = int(string(contributors[THREAD], "amount"))
                expected = 5001 * 438000000
                self.assertIn(amount, (438000000, expected))
                if amount == expected:
                    published = True
                    break
            self.assertTrue(published)
            claude(root, THREAD, "a-late", datetime.now(UTC))
            for _ in range(2):
                start = time.monotonic()
                refresh(store, launch(root, root), start + 0.2)
                self.assertLess(time.monotonic() - start, 0.35)
            detail = api(root, "ledger", "unattributable", "Other")
            contributors = {
                v["thread"]: v["amount_picos"] for v in objects(detail["contributors"])
            }
            self.assertEqual(
                int(string(contributors[THREAD], "amount")), 5002 * 438000000
            )
            self.assertEqual(contributors[OTHER], "438000000")
            self.assertEqual(
                record(detail["card"])["amount_picos"], str(5003 * 438000000)
            )
            # More than one delta page must retain every pending identity.
            with sqlite3.connect(path) as connection:
                burst_fields = ",".join(
                    "'burst-'||printf('%07d',n)" if name == "response" else name
                    for name in names
                )
                connection.execute(
                    f"WITH RECURSIVE n(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM n WHERE n<260) INSERT INTO responses SELECT {burst_fields} FROM responses,n WHERE response='seed'"
                )
            for _ in range(200):
                start = time.monotonic()
                result = refresh(store, launch(root, root), start + 0.2)
                self.assertLess(time.monotonic() - start, 0.35)
                if not result["summaries_behind"]:
                    break
            else:
                self.fail("Burst did not catch up")
            detail = api(root, "ledger", "unattributable", "Other")
            self.assertEqual(
                record(detail["card"])["amount_picos"], str(5263 * 438000000)
            )
            # Catching up native ownership evidence must rebuild published roots.
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "INSERT INTO bead_event_cursor VALUES (1,'','',1,NULL) ON CONFLICT(singleton) DO UPDATE SET caught_up=1,error=NULL"
                )
            for _ in range(200):
                result = refresh(store, launch(root, root), time.monotonic() + 0.2)
                if not result["summaries_behind"]:
                    break
            feed = api(root, "feed", "--older-completed")
            cards = objects(feed["cards"])
            self.assertEqual(
                next(
                    v["amount_picos"] for v in cards if v["key"] == "session:" + THREAD
                ),
                str(5262 * 438000000),
            )
            self.assertFalse(any(v["kind"] == "unattributable" for v in cards))

    def test_constrained_pages_eventually_publish_every_unpriced_request(self) -> None:
        class WorkClock:
            """Charge deterministic time at each SQLite progress checkpoint."""

            def __init__(self) -> None:
                self.now = 0.0

            def monotonic(self) -> float:
                self.now += 0.00025
                return self.now

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            claude(root, THREAD, "seed", datetime.now(UTC))
            command(root, "cost", "--task", THREAD)
            settle(root)
            store = UsageStore(root / "state/telemetry.sqlite3")
            for phase in (1, 2):
                with store.connect() as connection:
                    names = [
                        string(v["name"], "column")
                        for v in rows(connection, "PRAGMA table_info(responses)")
                    ]
                    fields = ",".join(
                        f"'{phase}-'||printf('%07d',n)" if name == "response" else name
                        for name in names
                    )
                    connection.execute(
                        f"WITH RECURSIVE n(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM n WHERE n<260) INSERT INTO responses SELECT {fields} FROM responses,n WHERE response='seed'"
                    )
                # A page that cannot finish must yield without losing already committed
                # progress; repeatedly imposing the same budget must still converge.
                for attempt in range(400):
                    with patch("time.monotonic", WorkClock().monotonic):
                        result = refresh(
                            store, launch(root, root), time.monotonic() + 0.2
                        )
                    if attempt == 0 and phase == 1:
                        self.assertTrue(result["summaries_behind"])
                        detail = api(root, "ledger", "unattributable", "Other")
                        self.assertEqual(
                            record(detail["card"])["amount_picos"], "438000000"
                        )
                    if not result["summaries_behind"]:
                        break
                else:
                    self.fail("Constrained pages made no progress")
                detail = api(root, "ledger", "unattributable", "Other")
                self.assertEqual(
                    record(detail["card"])["amount_picos"],
                    str((1 + 260 * phase) * 438000000),
                )

    def test_large_diagnostic_baseline_makes_bounded_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            claude(root, THREAD, "seed", datetime.now(UTC))
            command(root, "cost", "--task", THREAD)
            store = UsageStore(root / "state/telemetry.sqlite3")
            now = datetime.now(UTC).isoformat()
            with store.connect() as connection:
                connection.execute(
                    "WITH RECURSIVE n(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM n WHERE n<10000) INSERT INTO tool_calls(host,thread,agent,call_id,tool,started_at,finished_at,duration_ms,status,input_hash8,input_bytes,result_bytes,file,use_offset,result_offset,use_device,use_inode,result_device,result_inode,command_kind) SELECT 'claude',?,'','call-'||printf('%06d',n),'Read',?,?,70000,'ok','e3b0c442',0,0,'',0,0,0,0,0,0,'other' FROM n",
                    (THREAD, now, now),
                )
            for _ in range(6):
                start = time.monotonic()
                refresh(store, launch(root, root), start + 0.2)
                self.assertLess(time.monotonic() - start, 0.35)
            # Read-only diagnostic status reports work outstanding; ingestion and
            # percentile warmup retain forward progress rather than retrying one scan.
            status = api(root, "status")
            self.assertTrue(status["summaries_behind"])
            with store.connect(write=False) as connection:
                baseline = rows(
                    connection, "SELECT cursor FROM dashboard_duration_state"
                )[0]
                self.assertGreater(int(str(baseline["cursor"])), 0)
