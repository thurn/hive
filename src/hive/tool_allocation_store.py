"""Queryable allocation rows share transactions with their retained evidence."""

import sqlite3

from hive.errors import ErrorCode, HiveError
from hive.identity import ThreadId
from hive.jsonvalue import parse, sequence, string
from hive.pricing import Quote, dollars
from hive.tool_report import report
from hive.usage_store import row


def refresh(connection: sqlite3.Connection, task: ThreadId) -> None:
    # Replay the task: a late stream update can change all subsequent carrying
    # shares, even when those requests' token totals did not change.
    fetched: object = connection.execute(
        "SELECT response,_quote FROM request_detail WHERE thread=? AND host='claude' "
        "AND source<>'events' AND _quote IS NOT NULL",
        (task,),
    ).fetchall()
    quotes = {
        string(row(value, 2)[0], "response"): Quote.read(
            parse(string(row(value, 2)[1], "quote"))
        )
        for value in sequence(fetched, "retained allocation quotes")
    }
    allocation = report(connection, task, quotes) if quotes else None
    connection.execute(
        "DELETE FROM tool_allocation WHERE response IN (SELECT response FROM responses WHERE task=?)",
        (task,),
    )
    if allocation is None:
        return
    for response, charges in allocation.requests:
        if sum(charge.amount for charge in charges) != quotes[response].amount:
            raise HiveError(
                ErrorCode.INVALID_RECORD,
                "Tool allocation differs from retained request price",
            )
        connection.executemany(
            "INSERT INTO tool_allocation(response,ordinal,bucket,tool_use_id,tool_name,component,tokens,usd,method) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    response,
                    ordinal,
                    charge.bucket,
                    charge.ref,
                    charge.tool,
                    charge.component,
                    charge.tokens,
                    dollars(charge.amount),
                    charge.method,
                )
                for ordinal, charge in enumerate(charges)
            ],
        )


def create(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS tool_allocation (
        response TEXT NOT NULL, ordinal INTEGER NOT NULL, bucket TEXT NOT NULL,
        tool_use_id TEXT, tool_name TEXT, component TEXT NOT NULL,
        tokens INTEGER NOT NULL CHECK(tokens>=0), usd TEXT NOT NULL, method TEXT NOT NULL,
        PRIMARY KEY(response,ordinal))""")
    fetched: object = connection.execute(
        "SELECT DISTINCT task FROM responses WHERE host='claude'"
    ).fetchall()
    for value in sequence(fetched, "allocation tasks"):
        refresh(connection, ThreadId(string(row(value, 1)[0], "thread")))
