"""Seven-day event retention runs only with a trustworthy link refresh."""

import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hive.event_store import gap
from hive.jsonvalue import integer
from hive.usage_store import UsageStore, row


def retain(
    store: UsageStore, state: Path, deadline: float, *, permitted: bool
) -> dict[str, object]:
    if not permitted:
        return {"event_retention_skipped": True, "event_expired_files": 0}
    cutoff = datetime.now(UTC) - timedelta(days=7)
    expired = 0
    spool = state / "otlp-spool"
    if spool.is_symlink() or (spool / "rejected").is_symlink():
        return {
            "event_retention_skipped": True,
            "event_retention_error": "Spool symlink rejected",
        }
    for parent in (spool, spool / "rejected"):
        for path in parent.iterdir() if parent.is_dir() else ():
            if path.suffix not in {".json", ".tmp"}:
                continue
            if time.monotonic() >= deadline:
                return {
                    "event_retention_skipped": False,
                    "event_expired_files": expired,
                    "event_retention_behind": True,
                }
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_mtime >= cutoff.timestamp():
                continue
            with store.connect() as connection:
                gap(
                    connection,
                    str(path.relative_to(spool)),
                    -1,
                    "Spool file expired after seven days",
                    observed=datetime.fromtimestamp(info.st_mtime, UTC),
                )
            path.unlink(missing_ok=True)
            expired += 1
    if time.monotonic() >= deadline:
        return {
            "event_retention_skipped": False,
            "event_expired_files": expired,
            "event_retention_behind": True,
        }
    with store.connect() as connection:
        # Delete a bounded page. Later sweeps finish backlogs without monopolizing
        # the collection deadline; the registry is never inferred from events.
        for table in (
            "claude_request_events",
            "claude_event_sequences",
            "claude_request_errors",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} "
                "WHERE occurred<? AND task NOT IN (SELECT task FROM collection_tasks) LIMIT 1000)",
                (cutoff.isoformat(),),
            )
        connection.execute(
            "DELETE FROM response_estimates WHERE rowid IN (SELECT e.rowid FROM response_estimates e "
            "WHERE NOT EXISTS (SELECT 1 FROM responses r WHERE r.response=e.response) "
            "AND NOT EXISTS (SELECT 1 FROM claude_request_events v WHERE v.response=e.response) LIMIT 1000)"
        )
        remaining: object = connection.execute(
            "SELECT COUNT(*) FROM claude_request_events WHERE occurred<? AND task NOT IN (SELECT task FROM collection_tasks)",
            (cutoff.isoformat(),),
        ).fetchone()
    return {
        "event_retention_skipped": False,
        "event_expired_files": expired,
        "event_retention_behind": integer(row(remaining, 1)[0], "expired event count")
        > 0,
    }
