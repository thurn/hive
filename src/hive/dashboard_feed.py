"""Materialized lifetime cards, exact window totals and stable keyset pagination."""

import hashlib
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta

from hive.dashboard_states import activity, fallback_time, state
from hive.dashboard_summary import counts, status
from hive.dashboard_values import Filters, cursor, packed, rows, uncursor, window_start
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, record, sequence, string
from hive.pricing import dollars


def cards(connection: sqlite3.Connection, now: datetime) -> list[dict[str, object]]:
    modified = activity(connection, now)
    owners: dict[str, set[str]] = {}
    active_owners: dict[str, set[str]] = {}
    for item in rows(
        connection,
        "SELECT i.bead,i.end,COALESCE(r.root,i.thread) AS thread FROM bead_intervals i LEFT JOIN codex_roots r ON r.thread=i.thread",
    ):
        owners.setdefault("bead:" + string(item["bead"], "bead"), set()).add(
            string(item["thread"], "thread")
        )
        if item["end"] is None:
            active_owners.setdefault("bead:" + string(item["bead"], "bead"), set()).add(
                string(item["thread"], "thread")
            )
    latest_ci: dict[str, str] = {}
    for item in rows(
        connection, "SELECT key,state FROM card_ci ORDER BY updated,candidate"
    ):
        latest_ci[string(item["key"], "key")] = string(item["state"], "state")
    result = rows(
        connection,
        # Keep the joined primary keys bare so SQLite can use their indexes;
        # concatenating them scans every bead for every card on each feed read.
        "SELECT c.*,b.bead,b.title,b.status,b.subtitle,json_extract(b.payload,'$.issue_type') AS issue_type,b.created,b.closed,b.assignee,b.blockers,b.resolution,b.deferred_until,r.status AS interval_status,r.deleted FROM card_summaries c LEFT JOIN bead_rows b ON b.bead=substr(c.key,6) AND substr(c.key,1,5)='bead:' LEFT JOIN bead_replays r ON r.bead=substr(c.key,6) AND substr(c.key,1,5)='bead:' ORDER BY c.last_activity DESC,c.key",
    )
    primary_roles = {}
    for span in rows(
        connection, "SELECT thread,role FROM role_spans WHERE agent='' ORDER BY start"
    ):
        primary_roles.setdefault(string(span["thread"], "thread"), span["role"])
    titles = {
        string(v["thread"], "thread"): v["title"]
        for v in rows(connection, "SELECT thread,title FROM project_sessions")
    }
    for card in result:
        key = string(card["key"], "key")
        thread = key.removeprefix("session:") if key.startswith("session:") else None
        tasks = owners.get(key, set()) if thread is None else {thread}
        if card.get("issue_type") == "epic":
            card["subtitle"] = "Epic · open detail for descendant progress"
        card["owners"] = sorted(tasks)
        card["thread"] = thread
        card["primary_role"] = primary_roles.get(thread or "", "unknown")
        card["title"] = (
            card["title"]
            or (titles.get(thread) if thread else None)
            or (
                "Small unowned tails"
                if card["kind"] == "small_tails"
                else "Unattributable spend" if card["kind"] == "unattributable" else key
            )
        )
        card["roles"] = {k: str(v) for k, v in counts(card["roles"]).items()}
        card["badges"] = dict[str, object](counts(card["badges"]))
        card["usd"] = dollars(int(string(card["amount_picos"], "amount")))
        activity_tasks = active_owners.get(key, set()) if thread is None else tasks
        last = max(
            [modified.get(t, 0) for t in activity_tasks] or [0]
        ) or fallback_time(card["last_activity"])
        card["idle_seconds"] = max(0, int(now.timestamp() - last))
        card["state"] = state(card, now, last, latest_ci.get(key))
    return result


