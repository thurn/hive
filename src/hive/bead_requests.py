"""One retained-price snapshot supplies both bead reports and reconciliation."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from hive.claude_usage import Modifiers
from hive.codex_folding import root
from hive.identity import Host, PricingTier, ResponseId, ThreadId
from hive.jsonvalue import parse, sequence, string
from hive.pricing import picos
from hive.request_pricing import price_response, request_context, retain
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


def owners(
    connection: sqlite3.Connection, bead: str | None
) -> tuple[ThreadId, ...] | None:
    """A bead needs its owners' roots, including each full folded agent chain."""
    if bead is None:
        return None
    fetched: object = connection.execute(
        "SELECT thread FROM bead_seen_owners WHERE bead=?", (bead,)
    ).fetchall()
    return tuple(
        sorted(
            {
                root(connection, string(row(v, 1)[0], "owner"))
                for v in sequence(fetched, "owners")
            }
        )
    )


def prepare(store: UsageStore, tier: PricingTier, bead: str | None) -> int:
    """Retain missing prices in scope, without thread or tool report construction."""
    fresh: list[tuple[str, str, str]] = []
    with store.connect(write=False) as connection:
        scope = owners(connection, bead)
        fetched: object = connection.execute(
            "SELECT response,_usage,model,_conflicted,_quote,host,_modifiers,flags FROM request_detail "
            "WHERE (tier IS NULL OR tier=?) AND _quote IS NULL "
            "AND (? IS NULL OR thread IN (SELECT value FROM json_each(?)))",
            (tier, None if scope is None else json.dumps(scope), json.dumps(scope)),
        ).fetchall()
        for raw in sequence(fetched, "unretained requests"):
            (
                identity,
                usage,
                model,
                conflict,
                cached,
                raw_host,
                raw_modifiers,
                raw_flags,
            ) = row(raw, 8)
            host = Host(string(raw_host, "host"))
            modifiers = (
                Modifiers.read(parse(string(raw_modifiers, "modifiers")))
                if host == Host.CLAUDE
                else None
            )
            flags = tuple(
                string(v, "flag")
                for v in sequence(parse(string(raw_flags, "flags")), "flags")
            )
            price_response(
                fresh,
                ResponseId(string(identity, "response")),
                usage,
                model,
                cached,
                request_context(host, modifiers, conflict, tier),
                flags,
            )
    return retain(store, fresh)


def requests(
    connection: sqlite3.Connection,
    tier: PricingTier,
    scope: tuple[ThreadId, ...] | None = None,
) -> tuple[Request, ...]:
    fetched: object = connection.execute(
        "SELECT d.response,d.thread,d.host,d.observed_at,"
        "d.model,d.agent,d.skill,d.query_source,d.source,d.usd "
        "FROM request_detail d "
        "WHERE (d.tier IS NULL OR d.tier=?) AND (? IS NULL OR d.thread IN (SELECT value FROM json_each(?))) ORDER BY d.thread,d.response",
        (tier, None if scope is None else json.dumps(scope), json.dumps(scope)),
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
                (None if quote is None else picos(quote)),
            )
        )
    return tuple(result)
