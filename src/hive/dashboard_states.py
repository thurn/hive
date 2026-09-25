"""Read-time status uses every source belonging to an active root session."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hive.dashboard_values import rows
from hive.jsonvalue import string
from hive.usage import timestamp

FAILURES: frozenset[str] = frozenset(
    {
        "failed",
        "check-failed",
        "merge-conflict",
        "dependency-failed",
        "infrastructure-exhausted",
    }
)
RUNNING: frozenset[str] = frozenset(
    {
        "constructing",
        "queued",
        "running",
        "checking",
        "approved",
        "promoting",
        "awaiting-approval",
    }
)


def activity(connection: sqlite3.Connection, now: datetime) -> dict[str, float]:
    cutoff = (now - timedelta(days=1)).isoformat()
    result: dict[str, float] = {}
    for value in rows(
        connection,
        "SELECT DISTINCT s.task,s.path FROM sources s WHERE s.task IN (SELECT COALESCE(r.root,i.thread) FROM bead_intervals i LEFT JOIN codex_roots r ON r.thread=i.thread WHERE i.end IS NULL UNION SELECT thread FROM dashboard_contributions WHERE last_activity>=?)",
        (cutoff,),
    ):
        thread = string(value["task"], "thread")
        try:
            modified = Path(string(value["path"], "source")).stat().st_mtime
        except OSError:
            continue
        result[thread] = max(result.get(thread, 0), modified)
    return result


def state(
    card: dict[str, object], now: datetime, modified: float, ci: str | None
) -> str:
    idle = now.timestamp() - modified
    if card["kind"] != "bead":
        return "Working" if idle < 300 else "Idle" if idle < 86400 else "Finished"
    if (
        ci in FAILURES
        or card.get("deleted")
        or card.get("interval_status") in {"unknown", "pending"}
    ):
        return "Needs attention"
    if ci in RUNNING:
        return "In CI"
    native = card.get("status")
    if native == "in_progress":
        return "Working" if idle < 300 else "Stalled" if idle >= 1800 else "Idle"
    if native == "open":
        return "Blocked" if card.get("blockers") else "Ready"
    if native == "deferred":
        return "Deferred" if card.get("deferred_until") else "Awaiting approval"
    if native == "closed":
        return (
            "Cancelled"
            if card.get("resolution") == "cancelled"
            else "Complete" if card.get("resolution") == "completed" else "Closed"
        )
    return "Needs attention"


def fallback_time(value: object) -> float:
    return (
        timestamp(value).astimezone(UTC).timestamp()
        if isinstance(value, str) and value
        else 0
    )
