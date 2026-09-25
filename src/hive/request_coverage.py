"""Typed completeness evidence for bead owners within their retained-price snapshot."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from hive.claude_cost_state import comparison
from hive.event_evidence import read as event_evidence
from hive.identity import Host, PricingTier, ThreadId
from hive.jsonvalue import integer, sequence
from hive.pricing import picos
from hive.usage import timestamp
from hive.usage_store import row, source_status, stored_host


@dataclass(frozen=True)
class Coverage:
    host: Host | None
    unpriced: bool
    source_gaps: bool
    complete: bool


def read(
    connection: sqlite3.Connection, state: Path, task: ThreadId, tier: PricingTier
) -> Coverage:
    fetched: object = connection.execute(
        "SELECT d.usd,CASE WHEN r.host='claude' THEN 1-r.complete ELSE 0 END,COALESCE(r.last_observed,r.observed) FROM request_detail d JOIN responses r ON r.response=d.response WHERE d.thread=? "
        "AND d.source<>'events' AND (d.tier IS NULL OR d.tier=?)",
        (task, tier),
    ).fetchall()
    values = tuple(row(v, 3) for v in sequence(fetched, "coverage requests"))
    priced = tuple(v for v in values if v[0] is not None)
    amount = sum(picos(v[0]) for v in priced)
    last = max((timestamp(v[2]) for v in priced), default=None)
    health = source_status(connection, task)
    gaps = health.get("remaining_bytes") is None or any(
        bool(health.get(k))
        for k in ("parse_gaps", "source_error", "remaining_bytes", "incomplete_tail")
    )
    present: object = connection.execute(
        "SELECT 1 FROM request_detail WHERE thread=? LIMIT 1", (task,)
    ).fetchone()
    if present is None:
        return Coverage(None, False, True, False)
    host = stored_host(connection, task)
    unpriced = len(values) != len(priced)
    complete = False
    if host == Host.CLAUDE:
        evidence = event_evidence(
            connection,
            state,
            task,
            [],
            observed=len(values),
            priced=len(priced),
            amount=amount,
            partial=sum(integer(v[1], "partial output") for v in values),
            health=health,
            host_totals=comparison(connection, task, amount, last),
        )
        unpriced = unpriced or evidence.unpriced > 0
        complete = evidence.complete
    return Coverage(host, unpriced, gaps, complete)
