"""On-demand detail shares exactly the feed's retained-price assignments."""

import sqlite3
from datetime import timedelta

from hive.bead_assignment import Evidence
from hive.dashboard_accounting import Fact, bead_share, breakdown, facts
from hive.dashboard_values import cursor, rows, uncursor
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, record, sequence, string
from hive.pricing import dollars
from hive.tollgate_observation import candidates


def selected(
    connection: sqlite3.Connection, key: str
) -> tuple[tuple[Fact, int | None], ...]:
    if key.startswith("bead:"):
        evidence = Evidence.read(connection)
        bead = key[5:]
        threads = {i.thread for i in evidence.intervals if i.bead == bead}
        roots = {
            string(v["root"], "root")
            for t in threads
            for v in rows(
                connection, "SELECT root FROM codex_roots WHERE thread=?", (t,)
            )
        }
        return tuple(
            (f, bead_share(f, bead))
            for task in sorted(threads | roots)
            for f in facts(connection, task, evidence=evidence)
            if any(i.bead == bead for i, _ in f.assignment.shares)
        )
    threads = [
        string(v["thread"], "thread")
        for v in rows(
            connection, "SELECT thread FROM dashboard_contributions WHERE key=?", (key,)
        )
    ]
    shares: dict[str, int | None] = {}
    for thread in threads:
        for value in rows(
            connection,
            "SELECT response,amount_picos,shares FROM dashboard_requests WHERE thread=?",
            (thread,),
        ):
            total = 0
            matches = False
            for raw in sequence(parse(string(value["shares"], "shares")), "shares"):
                share = record(raw)
                if share["key"] == key:
                    total += int(string(share["amount_picos"], "amount"))
                    matches = True
            if matches:
                shares[string(value["response"], "response")] = (
                    total if value["amount_picos"] is not None else None
                )
    evidence = Evidence.read(connection)
    return tuple(
        (fact, shares[fact.request.response])
        for thread in threads
        for fact in facts(connection, thread, evidence=evidence)
        if fact.request.response in shares
    )


def page(
    connection: sqlite3.Connection, key: str, token: str | None, *, sort: str = "time"
) -> dict[str, object]:
    if sort not in {"time", "share"}:
        raise HiveError(ErrorCode.INVALID_INPUT, "Request sort must be time or share")
    values = selected(connection, key)

    def order(pair: tuple[Fact, int | None]) -> tuple[int | str, str]:
        fact, amount = pair
        return (
            -(amount or 0) if sort == "share" else fact.request.at.isoformat(),
            fact.request.response,
        )

    ordered = sorted(values, key=order)
    if token:
        after = uncursor(token)
        if after.get("key") != key or after.get("sort") != sort:
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Cursor belongs to another request list"
            )
        identity = string(after.get("response"), "response")
        anchor = next(
            (order(v) for v in ordered if v[0].request.response == identity), None
        )
        if anchor is None:
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Request cursor is no longer present"
            )
        ordered = [v for v in ordered if order(v) > anchor]
    output = [f.public(amount) for f, amount in ordered[:500]]
    return record(
        dict[str, object](
            requests=output,
            next_cursor=(
                cursor(
                    dict[str, object](
                        key=key, sort=sort, response=output[-1]["response"]
                    )
                )
                if len(ordered) > 500
                else None
            ),
        )
    )


