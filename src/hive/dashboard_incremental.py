"""Large roots rebuild in resumable pages while their last complete cards stay visible."""

import sqlite3
import time
from collections import Counter

from hive.bead_assignment import Evidence
from hive.claude_usage import Modifiers
from hive.cost_report import price_response
from hive.dashboard_accounting import facts, project, unattributable_projects
from hive.dashboard_values import packed, rows
from hive.identity import Host, PricingTier, ResponseId
from hive.jsonvalue import integer, parse, record, sequence, string
from hive.usage_store import UsageStore

PAGE = 128


def large(connection: sqlite3.Connection, thread: str) -> bool:
    return bool(
        rows(
            connection,
            "SELECT response FROM responses WHERE task=? LIMIT 1 OFFSET 256",
            (thread,),
        )
        or rows(
            connection,
            "SELECT response FROM claude_request_events WHERE task=? LIMIT 1 OFFSET 256",
            (thread,),
        )
        or rows(
            connection, "SELECT thread FROM dashboard_runs WHERE thread=?", (thread,)
        )
    )


def version(connection: sqlite3.Connection, thread: str) -> int:
    values = rows(
        connection, "SELECT version FROM dashboard_versions WHERE thread=?", (thread,)
    )
    return integer(values[0]["version"], "generation") if values else 0


def price(connection: sqlite3.Connection, identities: tuple[str, ...]) -> None:
    fresh: list[tuple[str, str, str]] = []
    for value in rows(
        connection,
        "SELECT * FROM request_detail WHERE (tier IS NULL OR tier='standard') AND response IN ("
        + ",".join("?" for _ in identities)
        + ") ORDER BY response",
        identities,
    ):
        if value["_quote"] is not None:
            continue
        host = Host(string(value["host"], "host"))
        price_response(
            fresh,
            ResponseId(string(value["response"], "response")),
            value["_usage"],
            value["model"],
            value["_conflicted"],
            None,
            host,
            (
                Modifiers.read(parse(string(value["_modifiers"], "modifiers")))
                if host == Host.CLAUDE
                else None
            ),
            tuple(
                string(v, "flag")
                for v in sequence(parse(string(value["flags"], "flags")), "flags")
            ),
            PricingTier.STANDARD,
        )
    # This exclusive transaction cannot race streamed usage. Use the shared
    # quote builder and keep first-observation evidence; no whole-root pricing.
    connection.executemany(
        "INSERT OR IGNORE INTO response_estimates VALUES (?,?,?)", fresh
    )


