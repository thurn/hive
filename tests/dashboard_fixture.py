"""Reconstruct a pre-dashboard schema for migration journeys."""

import sqlite3


def remove_dashboard(db: sqlite3.Connection) -> None:
    db.execute("DROP VIEW IF EXISTS dashboard_requests")
    for (trigger,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'dashboard_%'"
    ).fetchall():
        db.execute(f"DROP TRIGGER {trigger}")
    for (table,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'dashboard_%' OR name IN ('card_summaries','card_ci','bead_rows'))"
    ).fetchall():
        db.execute(f"DROP TABLE {table}")
    for name in (
        "responses_observed",
        "responses_task_response",
        "events_task_response",
    ):
        db.execute("DROP INDEX IF EXISTS " + name)
