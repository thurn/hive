"""Incremental session contributions make paged feeds independent of transcript size."""

import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime

from hive.bead_assignment import Evidence
from hive.cost_report import report as price_report
from hive.dashboard_accounting import Fact, facts, project, unattributable_projects
from hive.dashboard_values import packed, rows
from hive.identity import ThreadId
from hive.jsonvalue import integer, parse, record, sequence, string
from hive.launch_context import LaunchContext
from hive.tollgate_observation import candidates
from hive.usage_store import UsageStore


def counts(raw: object) -> Counter[str]:
    value = record(parse(string(raw, "counter")))
    return Counter(
        {key: int(string(amount, "picodollars")) for key, amount in value.items()}
    )


def refresh_card(connection: sqlite3.Connection, key: str) -> None:
    contributions = rows(
        connection, "SELECT * FROM dashboard_contributions WHERE key=?", (key,)
    )
    bead = key.removeprefix("bead:") if key.startswith("bead:") else None
    native = (
        rows(connection, "SELECT * FROM bead_rows WHERE bead=?", (bead,))
        if bead
        else []
    )
    cached = (
        rows(connection, "SELECT * FROM bead_replays WHERE bead=?", (bead,))
        if bead
        else []
    )
    if (cached and cached[0]["renamed_to"] is not None) or (
        not contributions and not native and not cached
    ):
        connection.execute("DELETE FROM card_summaries WHERE key=?", (key,))
        return
    kind = "bead" if bead else string(contributions[0]["kind"], "card kind")
    group = (
        string(native[0]["project"], "project")
        if native
        else (
            string(contributions[0]["project"], "project") if contributions else "Other"
        )
    )
    amount = sum(int(string(c["amount_picos"], "amount")) for c in contributions)
    roles: Counter[str] = Counter()
    badges: Counter[str] = Counter()
    for contribution in contributions:
        roles.update(counts(contribution["roles"]))
        badges.update(counts(contribution["badges"]))
    diagnostic_rows = rows(
        connection,
        "SELECT d.payload FROM dashboard_diagnostic_sets d WHERE d.thread IN (SELECT thread FROM dashboard_contributions WHERE key=?)",
        (key,),
    )
    diagnostic_activity = []
    for value in diagnostic_rows:
        snapshot = record(parse(string(value["payload"], "diagnostics")))
        entries = record(snapshot.get("cards", {}))
        if key in entries:
            entry = record(entries[key])
            badges.update(
                {k: integer(v, "badge") for k, v in record(entry["badges"]).items()}
            )
            diagnostic_activity.append(string(entry["at"], "diagnostic activity"))
    activity = max(
        [string(c["last_activity"], "activity") for c in contributions]
        + [string(c["updated"], "updated") for c in native]
        + diagnostic_activity
        + ["1970-01-01T00:00:00+00:00"]
    )
    ci = rows(connection, "SELECT state,updated FROM card_ci WHERE key=?", (key,))
    badges["ci_failures"] = sum(
        v["state"]
        in {
            "failed",
            "merge-conflict",
            "dependency-failed",
            "infrastructure-exhausted",
            "check-failed",
        }
        for v in ci
    )
    activity = max([activity] + [string(v["updated"], "CI activity") for v in ci])
    connection.execute(
        "INSERT INTO card_summaries VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET kind=excluded.kind,project=excluded.project,amount_picos=excluded.amount_picos,unpriced=excluded.unpriced,coverage=excluded.coverage,last_activity=excluded.last_activity,roles=excluded.roles,badges=excluded.badges",
        (
            key,
            kind,
            group,
            str(amount),
            sum(integer(c["unpriced"], "unpriced") for c in contributions),
            int(any(c["coverage"] for c in contributions)),
            activity,
            packed({k: str(v) for k, v in roles.items()}),
            packed({k: str(v) for k, v in badges.items()}),
        ),
    )


