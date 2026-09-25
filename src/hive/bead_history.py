"""Bounded native history refresh keeps former assignees observable."""

import sqlite3
import time
from dataclasses import replace
from datetime import timedelta

from hive.bead_events import Event, event_time
from hive.bead_events import read as events
from hive.bead_queries import BEAD, FIRST, PAGE, UUID7
from hive.beads_process import BeadsProcess
from hive.errors import ErrorCode, HiveError
from hive.identity import ThreadId
from hive.jsonvalue import integer, record, sequence, string
from hive.locking import file_lock
from hive.thread_links import ThreadLink, decode, thread_id
from hive.usage import timestamp
from hive.usage_store import UsageStore, row


def save(connection: sqlite3.Connection, values: tuple[Event, ...]) -> None:
    from hive.bead_intervals import owners

    for event in values:
        connection.execute(
            "INSERT OR IGNORE INTO bead_snapshots(bead,status,assignee,listed,rebuilt) VALUES (?,'','',0,0)",
            (event.bead,),
        )
        changed = connection.execute(
            "INSERT INTO bead_events VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "bead=excluded.bead,kind=excluded.kind,old_status=excluded.old_status,old_assignee=excluded.old_assignee,"
            "new_status=excluded.new_status,new_assignee=excluded.new_assignee,occurred=excluded.occurred,error=excluded.error "
            "WHERE bead_events.bead IS NOT excluded.bead OR bead_events.kind IS NOT excluded.kind "
            "OR bead_events.old_status IS NOT excluded.old_status OR bead_events.old_assignee IS NOT excluded.old_assignee "
            "OR bead_events.new_status IS NOT excluded.new_status OR bead_events.new_assignee IS NOT excluded.new_assignee "
            "OR bead_events.occurred IS NOT excluded.occurred OR bead_events.error IS NOT excluded.error",
            (
                event.identity,
                event.bead,
                event.kind,
                event.old_status,
                event.old_assignee,
                event.new_status,
                event.new_assignee,
                None if event.occurred is None else event.occurred.isoformat(),
                event.error,
            ),
        ).rowcount
        if changed:
            connection.execute(
                "UPDATE bead_snapshots SET dirty=1 WHERE bead=?", (event.bead,)
            )
        owners(connection, event.bead, (event,))
        connection.execute(
            "UPDATE bead_snapshots SET rebuilt=1 WHERE bead=? AND listed=1",
            (event.bead,),
        )


def historical(connection: sqlite3.Connection) -> tuple[ThreadLink, ...]:
    fetched: object = connection.execute(
        "SELECT bead,old_assignee FROM bead_events UNION SELECT bead,new_assignee FROM bead_events "
        "UNION SELECT bead,thread FROM bead_seen_owners WHERE bead NOT IN (SELECT bead FROM bead_replays WHERE renamed_to IS NOT NULL) "
        "UNION SELECT bead,thread FROM bead_intervals"
    ).fetchall()
    links: set[ThreadLink] = set()
    for value in sequence(fetched, "historical assignees"):
        bead, assignee = row(value, 2)
        task: ThreadId | None = thread_id(assignee)
        if task is not None:
            links.add(ThreadLink(task, string(bead, "bead"), "executor", False))
    return tuple(sorted(links))


def status(connection: sqlite3.Connection) -> dict[str, object]:
    fetched: object = connection.execute(
        "SELECT caught_up,error FROM bead_event_cursor WHERE singleton=1"
    ).fetchone()
    caught, error = (0, None) if fetched is None else row(fetched, 2)
    unknown: object = connection.execute(
        "SELECT COUNT(*) FROM bead_snapshots WHERE unknown_reason IS NOT NULL"
    ).fetchone()
    return {
        "bead_events_caught_up": bool(integer(caught, "events caught up")),
        "bead_events_behind": not bool(caught),
        "bead_events_error": error,
        "interval_unknown_beads": integer(row(unknown, 1)[0], "unknown beads"),
    }


def refresh(
    store: UsageStore, process: BeadsProcess, deadline: float
) -> tuple[tuple[ThreadLink, ...], tuple[str, ...]]:
    with file_lock(store.path.parent / "bead-history.lock", timeout=0):
        return _refresh(store, process, deadline)


