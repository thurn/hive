"""Certify replay against native before-states and the current bead snapshot."""

import hashlib
import json
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime

from hive.bead_events import Event, read
from hive.bead_replay import State, replay
from hive.beads_process import BeadsProcess
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import integer, sequence, string
from hive.thread_links import thread_id
from hive.usage import timestamp
from hive.usage_store import UsageStore, row


def cached(connection: sqlite3.Connection, bead: str) -> tuple[Event, ...]:
    fetched: object = connection.execute(
        "SELECT id,kind,old_status,old_assignee,new_status,new_assignee,occurred,error "
        "FROM bead_events WHERE bead=? ORDER BY id",
        (bead,),
    ).fetchall()
    result: list[Event] = []
    for value in sequence(fetched, "cached events"):
        (
            identity,
            kind,
            old_status,
            old_assignee,
            new_status,
            new_assignee,
            at,
            error,
        ) = row(value, 8)
        result.append(
            Event(
                string(identity, "event"),
                bead,
                string(kind, "kind"),
                string(old_status, "old status", empty=True),
                string(old_assignee, "old assignee", empty=True),
                (
                    None
                    if new_status is None
                    else string(new_status, "new status", empty=True)
                ),
                (
                    None
                    if new_assignee is None
                    else string(new_assignee, "new assignee", empty=True)
                ),
                None if at is None else timestamp(at),
                None if error is None else string(error, "event error"),
            )
        )
    return tuple(result)


def fingerprint(events: tuple[Event, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                (
                    event.identity,
                    event.kind,
                    event.old_status,
                    event.old_assignee,
                    event.new_status,
                    event.new_assignee,
                    None if event.occurred is None else event.occurred.isoformat(),
                    event.error,
                )
                for event in events
                if event.kind != "renamed"
            ]
        ).encode()
    ).hexdigest()


def owners(
    connection: sqlite3.Connection,
    bead: str,
    values: tuple[Event, ...],
    assignee: str = "",
) -> None:
    seen = {assignee}
    for event in values:
        seen.update((event.old_assignee, event.new_assignee or ""))
    connection.executemany(
        "INSERT OR IGNORE INTO bead_seen_owners VALUES (?,?)",
        [(bead, candidate) for candidate in seen if thread_id(candidate) is not None],
    )