def session(
    connection: sqlite3.Connection, thread: str, coverage: bool, *, staged: bool = False
) -> None:
    evidence: Evidence = Evidence.read(connection)
    observed: tuple[Fact, ...] = (
        () if staged else facts(connection, thread, evidence=evidence)
    )
    group = project(connection, thread)
    owning = any(
        i.thread == thread
        or (
            rows(
                connection,
                "SELECT 1 AS found FROM codex_roots WHERE thread=? AND root=?",
                (i.thread, thread),
            )
        )
        for i in evidence.intervals
    )
    unowned = sum(
        f.request.amount or 0 for f in observed if f.assignment.kind == "unowned"
    )
    total = sum(f.request.amount or 0 for f in observed)
    publication = 0
    generation = 0
    if staged:
        run = rows(
            connection,
            "SELECT total,unowned,publication,version FROM dashboard_runs WHERE thread=?",
            (thread,),
        )[0]
        publication = integer(run["publication"], "publication")
        generation = integer(run["version"], "generation")
        total, unowned = int(string(run["total"], "total")), int(
            string(run["unowned"], "unowned")
        )
    tail = owning and unowned > 0 and (unowned >= 10**12 or unowned * 4 >= total)
    session_key: str = (
        f"session:{thread}" if not owning or tail else f"ledger:small_tails:{group}"
    )
    session_kind = "tail" if tail else "agent" if not owning else "small_tails"
    amounts: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    roles: dict[str, Counter[str]] = {}
    badges: dict[str, Counter[str]] = {}
    activities: dict[str, str] = {}
    kinds: dict[str, str] = {}
    projects: dict[str, str] = {}
    old = {
        string(v["key"], "key")
        for v in rows(
            connection,
            "SELECT key FROM dashboard_contributions WHERE thread=?",
            (thread,),
        )
    }
    connection.execute(
        "DELETE FROM dashboard_stable_requests WHERE thread=?", (thread,)
    )
    if not staged:
        connection.execute(
            "INSERT OR IGNORE INTO dashboard_garbage SELECT thread,generation FROM dashboard_active WHERE thread=?",
            (thread,),
        )
        connection.execute("DELETE FROM dashboard_active WHERE thread=?", (thread,))
    for fact in observed:
        request = fact.request
        shares: list[tuple[str, int]] = []
        if fact.assignment.kind in {"owned", "shared"}:
            shares = [
                ("bead:" + interval.bead, share)
                for interval, share in fact.assignment.shares
            ]
        elif fact.assignment.kind == "unowned":
            shares = [(session_key, request.amount or 0)]
        else:
            destinations = unattributable_projects(connection, evidence, fact, group)
            numerator: int = request.amount or 0
            quotient = numerator // len(destinations)
            remainder = numerator % len(destinations)
            shares = [
                (f"ledger:unattributable:{destination}", quotient + int(n < remainder))
                for n, destination in enumerate(destinations)
            ]
        for key, amount in shares:
            amounts[key] += amount
            missing[key] += int(request.amount is None)
            roles.setdefault(key, Counter())[fact.role] += amount
            activities[key] = max(activities.get(key, ""), request.at.isoformat())
            kinds[key] = (
                "bead"
                if key.startswith("bead:")
                else (
                    "unattributable"
                    if key.startswith("ledger:unattributable:")
                    else session_kind
                )
            )
            projects[key] = key.split(":", 2)[2] if key.startswith("ledger:") else group
        connection.execute(
            "INSERT INTO dashboard_stable_requests VALUES (?,?,?,?,?,?,?,?,?)",
            (
                request.response,
                thread,
                request.at.isoformat(),
                fact.role,
                None if request.amount is None else str(request.amount),
                fact.assignment.kind,
                packed(
                    [
                        {"key": key, "amount_picos": str(amount)}
                        for key, amount in shares
                    ]
                ),
                group,
                int(coverage),
            ),
        )
    if staged:
        for value in rows(
            connection,
            "SELECT * FROM dashboard_stage_contributions WHERE thread=?",
            (thread,),
        ):
            original = string(value["key"], "key")
            key = session_key if original == "session:" + thread else original
            amounts[key] += int(string(value["amount_picos"], "amount"))
            missing[key] += integer(value["unpriced"], "unpriced")
            roles.setdefault(key, Counter()).update(counts(value["roles"]))
            activities[key] = string(value["last_activity"], "activity")
            kinds[key] = (
                session_kind
                if original == "session:" + thread
                else string(value["kind"], "kind")
            )
            projects[key] = key.split(":", 2)[2] if key.startswith("ledger:") else group
        connection.execute(
            "INSERT OR IGNORE INTO dashboard_garbage SELECT thread,generation FROM dashboard_active WHERE thread=? AND generation<>?",
            (thread, publication),
        )
        connection.execute(
            "INSERT INTO dashboard_active VALUES (?,?,?,?) ON CONFLICT(thread) DO UPDATE SET generation=excluded.generation,unowned_key=excluded.unowned_key,version=excluded.version",
            (thread, publication, session_key, generation),
        )
    # Empty observed sessions remain navigable but carry no invented spend.
    if (
        not owning
        and rows(
            connection,
            "SELECT 1 AS found FROM sources WHERE task=? LIMIT 1",
            (thread,),
        )
        and session_key not in amounts
    ):
        amounts[session_key] = 0
        kinds[session_key], projects[session_key] = session_kind, group
        activities[session_key] = "1970-01-01T00:00:00+00:00"
    # Claims and diagnostics can predate the first priced request.
    for interval in evidence.intervals:
        if interval.thread == thread or rows(
            connection,
            "SELECT 1 AS found FROM codex_roots WHERE thread=? AND root=?",
            (interval.thread, thread),
        ):
            key = "bead:" + interval.bead
            amounts[key] += 0
            kinds[key], projects[key] = "bead", group
            activities[key] = max(activities.get(key, ""), interval.start.isoformat())
    connection.execute("DELETE FROM dashboard_contributions WHERE thread=?", (thread,))
    for key, amount in amounts.items():
        connection.execute(
            "INSERT INTO dashboard_contributions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                thread,
                key,
                kinds[key],
                projects[key],
                str(amount),
                missing[key],
                int(coverage),
                activities[key],
                packed({k: str(v) for k, v in roles.get(key, {}).items()}),
                packed({k: str(v) for k, v in badges.get(key, {}).items()}),
            ),
        )
    for key in old | set(amounts):
        refresh_card(connection, key)
    connection.execute(
        "INSERT OR IGNORE INTO dashboard_ci_dirty SELECT candidate FROM tollgate_mentions WHERE thread=?",
        (thread,),
    )
    connection.execute(
        "INSERT OR IGNORE INTO dashboard_diagnostic_dirty VALUES (?,1)", (thread,)
    )
    if not staged:
        connection.execute("DELETE FROM dashboard_changes WHERE thread=?", (thread,))
    connection.execute(
        "DELETE FROM dashboard_dirty WHERE thread=? AND NOT EXISTS (SELECT 1 FROM dashboard_changes WHERE thread=?)",
        (thread, thread),
    )


