"""Only UUIDs observed in classified Tollgate input/output are candidates."""

import re
import sqlite3


def observe(
    connection: sqlite3.Connection, thread: str, agent: str, at: str, content: str
) -> None:
    for candidate in sorted(
        set(
            re.findall(
                r"(?<![\w-])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![\w-])",
                content,
            )
        )
    )[:32]:
        connection.execute(
            "INSERT INTO tollgate_mentions VALUES (?,?,?,?) ON CONFLICT(thread,agent,candidate) DO UPDATE SET first_seen=MIN(first_seen,excluded.first_seen)",
            (thread, agent, candidate, at),
        )