def refresh(store: UsageStore, process: BeadsProcess, deadline: float) -> bool:
    from hive.bead_history import save

    with store.connect(write=False) as connection:
        fetched: object = connection.execute(
            "SELECT s.bead FROM bead_snapshots s LEFT JOIN bead_replays r ON s.bead=r.bead "
            "WHERE s.dirty=1 OR r.rebuild=1 OR r.bead IS NULL ORDER BY "
            "CASE WHEN r.status='pending' THEN 0 WHEN s.dirty=1 THEN 1 ELSE 2 END,COALESCE(r.checked,''),s.bead"
        ).fetchall()
    pending = False
    for value in sequence(fetched, "replay beads"):
        bead = string(row(value, 1)[0], "bead")
        if time.monotonic() >= deadline:
            break
        with store.connect() as connection:
            snapshot: object = connection.execute(
                "SELECT status,assignee,listed,native_missing FROM bead_snapshots WHERE bead=?",
                (bead,),
            ).fetchone()
            status, assignee, listed, missing = row(snapshot, 4)
            state = State(
                string(status, "status", empty=True),
                string(assignee, "assignee", empty=True),
            )
            prior: object = connection.execute(
                "SELECT fingerprint,stable,rebuild,deleted,renamed_to FROM bead_replays WHERE bead=?",
                (bead,),
            ).fetchone()
            old_hash, stable, rebuild, was_deleted, renamed = (
                (None, 0, 0, 0, None) if prior is None else row(prior, 5)
            )
            if renamed is not None:
                connection.execute(
                    "UPDATE bead_snapshots SET dirty=0 WHERE bead=?", (bead,)
                )
                continue
            values = cached(connection, bead)
            owners(connection, bead, values, state.assignee)
        # A failure requires a fresh complete native read on the next sweep;
        # merely re-reading unchanged local cache cannot certify a mismatch.
        rebuilt = False
        checked = datetime.now(UTC).isoformat()
        if rebuild and listed:
            if time.monotonic() >= deadline:
                pending |= integer(stable, "stable mismatch count") < 2
                continue
            try:
                with store.connect() as connection:
                    connection.execute(
                        "UPDATE bead_replays SET checked=? WHERE bead=?",
                        (checked, bead),
                    )
                fresh = read(
                    replace(
                        process, timeout=max(0.001, deadline - time.monotonic())
                    ).bead_events(bead)
                )
                if any(event.bead != bead for event in fresh):
                    raise HiveError(
                        ErrorCode.INVALID_RECORD, "Rebuild returned another bead"
                    )
                with store.connect() as connection:
                    connection.execute("DELETE FROM bead_events WHERE bead=?", (bead,))
                    save(connection, fresh)
                    owners(connection, bead, fresh, state.assignee)
                values, rebuilt = fresh, True
            except (HiveError, OSError):
                pending = True
                continue
        deleted = not listed and bool(missing)
        if deleted and prior is not None and (not values or was_deleted):
            with store.connect() as connection:
                connection.execute(
                    "UPDATE bead_replays SET deleted=1,checked=? WHERE bead=?",
                    (checked, bead),
                )
                connection.execute(
                    "UPDATE bead_snapshots SET dirty=0 WHERE bead=?", (bead,)
                )
            continue
        result = replay(values)
        reason = result.error
        if not listed and not deleted:
            reason = "Bead list and event history disagree"
        elif listed and reason is None and result.state != state:
            reason = "Final ownership state disagrees with bead list"
        digest = fingerprint(values)
        unchanged = old_hash == digest
        count = (
            max(1, integer(stable, "stable mismatch count") + int(rebuilt))
            if unchanged
            else 1
        )
        if reason is None:
            interval_status, count, need_rebuild = "known", 0, 0
        elif any(event.error for event in values) or not values:
            interval_status, count, need_rebuild = "unknown", 2, 1
        else:
            interval_status, need_rebuild = ("unknown" if count >= 2 else "pending"), 1
            pending |= count < 2
        with store.connect() as connection:
            connection.execute(
                "INSERT INTO bead_replays VALUES (?,?,?,?,?,?,?,NULL,?,?) ON CONFLICT(bead) DO UPDATE SET "
                "fingerprint=excluded.fingerprint,status=excluded.status,reason=excluded.reason,stable=excluded.stable,"
                "rebuild=excluded.rebuild,deleted=excluded.deleted,last_event=excluded.last_event,checked=excluded.checked",
                (
                    bead,
                    digest,
                    interval_status,
                    reason,
                    count,
                    need_rebuild,
                    int(deleted),
                    max((event.identity for event in values), default=""),
                    checked,
                ),
            )
            connection.execute(
                "UPDATE bead_snapshots SET unknown_reason=?,dirty=0 WHERE bead=?",
                (reason, bead),
            )
            if reason is None:
                connection.execute("DELETE FROM bead_intervals WHERE bead=?", (bead,))
                connection.executemany(
                    "INSERT INTO bead_intervals VALUES (?,?,?,?,?)",
                    [
                        (
                            bead,
                            index,
                            interval.thread,
                            interval.start.isoformat(),
                            None if interval.end is None else interval.end.isoformat(),
                        )
                        for index, interval in enumerate(result.intervals)
                    ],
                )
    with store.connect() as connection:
        # A cascade rename preserves event IDs. Drop old charges only after the
        # new ID has a certified replay extending the previously observed evidence.
        pairs: object = connection.execute(
            "SELECT a.bead,b.bead,a.fingerprint,a.last_event FROM bead_replays a JOIN bead_events e ON e.id=a.last_event "
            "JOIN bead_replays b ON b.bead=e.bead JOIN bead_snapshots s ON s.bead=b.bead WHERE a.deleted=1 AND a.renamed_to IS NULL "
            "AND b.status='known' AND b.deleted=0 AND s.listed=1 AND a.bead<>b.bead"
        ).fetchall()
        for pair in sequence(pairs, "renamed beads"):
            if time.monotonic() >= deadline:
                pending = True
                break
            old, new, old_fingerprint, last_event = row(pair, 4)
            new_values = cached(connection, string(new, "renamed bead"))
            prefix = tuple(
                event
                for event in new_values
                if event.identity <= string(last_event, "last event", empty=True)
            )
            if fingerprint(prefix) != old_fingerprint:
                continue
            before: object = connection.execute(
                "SELECT thread,start,end FROM bead_intervals WHERE bead=? ORDER BY ordinal",
                (old,),
            ).fetchall()
            after: object = connection.execute(
                "SELECT thread,start,end FROM bead_intervals WHERE bead=? ORDER BY ordinal",
                (new,),
            ).fetchall()
            old_intervals = sequence(before, "old intervals")
            new_intervals = sequence(after, "new intervals")
            compatible = all(
                any(
                    row(old_interval, 3)[:2] == row(new_interval, 3)[:2]
                    and (
                        row(old_interval, 3)[2] is None
                        or row(old_interval, 3)[2] == row(new_interval, 3)[2]
                    )
                    for new_interval in new_intervals
                )
                for old_interval in old_intervals
            )
            if compatible:
                connection.execute("DELETE FROM bead_intervals WHERE bead=?", (old,))
                connection.execute(
                    "UPDATE bead_replays SET status='renamed',renamed_to=? WHERE bead=?",
                    (new, old),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO bead_seen_owners SELECT ?,thread FROM bead_seen_owners WHERE bead=?",
                    (new, old),
                )
        remaining: object = connection.execute(
            "SELECT COUNT(*) FROM bead_snapshots s LEFT JOIN bead_replays r ON s.bead=r.bead "
            "WHERE (s.dirty=1 AND (r.status IS NULL OR r.status<>'renamed')) OR r.status='pending'"
        ).fetchone()
        pending |= integer(row(remaining, 1)[0], "pending intervals") > 0
    return not pending


def reset(store: UsageStore) -> dict[str, object]:
    from hive.bead_queries import FIRST
    from hive.locking import file_lock

    with (
        file_lock(store.path.parent / "bead-history.lock", timeout=0),
        store.connect() as connection,
    ):
        connection.execute(
            "UPDATE bead_event_cursor SET after_id=?,newest=?,caught_up=0,error=NULL",
            (FIRST, FIRST),
        )
        connection.execute(
            "UPDATE bead_replays SET rebuild=1,stable=0,status=CASE WHEN renamed_to IS NULL AND deleted=0 THEN 'pending' ELSE status END"
        )
        connection.execute("UPDATE bead_snapshots SET rebuilt=0,dirty=1")
    return {"code": "BeadEventsCursorReset", "bead_events_caught_up": False}