def _refresh(
    store: UsageStore, process: BeadsProcess, deadline: float
) -> tuple[tuple[ThreadLink, ...], tuple[str, ...]]:
    def bounded() -> BeadsProcess:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Beads history budget exhausted"
            )
        return replace(process, timeout=remaining)

    listed = bounded().list_all()
    links, gaps = decode(listed)
    snapshots: list[tuple[str, str, str, str | None]] = []
    for raw in sequence(listed, "beads"):
        bead = record(raw, "bead")
        identity = string(bead.get("id"), "bead ID")
        if BEAD.fullmatch(identity) is not None:
            snapshots.append(
                (
                    identity,
                    string(bead.get("status"), "bead status"),
                    (
                        ""
                        if bead.get("assignee") is None
                        else string(bead["assignee"], "assignee", empty=True)
                    ),
                    (
                        None
                        if bead.get("created_at") is None
                        else timestamp(bead["created_at"]).isoformat()
                    ),
                )
            )
    with store.connect() as connection:
        from hive.dashboard_beads import cache as cache_beads

        cache_beads(connection, listed)
        listed_ids = {item[0] for item in snapshots}
        previous_snapshots: object = connection.execute(
            "SELECT bead,listed FROM bead_snapshots"
        ).fetchall()
        changed = [
            (string(row(item, 2)[0], "bead"),)
            for item in sequence(previous_snapshots, "previous beads")
            if bool(row(item, 2)[1]) != (row(item, 2)[0] in listed_ids)
        ]
        connection.execute("UPDATE bead_snapshots SET listed=0")
        connection.executemany(
            "UPDATE bead_snapshots SET rebuilt=0,dirty=1 WHERE bead=?", changed
        )
        connection.executemany(
            "INSERT INTO bead_snapshots(bead,status,assignee,created,listed) VALUES (?,?,?,?,1) "
            "ON CONFLICT(bead) DO UPDATE SET dirty=CASE WHEN bead_snapshots.status<>excluded.status OR bead_snapshots.assignee<>excluded.assignee THEN 1 ELSE dirty END,status=excluded.status,assignee=excluded.assignee,created=excluded.created,listed=1,native_missing=0",
            snapshots,
        )
        previous: object = connection.execute(
            "SELECT after_id,newest,caught_up FROM bead_event_cursor WHERE singleton=1"
        ).fetchone()
        after, newest, caught = (
            (FIRST, FIRST, 0) if previous is None else row(previous, 3)
        )
        after, newest = string(after, "event cursor"), string(newest, "newest event")
        if caught:
            cutoff = event_time(newest) - timedelta(minutes=10)
            prior: object = connection.execute(
                "SELECT MAX(id) FROM bead_events WHERE occurred<=? AND error IS NULL",
                (cutoff.isoformat(),),
            ).fetchone()
            at = row(prior, 1)[0]
            after = FIRST if at is None else string(at, "rewind cursor")
        connection.execute(
            "INSERT INTO bead_event_cursor VALUES (1,?,?,0,NULL) ON CONFLICT(singleton) DO UPDATE SET after_id=excluded.after_id,newest=excluded.newest,caught_up=0,error=NULL",
            (after, newest),
        )
    caught_up = False
    failure: str | None = None
    try:
        while time.monotonic() < deadline:
            page = events(bounded().event_page(after))
            valid = [
                event.identity for event in page if UUID7.fullmatch(event.identity)
            ]
            next_id = max([after, *valid])
            newest = max(
                [newest, *(event.identity for event in page if event.error is None)]
            )
            caught_up = len(page) < PAGE
            with store.connect() as connection:
                save(connection, page)
                connection.execute(
                    "UPDATE bead_event_cursor SET after_id=?,newest=? WHERE singleton=1",
                    (next_id, newest),
                )
            if caught_up:
                break
            if next_id == after:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Beads page has no valid advancing cursor"
                )
            after = next_id
        if caught_up:
            with store.connect(write=False) as connection:
                fetched: object = connection.execute(
                    "SELECT bead FROM bead_snapshots WHERE rebuilt=0 AND "
                    "(listed=0 OR NOT EXISTS (SELECT 1 FROM bead_events e WHERE e.bead=bead_snapshots.bead)) ORDER BY bead"
                ).fetchall()
            for raw in sequence(fetched, "beads needing history"):
                if time.monotonic() >= deadline:
                    caught_up = False
                    break
                identity = string(row(raw, 1)[0], "bead")
                values = events(bounded().bead_events(identity))
                if any(event.bead != identity for event in values):
                    raise HiveError(
                        ErrorCode.INVALID_RECORD, "Beads history returned another bead"
                    )
                with store.connect() as connection:
                    save(connection, values)
                    connection.execute(
                        "UPDATE bead_snapshots SET rebuilt=1,native_missing=? WHERE bead=?",
                        (
                            int(not values),
                            identity,
                        ),
                    )
    except (HiveError, OSError) as error:
        caught_up = False
        failure = str(error)
    if caught_up:
        from hive.bead_intervals import refresh as replay_intervals

        caught_up = replay_intervals(store, process, deadline)
    with store.connect() as connection:
        connection.execute(
            "UPDATE bead_event_cursor SET caught_up=?,error=? WHERE singleton=1",
            (int(caught_up), failure),
        )
        combined = set(links) | set(historical(connection))
        if not caught_up:
            previous_links: object = connection.execute(
                "SELECT task,bead,relation FROM collection_links"
            ).fetchall()
            for raw in sequence(previous_links, "retained links"):
                task, bead, relation = row(raw, 3)
                combined.add(
                    ThreadLink(
                        ThreadId(string(task, "thread")),
                        string(bead, "bead"),
                        string(relation, "relation"),
                        False,
                    )
                )
    return tuple(sorted(combined)), gaps
