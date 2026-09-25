"""Observe small diagnostic facts in the same transaction as usage cursors."""

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hive import diagnostic_commands as commands
from hive import diagnostic_roles as roles
from hive.errors import HiveError
from hive.identity import Host
from hive.jsonvalue import record, string
from hive.usage import timestamp
from hive.usage_store import row


@dataclass(frozen=True)
class Location:
    host: Host
    thread: str
    agent: str
    file: str
    offset: int
    device: int
    inode: int


def text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text(v) for v in value)
    if isinstance(value, dict):
        return text(value.get("text", value.get("content", "")))
    return ""


def identifier(value: object, default: str = "unknown") -> str:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[\w.:/-]{1,256}", value)
        else default
    )


def event(
    connection: sqlite3.Connection,
    location: Location,
    kind: str,
    at: str,
    *,
    until: str | None = None,
    ref: str | None = None,
    amount: int | None = None,
) -> None:
    key = hashlib.sha256(
        f"{location.thread}:{location.file}:{location.offset}:{kind}:{ref}".encode()
    ).hexdigest()
    connection.execute(
        "INSERT OR IGNORE INTO session_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            key,
            location.host,
            location.thread,
            location.agent,
            kind,
            at,
            until,
            None if amount is None else str(amount),
            ref,
            location.file,
            location.offset,
            location.device,
            location.inode,
        ),
    )


def use(
    connection: sqlite3.Connection,
    location: Location,
    at: str,
    call: str,
    tool: str,
    value: object,
) -> None:
    if call == "unknown" or tool == "unknown":
        return
    content = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    uses = commands.commands(content)
    command_kind = commands.kind(uses[0]) if len(uses) == 1 else "other"
    if tool.lower().endswith("monitor"):
        command_kind = "monitor"
    connection.execute(
        "INSERT INTO tool_calls(host,thread,agent,call_id,tool,started_at,status,input_hash8,input_bytes,file,use_offset,use_device,use_inode,command_kind) VALUES (?,?,?,?,?,?,'pending',?,?,?,?,?,?,?) ON CONFLICT(thread,agent,call_id) DO UPDATE SET input_hash8=excluded.input_hash8,input_bytes=excluded.input_bytes WHERE excluded.input_bytes>tool_calls.input_bytes",
        (
            location.host,
            location.thread,
            location.agent,
            call,
            tool,
            at,
            commands.digest(content),
            len(content.encode()),
            location.file,
            location.offset,
            location.device,
            location.inode,
            command_kind,
        ),
    )
    for command in uses:
        if commands.kind(command).startswith("tg_"):
            from hive.tollgate_mentions import observe as mentions

            mentions(connection, location.thread, location.agent, at, command)
    for ordinal, command in enumerate(uses if len(uses) == 1 else ()):
        connection.execute(
            "INSERT OR IGNORE INTO tool_commands VALUES (?,?,?,?,NULL,NULL,?,?)",
            (
                location.thread,
                location.agent,
                call,
                ordinal,
                commands.digest(command),
                commands.kind(command),
            ),
        )


def result(
    connection: sqlite3.Connection,
    location: Location,
    at: str,
    call: str,
    output: object,
    error: bool,
) -> None:
    raw: object = connection.execute(
        "SELECT tool,started_at,command_kind FROM tool_calls WHERE thread=? AND agent=? AND call_id=?",
        (location.thread, location.agent, call),
    ).fetchone()
    if raw is None:
        return
    tool, started, command_kind = row(raw, 3)
    content = text(output)
    if isinstance(command_kind, str) and command_kind.startswith("tg_"):
        from hive.tollgate_mentions import observe as mentions

        mentions(
            connection,
            location.thread,
            location.agent,
            string(started, "start"),
            content,
        )
    elapsed = max(0, round((timestamp(at) - timestamp(started)).total_seconds() * 1000))
    status = "error" if error else "ok"
    try:
        decoded: object = json.loads(content)
    except ValueError:
        decoded = None
    if isinstance(decoded, dict) and (
        decoded.get("isError") is True
        or isinstance(decoded.get("exit_code"), int)
        and decoded["exit_code"] != 0
    ):
        status = "error"
    if tool in {"exec", "functions.exec"}:
        try:
            parsed = commands.chunks(content, ())
        except HiveError:
            parsed = None
        if parsed is None:
            status = "unparsed"
            connection.execute(
                "DELETE FROM tool_commands WHERE thread=? AND agent=? AND call_id=?",
                (location.thread, location.agent, call),
            )
        else:
            status = (
                "error" if any(c.exit_code not in (None, 0) for c in parsed) else "ok"
            )
            for chunk in parsed:
                connection.execute(
                    "INSERT INTO tool_commands VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(thread,agent,call_id,ordinal) DO UPDATE SET exit_code=excluded.exit_code,wall_ms=excluded.wall_ms",
                    (
                        location.thread,
                        location.agent,
                        call,
                        chunk.ordinal,
                        chunk.exit_code,
                        chunk.wall_ms,
                        chunk.command_hash8,
                        chunk.command_kind,
                    ),
                )
    connection.execute(
        "UPDATE tool_calls SET finished_at=?,duration_ms=?,status=?,result_bytes=?,result_offset=?,result_device=?,result_inode=? WHERE thread=? AND agent=? AND call_id=?",
        (
            at,
            elapsed,
            status,
            len(content.encode()),
            location.offset,
            location.device,
            location.inode,
            location.thread,
            location.agent,
            call,
        ),
    )
    if tool in {"AskUserQuestion", "request_user_input"}:
        event(
            connection,
            location,
            "human_wait",
            string(started, "start"),
            until=at,
            ref=call,
        )
    if content.startswith(
        (
            "Permission to use",
            "Permission denied",
            "User denied",
            "The user doesn't want to proceed",
        )
    ):
        event(connection, location, "permission_denied", at, ref=call)