def batch(connection: sqlite3.Connection, thread: str) -> bool:
    from hive.dashboard_summary import counts, session

    generation = version(connection, thread)
    jobs = rows(connection, "SELECT * FROM dashboard_runs WHERE thread=?", (thread,))
    active = rows(
        connection, "SELECT * FROM dashboard_active WHERE thread=?", (thread,)
    )
    delta = not jobs and bool(active) and active[0]["version"] == generation
    if delta:
        publication = active[0]["generation"]
        connection.execute(
            "DELETE FROM dashboard_stage_contributions WHERE thread=?", (thread,)
        )
        connection.execute(
            "INSERT INTO dashboard_stage_contributions SELECT thread,CASE WHEN kind IN ('agent','tail','small_tails') THEN 'session:'||thread ELSE key END,CASE WHEN kind IN ('agent','tail','small_tails') THEN 'agent' ELSE kind END,project,amount_picos,unpriced,coverage,last_activity,roles,badges FROM dashboard_contributions WHERE thread=?",
            (thread,),
        )
        totals = rows(
            connection,
            "SELECT kind,amount_picos FROM dashboard_contributions WHERE thread=?",
            (thread,),
        )
        total = sum(int(string(v["amount_picos"], "amount")) for v in totals)
        unowned = sum(
            int(string(v["amount_picos"], "amount"))
            for v in totals
            if v["kind"] in {"agent", "tail", "small_tails"}
        )
        connection.execute(
            "INSERT INTO dashboard_runs VALUES (?,?,'~delta~',?,?,?)",
            (thread, generation, str(total), str(unowned), publication),
        )
    elif not jobs or jobs[0]["version"] != generation:
        if jobs:
            connection.execute(
                "INSERT OR IGNORE INTO dashboard_garbage VALUES (?,?)",
                (thread, jobs[0]["publication"]),
            )
        connection.execute("UPDATE dashboard_serial SET last=last+1 WHERE singleton=1")
        publication = rows(
            connection, "SELECT last FROM dashboard_serial WHERE singleton=1"
        )[0]["last"]
        connection.execute(
            "DELETE FROM dashboard_stage_contributions WHERE thread=?", (thread,)
        )
        connection.execute(
            "INSERT INTO dashboard_runs VALUES (?,?,'','0','0',?) ON CONFLICT(thread) DO UPDATE SET version=excluded.version,cursor='',total='0',unowned='0',publication=excluded.publication",
            (thread, generation, publication),
        )
    job = rows(connection, "SELECT * FROM dashboard_runs WHERE thread=?", (thread,))[0]
    publication = integer(job["publication"], "publication")
    after = string(job["cursor"], "cursor", empty=True)
    identities = tuple(
        string(v["response"], "response")
        for v in rows(
            connection,
            "SELECT response FROM (SELECT response FROM responses WHERE task=? AND response>? UNION SELECT response FROM claude_request_events WHERE task=? AND response>? AND NOT EXISTS (SELECT 1 FROM responses WHERE responses.response=claude_request_events.request_id AND host='claude')) ORDER BY response LIMIT ?",
            (thread, after, thread, after, PAGE),
        )
    )
    if delta:
        identities = ()
    scanning = bool(identities)
    if not scanning:
        identities = tuple(
            string(v["response"], "response")
            for v in rows(
                connection,
                "SELECT response FROM dashboard_changes WHERE thread=? ORDER BY response LIMIT ?",
                (thread, PAGE),
            )
        )
    price(connection, identities)
    evidence = Evidence.read(connection)
    observed = facts(connection, thread, evidence=evidence, identities=identities)
    group = project(connection, thread)
    total, unowned = int(string(job["total"], "total")), int(
        string(job["unowned"], "unowned")
    )
    contributions = {
        string(v["key"], "key"): v
        for v in rows(
            connection,
            "SELECT * FROM dashboard_stage_contributions WHERE thread=?",
            (thread,),
        )
    }
    touched: set[str] = set()
    # Corrections and late IDs are replayed by response, without throwing away
    # completed pages when an active session merely appends new requests.
    for identity in identities:
        previous = rows(
            connection,
            "SELECT * FROM dashboard_stage_requests WHERE thread=? AND response=? AND generation=?",
            (thread, identity, publication),
        )
        if not previous:
            continue
        old = previous[0]
        old_amount = int(string(old["amount_picos"] or "0", "amount"))
        total -= old_amount
        if old["kind"] == "unowned":
            unowned -= old_amount
        for raw in sequence(parse(string(old["shares"], "shares")), "shares"):
            share = record(raw)
            key = string(share["key"], "key")
            amount = int(string(share["amount_picos"], "share"))
            value = contributions[key]
            role_amounts = counts(value["roles"])
            role_amounts[string(old["role"], "role")] -= amount
            value.update(
                amount_picos=str(int(string(value["amount_picos"], "amount")) - amount),
                unpriced=integer(value["unpriced"], "unpriced")
                - int(old["amount_picos"] is None),
                roles=packed({k: str(v) for k, v in role_amounts.items()}),
            )
            touched.add(key)
        connection.execute(
            "DELETE FROM dashboard_stage_requests WHERE thread=? AND response=? AND generation=?",
            (thread, identity, publication),
        )
    for fact in observed:
        request = fact.request
        total += request.amount or 0
        if fact.assignment.kind in {"owned", "shared"}:
            shares = [("bead:" + i.bead, a) for i, a in fact.assignment.shares]
        elif fact.assignment.kind == "unowned":
            shares = [("session:" + thread, request.amount or 0)]
            unowned += request.amount or 0
        else:
            projects = unattributable_projects(connection, evidence, fact, group)
            quotient, remainder = divmod(request.amount or 0, len(projects))
            shares = [
                ("ledger:unattributable:" + p, quotient + int(n < remainder))
                for n, p in enumerate(projects)
            ]
        for key, amount in shares:
            touched.add(key)
            value = contributions.setdefault(
                key,
                dict[str, object](
                    amount_picos="0",
                    unpriced=0,
                    roles="{}",
                    last_activity="1970-01-01T00:00:00+00:00",
                ),
            )
            role_amounts: Counter[str] = counts(value["roles"])
            role_amounts[fact.role] += amount
            value.update(
                amount_picos=str(int(string(value["amount_picos"], "amount")) + amount),
                unpriced=integer(value["unpriced"], "unpriced")
                + int(request.amount is None),
                roles=packed({k: str(v) for k, v in role_amounts.items()}),
                last_activity=max(
                    string(value["last_activity"], "activity"), request.at.isoformat()
                ),
            )
        connection.execute(
            "INSERT INTO dashboard_stage_requests VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                request.response,
                thread,
                request.at.isoformat(),
                fact.role,
                None if request.amount is None else str(request.amount),
                fact.assignment.kind,
                packed([dict(key=k, amount_picos=str(a)) for k, a in shares]),
                group,
                1,
                publication,
            ),
        )
    for key in touched:
        value = contributions[key]
        kind = (
            "bead"
            if key.startswith("bead:")
            else "unattributable" if key.startswith("ledger:") else "agent"
        )
        connection.execute(
            "INSERT INTO dashboard_stage_contributions VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(thread,key) DO UPDATE SET amount_picos=excluded.amount_picos,unpriced=excluded.unpriced,last_activity=excluded.last_activity,roles=excluded.roles",
            (
                thread,
                key,
                kind,
                key.split(":", 2)[2] if key.startswith("ledger:") else group,
                value["amount_picos"],
                value["unpriced"],
                1,
                value["last_activity"],
                value["roles"],
                "{}",
            ),
        )
    connection.execute(
        "UPDATE dashboard_runs SET version=?,cursor=?,total=?,unowned=? WHERE thread=?",
        (
            version(connection, thread),
            identities[-1] if scanning else after,
            str(total),
            str(unowned),
            thread,
        ),
    )
    connection.executemany(
        "DELETE FROM dashboard_changes WHERE thread=? AND response=?",
        [(thread, i) for i in identities],
    )
    if not delta and (scanning or identities):
        return False
    session(connection, thread, True, staged=True)
    connection.execute("DELETE FROM dashboard_runs WHERE thread=?", (thread,))
    connection.execute(
        "DELETE FROM dashboard_stage_contributions WHERE thread=?", (thread,)
    )
    return True