def status(connection: sqlite3.Connection) -> dict[str, object]:
    values = rows(connection, "SELECT * FROM dashboard_health WHERE singleton=1")
    value = values[0]
    behind = bool(
        rows(connection, "SELECT thread FROM dashboard_dirty LIMIT 1")
        or rows(connection, "SELECT bead FROM dashboard_bead_dirty LIMIT 1")
        or rows(connection, "SELECT candidate FROM dashboard_ci_dirty LIMIT 1")
        or rows(connection, "SELECT key FROM dashboard_card_dirty LIMIT 1")
        or rows(connection, "SELECT thread FROM dashboard_diagnostic_dirty LIMIT 1")
    )
    return dict(
        summaries_behind=behind,
        summaries_refreshed=value["refreshed"],
        summaries_error=value["error"],
    )


def refresh(
    store: UsageStore, context: LaunchContext, deadline: float
) -> dict[str, object]:
    with store.connect() as connection:
        if time.monotonic() >= deadline:
            return status(connection)
        health = rows(
            connection,
            "SELECT source,attribution FROM dashboard_health WHERE singleton=1",
        )[0]
        current = rows(
            connection,
            "SELECT caught_up,error FROM bead_event_cursor WHERE singleton=1",
        )
        attribution = packed(current)
        if health["source"] != context.commit or health["attribution"] != attribution:
            connection.execute(
                "INSERT OR IGNORE INTO dashboard_dirty SELECT task FROM collection_tasks UNION SELECT task FROM responses UNION SELECT task FROM claude_request_events"
            )
            connection.execute(
                "INSERT OR IGNORE INTO dashboard_bead_dirty SELECT bead FROM bead_rows"
            )
            connection.execute(
                "INSERT OR IGNORE INTO dashboard_garbage SELECT thread,publication FROM dashboard_runs"
            )
            connection.execute("DELETE FROM dashboard_runs")
            connection.execute(
                "INSERT INTO dashboard_versions SELECT thread,1 FROM dashboard_dirty WHERE 1 ON CONFLICT(thread) DO UPDATE SET version=version+1"
            )
            connection.execute(
                "UPDATE dashboard_health SET source=?,attribution=? WHERE singleton=1",
                (context.commit, attribution),
            )
        tasks = rows(
            connection, "SELECT thread FROM dashboard_dirty ORDER BY rowid LIMIT 32"
        )
    for value in tasks:
        if time.monotonic() >= deadline - 0.04:
            break
        thread = string(value["thread"], "thread")
        from hive.dashboard_incremental import advance, large

        with store.connect(write=False) as connection:
            paged = large(connection, thread)
        if paged:
            advance(store, thread, min(deadline - 0.04, time.monotonic() + 0.05))
            # Unfinished roots rotate instead of monopolizing the next sweep.
            with store.connect() as connection:
                pending = rows(
                    connection,
                    "SELECT thread FROM dashboard_dirty WHERE thread=?",
                    (thread,),
                )
                if pending:
                    connection.execute(
                        "DELETE FROM dashboard_dirty WHERE thread=?", (thread,)
                    )
                    connection.execute(
                        "INSERT INTO dashboard_dirty VALUES (?)", (thread,)
                    )
            continue
        report = price_report(store, ThreadId(thread))
        with store.connect() as connection:
            session(connection, thread, report.get("complete_estimate_usd") is None)
    from hive.dashboard_diagnostics import refresh as refresh_diagnostics

    refresh_diagnostics(store, min(deadline, time.monotonic() + 0.05))
    with store.connect() as connection:
        if time.monotonic() < deadline:
            from hive.dashboard_incremental import cleanup

            cleanup(connection)
        if time.monotonic() < deadline:
            refresh_ci(connection, deadline)
        for value in rows(
            connection, "SELECT bead FROM dashboard_bead_dirty ORDER BY bead LIMIT 128"
        ):
            if time.monotonic() >= deadline:
                break
            bead = string(value["bead"], "bead")
            refresh_card(connection, "bead:" + bead)
            connection.execute("DELETE FROM dashboard_bead_dirty WHERE bead=?", (bead,))
        for value in rows(
            connection, "SELECT key FROM dashboard_card_dirty ORDER BY rowid LIMIT 128"
        ):
            if time.monotonic() >= deadline:
                break
            key = string(value["key"], "key")
            refresh_card(connection, key)
            connection.execute("DELETE FROM dashboard_card_dirty WHERE key=?", (key,))
        connection.execute(
            "UPDATE dashboard_health SET refreshed=? WHERE singleton=1",
            (datetime.now(UTC).isoformat(),),
        )
        return status(connection)


