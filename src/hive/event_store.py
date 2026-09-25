"""Store allow-listed events once; sequence observations cover every event type."""

import json
import sqlite3
from datetime import UTC, datetime

from hive.claude_events import Log, request
from hive.errors import ErrorCode, HiveError
from hive.event_pricing import derive
from hive.identity import ThreadId
from hive.jsonvalue import string
from hive.pricing import dollars
from hive.usage_store import row


def gap(
    connection: sqlite3.Connection,
    file: str,
    position: int,
    detail: str,
    task: ThreadId | None = None,
    observed: datetime | None = None,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO claude_event_gaps(file,position,task,observed,detail) VALUES (?,?,?,?,?)",
        (file, position, task, (observed or datetime.now(UTC)).isoformat(), detail),
    )


def save(connection: sqlite3.Connection, event: Log) -> None:
    identity = (event.task, event.occurred.isoformat(), event.sequence)
    previous: object = connection.execute(
        "SELECT kind,version FROM claude_event_sequences WHERE task=? AND occurred=? AND sequence=?",
        identity,
    ).fetchone()
    if previous is not None and row(previous, 2) != (event.kind, event.version):
        raise HiveError(ErrorCode.INVALID_RECORD, "Conflicting event sequence identity")
    connection.execute(
        "INSERT OR IGNORE INTO claude_event_sequences(task,occurred,sequence,kind,version) VALUES (?,?,?,?,?)",
        (*identity, event.kind, event.version),
    )
    if event.kind == "api_error":
        connection.execute(
            "INSERT OR IGNORE INTO claude_request_errors(task,occurred,sequence) VALUES (?,?,?)",
            identity,
        )
    if event.kind != "api_request":
        return
    value = request(event)
    evidence = json.dumps(dict(event.values), sort_keys=True)
    old: object = connection.execute(
        "SELECT attributes FROM claude_request_events WHERE response=?",
        (value.response,),
    ).fetchone()
    if old is not None:
        if string(row(old, 1)[0], "event attributes") != evidence:
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Conflicting duplicate request event"
            )
        return
    occupied: object = connection.execute(
        "SELECT host FROM responses WHERE response=?", (value.response,)
    ).fetchone()
    if occupied is not None and row(occupied, 1)[0] != "claude":
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Event request identity belongs to another host"
        )
    derived = derive(value)
    flags = derived.flags + (("unjoinable",) if value.request_id is None else ())
    connection.execute(
        "INSERT INTO claude_request_events(response,request_id,client_id,task,occurred,sequence,observed,prompt,model,"
        "input,cached,writes,output,micros,host_usd,duration,speed,query_source,agent_name,skill,plugin,mcp_server,mcp_tool,effort,version,"
        "usage,modifiers,modifier_key,flags,attributes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            value.response,
            value.request_id,
            value.client_id,
            event.task,
            event.occurred.isoformat(),
            event.sequence,
            value.observed.isoformat(),
            value.prompt,
            value.model,
            value.input,
            value.cached,
            value.writes,
            value.output,
            value.micros,
            dollars(value.micros * 1_000_000),
            value.duration,
            value.speed,
            value.query_source,
            value.agent_name,
            value.skill,
            value.plugin,
            value.mcp_server,
            value.mcp_tool,
            value.effort,
            event.version,
            json.dumps(derived.usage.value()),
            json.dumps(derived.modifiers.value()),
            derived.modifiers.key,
            json.dumps(flags),
            evidence,
        ),
    )