def observe(
    connection: sqlite3.Connection, location: Location, raw: dict[str, object]
) -> None:
    previous: object = connection.execute(
        "SELECT last_record,spawned FROM diagnostic_sessions WHERE thread=? AND agent=?",
        (location.thread, location.agent),
    ).fetchone()
    if raw.get("type") == "custom-title":
        title = raw.get("customTitle")
        if isinstance(title, str):
            connection.execute(
                "INSERT INTO diagnostic_titles VALUES (?,?,?,?) ON CONFLICT(thread,agent) DO UPDATE SET role=excluded.role,offset=excluded.offset WHERE excluded.offset>=diagnostic_titles.offset",
                (
                    location.thread,
                    location.agent,
                    roles.title_role(title),
                    location.offset,
                ),
            )
        return
    if raw.get("timestamp") is None:
        return
    at = timestamp(raw["timestamp"]).astimezone(UTC).isoformat()
    connection.execute(
        "INSERT INTO diagnostic_sessions VALUES (?,?,?,?) ON CONFLICT(thread,agent) DO UPDATE SET last_record=MAX(last_record,excluded.last_record),spawned=MIN(spawned,excluded.spawned)",
        (location.thread, location.agent, at, at),
    )
    payload = raw.get("payload")
    data = record(payload, "native payload") if isinstance(payload, dict) else raw
    kind = data.get("type") if location.host == Host.CODEX else raw.get("type")
    user = (kind == "message" and data.get("role") == "user") or kind == "user"
    message = raw.get("message") if location.host == Host.CLAUDE else data
    message = record(message, "message") if isinstance(message, dict) else {}
    content = text(message.get("content", data.get("text", "")))
    if user and previous is not None:
        last = string(row(previous, 2)[0], "last record")
        if (timestamp(at) - timestamp(last)).total_seconds() > 300:
            event(connection, location, "idle", last, until=at)
    found = None
    if user:
        found = roles.detect(content)
        if content.startswith("[Request interrupted by user"):
            event(connection, location, "interrupt", at)
    if raw.get("attributionSkill") is not None:
        found = identifier(raw["attributionSkill"])
    if roles.observe(connection, location.thread, location.agent, at, found):
        event(connection, location, "role_change", at, ref=found)
    if raw.get("subtype") == "api_error":
        event(connection, location, "api_error", at)
    if raw.get("subtype") in {"compact_boundary", "microcompact_boundary"} or kind in {
        "context_compacted",
        "compaction",
    }:
        event(connection, location, "compaction", at)
    if kind == "turn_aborted":
        event(connection, location, "interrupt", at)
    if location.host == Host.CODEX:
        if kind in {"function_call", "custom_tool_call"}:
            use(
                connection,
                location,
                at,
                identifier(data.get("call_id")),
                identifier(data.get("name")),
                data.get("arguments", data.get("input", "")),
            )
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            result(
                connection,
                location,
                at,
                identifier(data.get("call_id")),
                data.get("output"),
                data.get("is_error") is True,
            )
    else:
        blocks = message.get("content")
        if isinstance(blocks, list):
            for value in blocks:
                block = record(value, "message block")
                if block.get("type") == "tool_use":
                    use(
                        connection,
                        location,
                        at,
                        identifier(block.get("id")),
                        identifier(block.get("name")),
                        block.get("input"),
                    )
                elif block.get("type") == "tool_result":
                    result(
                        connection,
                        location,
                        at,
                        identifier(block.get("tool_use_id")),
                        block.get("content"),
                        block.get("is_error") is True,
                    )


def orphan(connection: sqlite3.Connection) -> None:
    cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    connection.execute(
        "UPDATE tool_calls SET status='orphaned' WHERE status='pending' AND started_at<? AND thread IN (SELECT thread FROM diagnostic_sessions GROUP BY thread HAVING MAX(last_record)<?)",
        (cutoff, cutoff),
    )
