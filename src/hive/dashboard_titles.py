"""Session title fallback reads only a bounded native prefix on demand."""

import sqlite3

from hive.dashboard_excerpts import clip, line
from hive.dashboard_values import rows
from hive.diagnostic_store import text
from hive.errors import HiveError
from hive.jsonvalue import parse, record, string
from hive.transcript_chunks import MAX_LINE


def title(connection: sqlite3.Connection, thread: str) -> str | None:
    sources = rows(
        connection,
        "SELECT path,device,inode FROM sources WHERE task=? AND file=''",
        (thread,),
    )
    if not sources:
        return None
    source = sources[0]
    # Verify the native file before scanning; it can move between collector passes.
    try:
        path = string(source["path"], "source")
        first = line(path, 0, source["device"], source["inode"])
        del first
        with open(path, "rb") as stream:
            content = stream.read(MAX_LINE)
        for raw in content.splitlines(keepends=True):
            if not raw.endswith(b"\n"):
                break
            value = record(parse(raw.decode()))
            if value.get("type") == "custom-title":
                candidate = value.get("customTitle")
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()[:120]
            payload = (
                record(value["payload"])
                if isinstance(value.get("payload"), dict)
                else value
            )
            if payload.get("role") != "user" and value.get("type") != "user":
                continue
            message = (
                record(value["message"])
                if isinstance(value.get("message"), dict)
                else payload
            )
            candidate = text(message.get("content"))
            if candidate.strip():
                return clip(candidate.strip().splitlines()[0], 480)[:120]
    except (OSError, ValueError, HiveError, UnicodeError):
        return None
    return None
