"""Exact dollars, validated SQL rows, and immutable public query options."""

import base64
import binascii
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, record, sequence, string
from hive.usage_store import row


@dataclass(frozen=True)
class Filters:
    window: str = "7d"
    project: str | None = None
    role: str | None = None
    state: str | None = None
    q: str = ""
    active: bool = False
    older_completed: bool = False
    cursor: str | None = None


def picos(value: object) -> int:
    text = string(value, "USD")
    if re.fullmatch(r"[0-9]+(?:\.[0-9]{1,12})?", text) is None:
        raise HiveError(ErrorCode.INVALID_RECORD, "Invalid dollar amount")
    whole, _, fraction = text.partition(".")
    return int(whole) * 10**12 + int(fraction.ljust(12, "0"))


def rows(
    connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> list[dict[str, object]]:
    cursor = connection.execute(sql, parameters)
    description: object = cursor.description
    if not isinstance(description, tuple):
        raise HiveError(ErrorCode.INVALID_RECORD, "Query has no columns")
    names = tuple(string(row(c, 7)[0], "column") for c in description)
    return [
        dict(zip(names, row(v, len(names)), strict=True))
        for v in sequence(cursor.fetchall(), "rows")
    ]


def packed(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cursor(value: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(packed(value).encode()).decode()


def uncursor(value: str) -> dict[str, object]:
    try:
        if len(value) > 8192:
            raise ValueError("Oversized cursor")
        return record(
            parse(base64.b64decode(value, altchars=b"-_", validate=True).decode())
        )
    except (ValueError, UnicodeError, binascii.Error, HiveError) as error:
        raise HiveError(ErrorCode.INVALID_INPUT, "Invalid dashboard cursor") from error


def window_start(window: str) -> tuple[str, str]:
    try:
        path = Path("/etc/localtime")
        key = str(path.resolve()).split("zoneinfo/", 1)[-1]
        with path.open("rb") as stream:
            zone = ZoneInfo.from_file(stream, key=key)
        now = datetime.now(zone)
    except (OSError, ValueError):
        now = datetime.now(UTC)
    if window not in {"today", "7d", "30d"}:
        raise HiveError(ErrorCode.INVALID_INPUT, "Window must be today, 7d, or 30d")
    beginning = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days={"today": 0, "7d": 6, "30d": 29}[window]
    )
    return beginning.astimezone(UTC).isoformat(), str(now.tzinfo)
