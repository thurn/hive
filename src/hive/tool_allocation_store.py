"""Queryable shares and their token basis share transactions with observations."""

import json
import sqlite3

from hive import tool_basis_store
from hive.errors import ErrorCode, HiveError
from hive.identity import ThreadId
from hive.jsonvalue import parse, sequence, string
from hive.pricing import PricedUsage, dollars
from hive.tool_allocation import Charge, price
from hive.tool_replay import read, replay
from hive.usage import tokens
from hive.usage_store import row


def quotes(
    connection: sqlite3.Connection,
    task: ThreadId,
    selected: tuple[str, ...] | None = None,
) -> dict[str, PricedUsage]:
    fetched: object = connection.execute(
        "SELECT response,_quote,_usage FROM request_detail WHERE thread=? AND host='claude' "
        "AND source<>'events' AND _quote IS NOT NULL "
        "AND (? IS NULL OR response IN (SELECT value FROM json_each(?)))",
        (
            task,
            None if selected is None else json.dumps(selected),
            json.dumps(selected),
        ),
    ).fetchall()
    return {
        string(row(v, 3)[0], "response"): PricedUsage.read(
            parse(string(row(v, 3)[1], "quote")),
            tokens(parse(string(row(v, 3)[2], "usage"))),
        )
        for v in sequence(fetched, "allocation prices")
    }


def write(
    connection: sqlite3.Connection,
    response: str,
    charges: tuple[Charge, ...],
    quoted: PricedUsage,
) -> None:
    if sum(c.amount for c in charges) != quoted.amount:
        raise HiveError(
            ErrorCode.INVALID_RECORD,
            "Tool allocation differs from retained request price",
        )
    connection.execute("DELETE FROM tool_allocation WHERE response=?", (response,))
    connection.executemany(
        "INSERT INTO tool_allocation(response,ordinal,bucket,tool_use_id,tool_name,component,tokens,usd,method) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                response,
                n,
                c.bucket,
                c.ref,
                c.tool,
                c.component,
                c.tokens,
                dollars(c.amount),
                c.method,
            )
            for n, c in enumerate(charges)
        ],
    )


def refresh(connection: sqlite3.Connection, task: ThreadId) -> None:
    """Collection refreshes full context chains, including still-unpriced requests."""
    prices = quotes(connection, task)
    result = replay(read(connection, task, prices))
    connection.execute(
        "DELETE FROM allocation_basis WHERE response IN (SELECT response FROM responses WHERE task=?)",
        (task,),
    )
    for response, shares in result.bases:
        tool_basis_store.write(connection, response, shares)
    connection.execute(
        "DELETE FROM tool_allocation WHERE response IN (SELECT response FROM responses WHERE task=?)",
        (task,),
    )
    for response, charges in result.requests:
        write(connection, response, charges, prices[response])


def retain(
    connection: sqlite3.Connection, task: ThreadId, selected: tuple[str, ...]
) -> None:
    """Apply first-retained rates without replaying an indexed context prefix."""
    prices = quotes(connection, task, selected)
    bases = tool_basis_store.read(connection, tuple(prices))
    missing = tuple(response for response in prices if response not in bases)
    if missing:
        indexed: object = connection.execute(
            "SELECT 1 FROM allocation_responses WHERE response IN (SELECT value FROM json_each(?)) LIMIT 1",
            (json.dumps(missing),),
        ).fetchone()
        if indexed is not None:
            raise HiveError(
                ErrorCode.INVALID_RECORD,
                "Indexed request is missing its allocation basis",
            )
        # Batch independent legacy observations; none has an indexed prefix.
        result = replay(read(connection, task, {}, unindexed=missing))
        for response, shares in result.bases:
            tool_basis_store.write(connection, response, shares)
            bases[response] = shares
    for response, quoted in prices.items():
        write(connection, response, price(quoted, bases.get(response, ())), quoted)


def refresh_all(connection: sqlite3.Connection) -> None:
    fetched: object = connection.execute(
        "SELECT DISTINCT task FROM responses WHERE host='claude'"
    ).fetchall()
    for value in sequence(fetched, "allocation tasks"):
        refresh(connection, ThreadId(string(row(value, 1)[0], "thread")))


def migrate_basis(connection: sqlite3.Connection) -> None:
    if tool_basis_store.create(connection):
        refresh_all(connection)


def create(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS tool_allocation (
        response TEXT NOT NULL, ordinal INTEGER NOT NULL, bucket TEXT NOT NULL,
        tool_use_id TEXT, tool_name TEXT, component TEXT NOT NULL,
        tokens INTEGER NOT NULL CHECK(tokens>=0), usd TEXT NOT NULL, method TEXT NOT NULL,
        PRIMARY KEY(response,ordinal))""")
    tool_basis_store.create(connection)
    refresh_all(connection)