def detail(
    connection: sqlite3.Connection, key: str, *, whole_session: bool = False
) -> dict[str, object]:
    from hive.bead_requests import Request
    from hive.diagnostic_report import events, tools
    from hive.identity import Host, ThreadId
    from hive.usage import timestamp

    cards = rows(connection, "SELECT * FROM card_summaries WHERE key=?", (key,))
    if not cards:
        raise HiveError(ErrorCode.NOT_FOUND, "Dashboard card not found")
    card: dict[str, object] = cards[0]
    observations = selected(connection, key)
    if whole_session and key.startswith("session:"):
        observations = tuple((f, f.request.amount) for f in facts(connection, key[8:]))
    amount = sum(a or 0 for _, a in observations)
    card["amount_picos"] = str(amount)
    tasks: set[str] = {f.request.thread for f, _ in observations}
    tasks.update(
        string(v["thread"], "thread")
        for v in rows(
            connection, "SELECT thread FROM dashboard_contributions WHERE key=?", (key,)
        )
    )
    evidence: Evidence = Evidence.read(connection)
    intervals = [
        i
        for i in evidence.intervals
        if (key.startswith("bead:") and i.bead == key[5:])
        or (not key.startswith("bead:") and i.thread in tasks)
    ]
    context: list[dict[str, object]] = []
    if key.startswith("bead:"):
        for task in sorted(tasks):
            for fact in facts(connection, task, evidence=evidence):
                if fact.assignment.kind != "unowned":
                    continue
                distances = [
                    (abs(fact.request.at - boundary), i.bead)
                    for i in evidence.intervals
                    if i.thread == task
                    for boundary in (i.start, i.end)
                    if boundary is not None
                ]
                if distances:
                    distance, bead = min(distances)
                    if distance <= timedelta(hours=2) and bead == key[5:]:
                        context.append(fact.public(0))

    def relevant(item: dict[str, object], at: str) -> bool:
        assignment = evidence.assign(
            Request(
                "diagnostic",
                ThreadId(string(item["thread"], "thread")),
                Host(string(item["host"], "host")),
                timestamp(item[at]),
                None,
                string(item["agent"], "agent", empty=True) or None,
                None,
                None,
                "diagnostic",
                0,
            )
        )
        if key.startswith("bead:"):
            return any(i.bead == key[5:] for i, _ in assignment.shares)
        if card["kind"] == "tail" and not whole_session:
            return assignment.kind == "unowned"
        return True

    diagnostics: list[dict[str, object]] = []
    for task in sorted(tasks):
        diagnostics.extend(
            {
                k: v
                for k, v in c.items()
                if k not in {"file", "use_offset", "result_offset"}
            }
            for c in tools(connection, task)
            if relevant(c, "started_at")
        )
        diagnostics.extend(
            {k: v for k, v in e.items() if k not in {"file", "offset"}}
            for e in events(connection, task)
            if relevant(e, "at")
        )
    ci = []
    for candidate in candidates(connection):
        if any(
            (
                key.startswith("bead:")
                and key[5:] in sequence(record(link)["beads"], "beads")
            )
            or (not key.startswith("bead:") and record(link)["thread"] in tasks)
            for link in sequence(candidate["links"], "links")
        ):
            ci.append(candidate)
    spans = [
        v
        for task in sorted(tasks)
        for v in rows(
            connection,
            "SELECT * FROM role_spans WHERE thread=? ORDER BY start,agent",
            (task,),
        )
    ]
    created = [
        v
        for task in sorted(tasks)
        for v in rows(
            connection,
            "SELECT bead,relation FROM collection_links WHERE task=? AND relation='creator'",
            (task,),
        )
    ]
    dimensions = breakdown(connection, observations)
    return dict[str, object](
        card=card,
        amount_picos=card["amount_picos"],
        usd=dollars(int(string(card["amount_picos"], "amount"))),
        timeline=[
            dict[str, object](
                response=f.request.response,
                at=f.request.at.isoformat(),
                thread=f.request.thread,
                agent=f.request.agent,
                role=f.role,
                amount_picos=None if amount is None else str(amount),
            )
            for f, amount in observations
        ],
        intervals=[
            dict[str, object](
                bead=i.bead,
                thread=i.thread,
                start=i.start.isoformat(),
                end=i.end.isoformat() if i.end else None,
                deleted=i.deleted,
            )
            for i in intervals
        ],
        context_requests=context,
        breakdown=dimensions,
        diagnostics=diagnostics,
        candidates=ci,
        role_spans=spans,
        subagents=dimensions["subagent"],
        created_beads=created,
        contributors=rows(
            connection,
            "SELECT thread,amount_picos,coverage FROM dashboard_contributions WHERE key=? ORDER BY thread",
            (key,),
        ),
    )