def refresh_ci(connection: sqlite3.Connection, deadline: float = float("inf")) -> None:
    for queued in rows(
        connection, "SELECT candidate FROM dashboard_ci_dirty ORDER BY rowid LIMIT 32"
    ):
        if time.monotonic() >= deadline:
            return
        identity = string(queued["candidate"], "candidate")
        keys = {
            string(v["key"], "key")
            for v in rows(
                connection, "SELECT key FROM card_ci WHERE candidate=?", (identity,)
            )
        }
        connection.execute("DELETE FROM card_ci WHERE candidate=?", (identity,))
        for candidate in candidates(connection, identity):
            for raw_link in sequence(candidate["links"], "links"):
                link = record(raw_link)
                targets = [
                    "bead:" + string(b, "bead")
                    for b in sequence(link["beads"], "beads")
                ]
                if link["thread"] is not None:
                    targets.append("session:" + string(link["thread"], "thread"))
                for key in targets:
                    keys.add(key)
                    connection.execute(
                        "INSERT OR IGNORE INTO card_ci VALUES (?,?,?,?)",
                        (key, identity, candidate["state"], candidate["updated_at"]),
                    )
        for key in keys:
            if rows(connection, "SELECT key FROM card_summaries WHERE key=?", (key,)):
                refresh_card(connection, key)
        connection.execute(
            "DELETE FROM dashboard_ci_dirty WHERE candidate=?", (identity,)
        )
