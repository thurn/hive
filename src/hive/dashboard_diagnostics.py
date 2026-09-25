"""Paged diagnostic snapshots keep large tool histories outside the collector deadline."""

import math
import sqlite3
import time
from datetime import UTC, datetime, timedelta

from hive.bead_assignment import Evidence
from hive.bead_requests import Request
from hive.dashboard_accounting import Fact, project, unattributable_projects
from hive.dashboard_incremental import version
from hive.dashboard_values import packed, rows
from hive.diagnostic_report import WAITING, cache_amount
from hive.identity import Host, ThreadId
from hive.jsonvalue import integer, parse, record, sequence, string
from hive.usage import timestamp
from hive.usage_store import UsageStore

PAGE = 64


def p95(
    connection: sqlite3.Connection, thread: str, tool: str, kind: str
) -> int | None:
    if not rows(
        connection,
        "SELECT 1 AS ready FROM dashboard_duration_state WHERE singleton=1 AND complete=1",
    ):
        return None
    group: str = project(connection, thread)
    name = tool if kind == "other" else kind
    previous = rows(
        connection,
        "SELECT p95,checked FROM dashboard_percentiles WHERE project=? AND kind=?",
        (group, name),
    )
    if (
        previous
        and time.time() - float(string(str(previous[0]["checked"]), "checked")) < 300
    ):
        return integer(previous[0]["p95"], "p95")
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    query = "FROM dashboard_durations WHERE project=? AND started>=? AND kind=?"
    parameters = (group, cutoff, name)
    count = integer(
        rows(connection, "SELECT COUNT(*) AS n " + query, parameters)[0]["n"], "count"
    )
    values = rows(
        connection,
        "SELECT duration " + query + " ORDER BY duration LIMIT 1 OFFSET ?",
        parameters + (max(0, math.ceil(0.95 * count) - 1),),
    )
    duration = integer(values[0]["duration"], "duration") if values else 0
    connection.execute(
        "INSERT INTO dashboard_percentiles VALUES (?,?,?,?) ON CONFLICT(project,kind) DO UPDATE SET p95=excluded.p95,checked=excluded.checked",
        (group, name, duration, time.time()),
    )
    return duration


