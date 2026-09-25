"""Keep retained Claude rates while monotonic observations gain output tokens."""

import json
import sqlite3

from hive.errors import ErrorCode, HiveError
from hive.identity import Host, ResponseId
from hive.jsonvalue import parse, sequence, string
from hive.pricing import Quote
from hive.usage import Tokens
from hive.usage_store import row


def usage_updates(
    connection: sqlite3.Connection, response: ResponseId, usage: Tokens
) -> list[tuple[str, str, str]]:
    fetched: object = connection.execute(
        "SELECT tier,quote FROM response_estimates WHERE response=?", (response,)
    ).fetchall()
    updates: list[tuple[str, str, str]] = []
    for raw in sequence(fetched, "retained response estimates"):
        key, value = row(raw, 2)
        quoted = Quote.read(parse(string(value, "retained price")))
        if quoted.host != Host.CLAUDE or quoted.modifier_key != key:
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Retained Claude price identity disagrees"
            )
        updates.append(
            (
                json.dumps(quoted.with_usage(usage).value(usage)),
                response,
                string(key, "modifier key"),
            )
        )
    return updates


def apply_updates(
    connection: sqlite3.Connection, updates: list[tuple[str, str, str]]
) -> None:
    connection.executemany(
        "UPDATE response_estimates SET quote=? WHERE response=? AND tier=?", updates
    )
