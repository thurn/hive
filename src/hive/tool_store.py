"""Stable part identities make streaming, inode replacement and rereads converge."""

import hashlib
import json
import sqlite3

from hive.claude_usage import ClaudeResponse
from hive.identity import ThreadId
from hive.jsonvalue import integer, sequence, string
from hive.tool_parts import Part, parts
from hive.usage import Tokens
from hive.usage_store import row


def record_id(
    connection: sqlite3.Connection, task: ThreadId, file: str, identity: str
) -> tuple[int, bool]:
    added = bool(
        connection.execute(
            "INSERT OR IGNORE INTO allocation_seen(task,file,identity) VALUES (?,?,?)",
            (task, file, identity),
        ).rowcount
    )
    value: object = connection.execute(
        "SELECT ordinal FROM allocation_seen WHERE task=? AND file=? AND identity=?",
        (task, file, identity),
    ).fetchone()
    return integer(row(value, 1)[0], "part record"), added


def observe(
    connection: sqlite3.Connection,
    task: ThreadId,
    file: str,
    position: int,
    raw: dict[str, object],
    response: ClaudeResponse | None,
) -> None:
    agent = "" if raw.get("agentId") is None else string(raw["agentId"], "part agent")
    if file and not agent:
        agent = file.removeprefix("agent-")
    values = parts(raw)
    # Native UUIDs survive replacement/replay. Older records without UUIDs use
    # their file position and a digest, never the inode or stored text.
    digest = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    identity = (
        string(raw["uuid"], "record UUID")
        if raw.get("uuid") is not None
        else f"{position}:{digest}"
    )
    order, added_record = record_id(connection, task, file, identity)
    if not added_record and response is None:
        return
    connection.execute(
        "INSERT OR IGNORE INTO allocation_cursor(task,agent) VALUES (?,?)",
        (task, agent),
    )
    if (
        added_record
        and raw.get("type") == "system"
        and raw.get("subtype") in {"compact_boundary", "microcompact_boundary"}
    ):
        connection.execute(
            "UPDATE allocation_cursor SET reset=1 WHERE task=? AND agent=?",
            (task, agent),
        )
    previous_raw: object = connection.execute(
        "SELECT response,reset FROM allocation_cursor WHERE task=? AND agent=?",
        (task, agent),
    ).fetchone()
    previous, reset = row(previous_raw, 2)
    if response is not None:
        usage = response.usage.tokens
        if not isinstance(usage, Tokens):
            return
        identity = response.usage.response
        added = connection.execute(
            "INSERT OR IGNORE INTO allocation_responses(response,task,agent,previous,reset) VALUES (?,?,?,?,?)",
            (identity, task, agent, previous, reset),
        ).rowcount
        if added:
            connection.execute(
                "INSERT INTO segment_parts SELECT task,agent,?,ordinal,record,part_order,kind,ref,name,bytes FROM pending_parts WHERE task=? AND agent=?",
                (identity, task, agent),
            )
            connection.execute(
                "DELETE FROM pending_parts WHERE task=? AND agent=?", (task, agent)
            )
            connection.execute(
                "UPDATE allocation_cursor SET response=?,reset=0 WHERE task=? AND agent=?",
                (identity, task, agent),
            )
        # Carried assistant facts are read from response_blocks, so final
        # streaming updates also repair every later segment containing them.
        block = string(raw.get("uuid", str(order)), "content identity")
        for ordinal, part in enumerate(values):
            connection.execute(
                "INSERT INTO response_blocks VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(response,block,ordinal) DO UPDATE SET kind=excluded.kind,name=excluded.name,ref=excluded.ref,bytes=excluded.bytes,output_count=excluded.output_count,thinking_count=excluded.thinking_count WHERE excluded.output_count>=response_blocks.output_count AND excluded.thinking_count>=response_blocks.thinking_count",
                (
                    identity,
                    block,
                    ordinal,
                    order,
                    part.kind,
                    part.name,
                    part.ref,
                    part.size,
                    usage.output,
                    usage.reasoning_output,
                ),
            )
    elif added_record:
        for ordinal, part in enumerate(values):
            pending(connection, task, agent, order, ordinal, part)


def pending(
    connection: sqlite3.Connection,
    task: ThreadId,
    agent: str,
    order: int,
    ordinal: int,
    part: Part,
) -> None:
    connection.execute(
        "INSERT INTO pending_parts(task,agent,record,part_order,kind,ref,name,bytes) VALUES (?,?,?,?,?,?,?,?)",
        (task, agent, order, ordinal, part.kind, part.ref, part.name, part.size),
    )


def oversized(
    connection: sqlite3.Connection, task: ThreadId, file: str, start: int, size: int
) -> None:
    end = start + size
    existing: object = connection.execute(
        "SELECT start,end,record FROM tool_oversized WHERE task=? AND file=? AND start<=? AND end>=? ORDER BY start LIMIT 1",
        (task, file, end, start),
    ).fetchone()
    if existing is None:
        order, added = record_id(connection, task, file, f"oversized:{start}")
        if not added:
            return
        connection.execute(
            "INSERT INTO tool_oversized VALUES (?,?,?,?,?)",
            (task, file, start, end, order),
        )
        pending(
            connection,
            task,
            file.removeprefix("agent-") if file else "",
            order,
            0,
            Part("oversized", None, None, size),
        )
    else:
        old_start, old_end, order = (
            integer(value, "oversized range") for value in row(existing, 3)
        )
        if end <= old_end:
            return
        connection.execute(
            "UPDATE tool_oversized SET end=? WHERE task=? AND file=? AND start=?",
            (end, task, file, old_start),
        )
        for table in ("pending_parts", "segment_parts"):
            connection.execute(
                f"UPDATE {table} SET bytes=? WHERE record=? AND kind='oversized'",
                (end - old_start, order),
            )


def read_parts(
    connection: sqlite3.Connection, response: str, *, output: bool
) -> tuple[Part, ...]:
    if output:
        query = "SELECT kind,ref,name,bytes FROM response_blocks WHERE response=? ORDER BY record,ordinal"
    else:
        query = "SELECT kind,ref,name,bytes FROM (SELECT kind,ref,name,bytes,record,part_order FROM segment_parts WHERE response=? UNION ALL SELECT b.kind,b.ref,b.name,b.bytes,b.record,b.ordinal FROM response_blocks b JOIN allocation_responses a ON b.response=a.previous WHERE a.response=?) ORDER BY record,part_order"
    fetched: object = connection.execute(
        query, (response,) if output else (response, response)
    ).fetchall()
    result: list[Part] = []
    for value in sequence(fetched, "allocation parts"):
        kind, ref, name, count = row(value, 4)
        result.append(
            Part(
                string(kind, "part kind"),
                None if ref is None else string(ref, "part ref"),
                None if name is None else string(name, "part name"),
                integer(count, "part bytes"),
            )
        )
    return tuple(result)
