"""One retained-price snapshot supplies both bead reports and reconciliation."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from hive.cost_report import report as thread_report
from hive.identity import Host, PricingTier, ThreadId
from hive.jsonvalue import integer, parse, sequence, string
from hive.pricing import Quote
from hive.usage import timestamp
from hive.usage_store import UsageStore, row


@dataclass(frozen=True)
class Request:
    response: str
    thread: ThreadId
    host: Host
    at: datetime
    model: str | None
    agent: str | None
    skill: str | None
    query_source: str | None
    source: str
    amount: int | None


def prepare(
    store: UsageStore, tier: PricingTier
) -> tuple[dict[str, dict[str, object]], int]:
    with store.connect(write=False) as connection:
        fetched: object = connection.execute(
            "SELECT DISTINCT thread,host FROM request_detail"
        ).fetchall()
    reports: dict[str, dict[str, object]] = {}
    unretained = 0
    for value in sequence(fetched, "cost threads"):
        task, host = row(value, 2)
        identity = ThreadId(string(task, "thread"))
        report = thread_report(store, identity, None if host == Host.CLAUDE else tier)
        reports[identity] = report
        unretained += integer(report["unretained_estimates"], "unretained estimates")
    return reports, unretained


def requests(connection: sqlite3.Connection, tier: PricingTier) -> tuple[Request, ...]:
    fetched: object = connection.execute(
        "SELECT d.response,d.thread,d.host,CASE WHEN d.source='events' THEN e.occurred ELSE d.observed_at END,"
        "d.model,d.agent,d.skill,d.query_source,d.source,d._quote "
        "FROM request_detail d LEFT JOIN claude_request_events e ON d.source='events' AND d.response=e.response "
        "WHERE d.tier IS NULL OR d.tier=? ORDER BY d.thread,d.response",
        (tier,),
    ).fetchall()
    result: list[Request] = []
    for value in sequence(fetched, "retained requests"):
        response, thread, host, at, model, agent, skill, query, source, quote = row(
            value, 10
        )
        result.append(
            Request(
                string(response, "request"),
                ThreadId(string(thread, "thread")),
                Host(string(host, "host")),
                timestamp(at),
                None if model is None else string(model, "model"),
                None if agent is None else string(agent, "agent"),
                None if skill is None else string(skill, "skill"),
                None if query is None else string(query, "query source"),
                string(source, "source"),
                (
                    None
                    if quote is None
                    else int(Quote.read(parse(string(quote, "retained quote"))).amount)
                ),
            )
        )
    return tuple(result)