def batch(connection: sqlite3.Connection, thread: str) -> None:
    generation = version(connection, thread)
    queued = rows(
        connection,
        "SELECT revision FROM dashboard_diagnostic_dirty WHERE thread=?",
        (thread,),
    )
    if not queued:
        return
    unowned = rows(
        connection,
        "SELECT key FROM dashboard_contributions WHERE thread=? AND kind IN ('agent','tail','small_tails')",
        (thread,),
    )
    unowned_key: str = (
        string(unowned[0]["key"], "key") if unowned else "session:" + thread
    )
    jobs = rows(
        connection, "SELECT * FROM dashboard_diagnostic_jobs WHERE thread=?", (thread,)
    )
    job = jobs[0] if jobs else None
    if (
        job is None
        or job["version"] != generation
        or record(parse(string(job["payload"], "job"))).get("unowned_key")
        != unowned_key
    ):
        payload: dict[str, object] = dict[str, object](
            cards={}, runs={}, insights=[], unowned_key=unowned_key
        )
        connection.execute(
            "INSERT INTO dashboard_diagnostic_jobs VALUES (?,?,?,'tools','',?) ON CONFLICT(thread) DO UPDATE SET version=excluded.version,revision=excluded.revision,phase='tools',cursor='',payload=excluded.payload",
            (thread, generation, queued[0]["revision"], packed(payload)),
        )
        job = rows(
            connection,
            "SELECT * FROM dashboard_diagnostic_jobs WHERE thread=?",
            (thread,),
        )[0]
    payload = record(parse(string(job["payload"], "job")))
    cards: dict[str, object] = record(payload["cards"])
    runs = record(payload["runs"])
    insights = sequence(payload["insights"], "insights")
    evidence: Evidence = Evidence.read(connection)
    group: str = project(connection, thread)

    def keys(value: dict[str, object], at: str) -> list[str]:
        request = Request(
            "diagnostic",
            ThreadId(thread),
            Host(string(value["host"], "host")),
            timestamp(value[at]),
            None,
            string(value["agent"], "agent", empty=True) or None,
            None,
            None,
            "diagnostic",
            0,
        )
        assignment = evidence.assign(request)
        if assignment.kind in {"owned", "shared"}:
            return ["bead:" + i.bead for i, _ in assignment.shares]
        if assignment.kind == "unowned":
            return [unowned_key]
        return [
            "ledger:unattributable:" + p
            for p in unattributable_projects(
                connection,
                evidence,
                Fact(request, assignment, "ad_hoc", False, ()),
                group,
            )
        ]

    def mark(
        destinations: list[str], at: str, name: str | None = None, count: int = 1
    ) -> None:
        for key in destinations:
            item = record(cards.get(key, dict(at=at, badges={})))
            badges = record(item["badges"])
            if name is not None:
                badges[name] = integer(badges.get(name, 0), "count") + count
            item.update(at=max(string(item["at"], "activity"), at), badges=badges)
            cards[key] = item

    cursor = string(job["cursor"], "cursor", empty=True)
    after = tuple(sequence(parse(cursor), "cursor")) if cursor else ("", "", "")
    if job["phase"] == "tools":
        values = rows(
            connection,
            "SELECT host,agent,call_id,tool,input_hash8,started_at,finished_at,status,duration_ms,command_kind FROM tool_calls WHERE thread=? AND (started_at,call_id,agent)>(?,?,?) ORDER BY started_at,call_id,agent LIMIT ?",
            (thread, *after, PAGE),
        )
        for value in values:
            at = string(value["started_at"], "start")
            destinations = keys(value, "started_at")
            mark(destinations, at)
            if value["status"] == "error":
                mark(destinations, at, "tool_errors")
            kind = string(value["command_kind"], "kind")
            duration = value["duration_ms"]
            if isinstance(duration, int) and duration > 60000 and kind not in WAITING:
                threshold = p95(connection, thread, string(value["tool"], "tool"), kind)
                if threshold is None:
                    return
                if duration > threshold:
                    mark(destinations, at, "slow_tools")
            agent = string(value["agent"], "agent", empty=True)
            run = record(runs[agent]) if agent in runs else None
            signature = packed([value["tool"], value["input_hash8"]])
            if value["status"] != "error":
                if run is not None and integer(run["count"], "count") >= 3:
                    insights.append(run)
                runs.pop(agent, None)
                continue
            if run is not None and run["signature"] != signature:
                if integer(run["count"], "count") >= 3:
                    insights.append(run)
                run = None
            if run is None:
                run = dict[str, object](
                    kind="retry_loop",
                    signature=signature,
                    count=0,
                    at=at,
                    until=at,
                    agent=agent,
                    keys=[],
                    initial=[],
                )
            number = integer(run["count"], "count") + 1
            initial = sequence(run["initial"], "initial")
            if number <= 2:
                initial.append(destinations)
            elif number == 3:
                for previous in initial:
                    mark(
                        [string(k, "key") for k in sequence(previous, "keys")],
                        at,
                        "retry_loops",
                    )
                mark(destinations, at, "retry_loops")
            else:
                mark(destinations, at, "retry_loops")
            run.update(
                count=number,
                until=value["finished_at"] or at,
                initial=initial,
                keys=sorted(
                    {string(k, "key") for k in sequence(run["keys"], "keys")}
                    | set(destinations)
                ),
            )
            runs[agent] = run
        if values:
            last = values[-1]
            cursor = packed([last["started_at"], last["call_id"], last["agent"]])
        phase = "tools" if len(values) == PAGE else "events"
        if phase == "events":
            for raw in runs.values():
                run = record(raw)
                if integer(run["count"], "count") >= 3:
                    insights.append(run)
            runs = {}
            cursor = ""
    else:
        event_after = after[:2]
        values = rows(
            connection,
            "SELECT * FROM session_events WHERE thread=? AND (at,id)>(?,?) ORDER BY at,id LIMIT ?",
            (thread, *event_after, PAGE),
        )
        for value in values:
            at = string(value["at"], "at")
            destinations = keys(value, "at")
            mark(destinations, at)
            if value["kind"] == "api_error":
                mark(destinations, at, "api_errors")
            if (
                value["kind"] == "human_wait"
                and (
                    (timestamp(value["until"]) if value["until"] else datetime.now(UTC))
                    - timestamp(at)
                ).total_seconds()
                > 600
            ):
                mark(destinations, at, "human_waits")
            if value["kind"] == "cache_rewrite" and isinstance(value["ref"], str):
                amount = cache_amount(connection, string(value["ref"], "response"))
                if amount is not None:
                    insights.append(
                        dict(
                            kind="cache_rewrite",
                            at=at,
                            amount_picos=str(amount),
                            keys=destinations,
                        )
                    )
        if values:
            cursor = packed([values[-1]["at"], values[-1]["id"], ""])
        phase = "events" if len(values) == PAGE else "done"
    payload.update(cards=cards, runs=runs, insights=insights)
    if phase != "done":
        connection.execute(
            "UPDATE dashboard_diagnostic_jobs SET phase=?,cursor=?,payload=? WHERE thread=?",
            (phase, cursor, packed(payload), thread),
        )
        return
    previous = rows(
        connection,
        "SELECT payload FROM dashboard_diagnostic_sets WHERE thread=?",
        (thread,),
    )
    old = (
        record(
            record(parse(string(previous[0]["payload"], "snapshot"))).get("cards", {})
        )
        if previous
        else {}
    )
    connection.execute(
        "INSERT INTO dashboard_diagnostic_sets VALUES (?,?) ON CONFLICT(thread) DO UPDATE SET payload=excluded.payload",
        (thread, packed(payload)),
    )
    connection.executemany(
        "INSERT OR IGNORE INTO dashboard_card_dirty VALUES (?)",
        [(k,) for k in set(old) | set(cards)],
    )
    connection.execute(
        "DELETE FROM dashboard_diagnostic_jobs WHERE thread=?", (thread,)
    )
    connection.execute(
        "DELETE FROM dashboard_diagnostic_dirty WHERE thread=? AND revision=?",
        (thread, job["revision"]),
    )


