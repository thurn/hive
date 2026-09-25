"""Token partitions are materialized with observations, before price retention."""

import json
import sqlite3

from hive.jsonvalue import integer, sequence, string
from hive.tool_allocation import TokenShare
from hive.usage_store import row


def create(connection: sqlite3.Connection) -> bool:
    found: object = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='allocation_basis'"
    ).fetchone()
    if found is not None:
        return False
    connection.execute("""CREATE TABLE allocation_basis (
        response TEXT NOT NULL, ordinal INTEGER NOT NULL, bucket TEXT NOT NULL,
        tool TEXT, ref TEXT, phase TEXT NOT NULL, method TEXT NOT NULL,
        component TEXT NOT NULL, tokens INTEGER NOT NULL CHECK(tokens>=0),
        PRIMARY KEY(response,ordinal))""")
    return True


def write(
    connection: sqlite3.Connection, response: str, shares: tuple[TokenShare, ...]
) -> None:
    connection.execute("DELETE FROM allocation_basis WHERE response=?", (response,))
    connection.executemany(
        "INSERT INTO allocation_basis VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                response,
                n,
                s.bucket,
                s.tool,
                s.ref,
                s.phase,
                s.method,
                s.component,
                s.tokens,
            )
            for n, s in enumerate(shares)
        ],
    )


def read(
    connection: sqlite3.Connection, responses: tuple[str, ...]
) -> dict[str, tuple[TokenShare, ...]]:
    fetched: object = connection.execute(
        "SELECT response,bucket,tool,ref,phase,method,component,tokens FROM allocation_basis "
        "WHERE response IN (SELECT value FROM json_each(?)) ORDER BY response,ordinal",
        (json.dumps(responses),),
    ).fetchall()
    result: dict[str, list[TokenShare]] = {}
    for raw in sequence(fetched, "allocation basis"):
        response, bucket, tool, ref, phase, method, component, count = row(raw, 8)
        result.setdefault(string(response, "response"), []).append(
            TokenShare(
                string(bucket, "bucket"),
                None if tool is None else string(tool, "tool"),
                None if ref is None else string(ref, "ref"),
                string(phase, "phase"),
                string(method, "method"),
                string(component, "component"),
                integer(count, "tokens"),
            )
        )
    return {response: tuple(shares) for response, shares in result.items()}