def window(
    connection: sqlite3.Connection, filters: Filters
) -> tuple[dict[str, object], list[dict[str, object]]]:
    start, zone = window_start(filters.window)
    selected = rows(
        connection,
        "SELECT d.*,r.agent FROM dashboard_requests d LEFT JOIN responses r ON r.response=d.response WHERE d.observed>=? ORDER BY d.observed,d.response",
        (start,),
    )
    roles: Counter[str] = Counter()
    total = missing = incomplete = 0
    retained: list[dict[str, object]] = []
    groups = {
        string(v["key"], "key"): v["project"]
        for v in rows(connection, "SELECT key,project FROM card_summaries")
    }
    for value in selected:
        shares = [
            record(v)
            for v in sequence(parse(string(value["shares"], "shares")), "shares")
        ]
        if filters.project:
            shares = [
                s
                for s in shares
                if groups.get(string(s["key"], "key")) == filters.project
            ]
            if not shares:
                continue
        if filters.role and value["role"] != filters.role:
            continue
        amount = sum(int(string(s["amount_picos"], "amount")) for s in shares)
        if value["amount_picos"] is not None:
            value["amount_picos"] = str(amount)
        total += amount
        missing += int(value["amount_picos"] is None)
        incomplete += amount if value["coverage"] else 0
        roles[string(value["role"], "role")] += amount
        value["shares"] = shares
        retained.append(value)
    return (
        dict[str, object](
            window=filters.window,
            start=start,
            timezone=zone,
            amount_picos=str(total),
            usd=dollars(total),
            roles=[
                dict[str, object](
                    role=role, amount_picos=str(amount), usd=dollars(amount)
                )
                for role, amount in sorted(roles.items())
            ],
            unpriced=missing,
            incomplete_picos=str(incomplete),
        ),
        retained,
    )


def feed(connection: sqlite3.Connection, filters: Filters) -> dict[str, object]:
    from hive.dashboard_hotspots import hotspots

    now = datetime.now(UTC)
    all_cards = cards(connection, now)
    summary, requests = window(connection, filters)
    selected = []
    query: dict[str, object] = dict[str, object](
        window=filters.window,
        project=filters.project,
        role=filters.role,
        state=filters.state,
        q=filters.q,
        active=filters.active,
        older_completed=filters.older_completed,
    )
    after = uncursor(filters.cursor) if filters.cursor else None
    if after is not None and after.get("query") != query:
        raise HiveError(ErrorCode.INVALID_INPUT, "Cursor belongs to another feed")
    for card in all_cards:
        if filters.project and card["project"] != filters.project:
            continue
        if filters.role and filters.role not in record(card["roles"]):
            continue
        if filters.state and card["state"] != filters.state:
            continue
        if (
            filters.q.casefold()
            not in (
                str(card["title"])
                + " "
                + str(card["key"])
                + " "
                + str(card["subtitle"])
            ).casefold()
        ):
            continue
        if filters.active and str(card["last_activity"]) < str(summary["start"]):
            continue
        if (
            not filters.older_completed
            and card["status"] == "closed"
            and fallback_time(card["closed"]) < (now - timedelta(days=7)).timestamp()
        ):
            continue
        if after is not None:
            last, key = (
                string(after.get("last"), "cursor activity"),
                string(after.get("key"), "cursor key"),
            )
            if str(card["last_activity"]) > last or (
                card["last_activity"] == last and str(card["key"]) <= key
            ):
                continue
        selected.append(card)
    page = selected[:50]
    from hive.dashboard_titles import title as native_title

    for card in page:
        if card["thread"] is not None and card["title"] == card["key"]:
            card["title"] = (
                native_title(connection, string(card["thread"], "thread"))
                or card["title"]
            )
    body: dict[str, object] = dict[str, object](
        cards=page,
        projects=[
            dict[str, object](
                id=group, count=sum(c["project"] == group for c in all_cards)
            )
            for group in sorted({string(c["project"], "project") for c in all_cards})
        ],
        summary=summary,
        hotspots=hotspots(connection, all_cards, summary, requests),
        next_cursor=(
            cursor(
                dict[str, object](
                    query=query, last=page[-1]["last_activity"], key=page[-1]["key"]
                )
            )
            if len(selected) > 50
            else None
        ),
        **status(connection),
        collector=freshness(connection),
    )
    # Freshness is reported independently; only visible content affects the revision.
    revision_body = {
        k: v
        for k, v in body.items()
        if not k.startswith("summaries_") and k != "collector"
    }
    for card in all_cards:
        card.pop("idle_seconds", None)
    revision_body["all_cards"] = all_cards
    body["revision"] = hashlib.sha256(packed(revision_body).encode()).hexdigest()
    return body


def freshness(connection: sqlite3.Connection) -> dict[str, object]:
    from hive.bead_history import status as bead_status
    from hive.codex_discovery import state as discovery_state
    from hive.tollgate_observation import status as ci_status

    health = rows(
        connection, "SELECT refreshed,error FROM collection_health WHERE singleton=1"
    )
    return {
        **status(connection),
        **bead_status(connection),
        **ci_status(connection),
        "registry": health[0] if health else None,
        "discovery_behind": discovery_state(connection, "behind", "1") == "1",
        "discovery_error": discovery_state(connection, "error") or None,
    }