def advance(store: UsageStore, thread: str, deadline: float) -> None:
    # Each page commits its cursor and sums together. A change to native evidence
    # increments the generation and discards the incomplete rebuild on next pass.
    while time.monotonic() < deadline - 0.01:
        try:
            with store.connect() as connection:
                connection.set_progress_handler(
                    lambda: int(time.monotonic() >= deadline), 1000
                )
                finished = batch(connection, thread)
                connection.set_progress_handler(None, 0)
            if finished:
                return
        except sqlite3.OperationalError as error:
            if str(error) != "interrupted":
                raise
            return


def cleanup(connection: sqlite3.Connection) -> None:
    garbage = rows(
        connection, "SELECT thread,generation FROM dashboard_garbage LIMIT 1"
    )
    if not garbage:
        return
    thread, generation = garbage[0]["thread"], garbage[0]["generation"]
    connection.execute(
        "DELETE FROM dashboard_stage_requests WHERE rowid IN (SELECT rowid FROM dashboard_stage_requests WHERE thread=? AND generation=? LIMIT 128)",
        (thread, generation),
    )
    if not rows(
        connection,
        "SELECT 1 AS found FROM dashboard_stage_requests WHERE thread=? AND generation=? LIMIT 1",
        (thread, generation),
    ):
        connection.execute(
            "DELETE FROM dashboard_garbage WHERE thread=? AND generation=?",
            (thread, generation),
        )