def refresh(store: UsageStore, deadline: float) -> None:
    from hive.dashboard_durations import refresh as refresh_durations

    refresh_durations(store, min(deadline, time.monotonic() + 0.025))
    with store.connect(write=False) as connection:
        tasks = rows(
            connection,
            "SELECT thread FROM dashboard_diagnostic_dirty ORDER BY rowid LIMIT 8",
        )
    for value in tasks:
        if time.monotonic() >= deadline - 0.01:
            return
        thread = string(value["thread"], "thread")
        try:
            with store.connect() as connection:
                connection.set_progress_handler(
                    lambda: int(time.monotonic() >= deadline), 1000
                )
                batch(connection, thread)
                connection.set_progress_handler(None, 0)
                pending = rows(
                    connection,
                    "SELECT revision FROM dashboard_diagnostic_dirty WHERE thread=?",
                    (thread,),
                )
                if pending:
                    connection.execute(
                        "DELETE FROM dashboard_diagnostic_dirty WHERE thread=?",
                        (thread,),
                    )
                    connection.execute(
                        "INSERT INTO dashboard_diagnostic_dirty VALUES (?,?)",
                        (thread, pending[0]["revision"]),
                    )
        except sqlite3.OperationalError as error:
            if str(error) != "interrupted":
                raise
            return
