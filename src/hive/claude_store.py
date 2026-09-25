"""Claude per-file cursors and monotonic request updates share one transaction."""

import json
import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path

from hive.claude_usage import ClaudeResponse, decode
from hive.errors import ErrorCode, HiveError
from hive.identity import Host, ThreadId
from hive.jsonvalue import integer, parse, record, string
from hive.transcript_chunks import MAX_BATCH, MAX_LINE, Line, Oversize, read
from hive.usage import Tokens, timestamp, tokens
from hive.usage_store import UsageStore, row


def file_key(thread: ThreadId, path: Path) -> str:
    if path.name == f"{thread}.jsonl":
        return ""
    if (
        path.parent.name == "subagents"
        and path.parent.parent.name == thread
        and path.name.startswith("agent-")
        and path.suffix == ".jsonl"
        and len(path.stem) > 6
    ):
        return path.stem
    raise HiveError(
        ErrorCode.INVALID_RECORD, "Claude filename does not match thread or subagent"
    )


def regular(path: Path, *, parents: int = 0) -> None:
    if any(part.is_symlink() for part in (path, *list(path.parents)[:parents])):
        raise ValueError("Transcript symlinks are not allowed")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("A transcript must be a regular file")


def save(connection: sqlite3.Connection, event: ClaudeResponse) -> None:
    usage = event.usage
    current = usage.tokens
    if not isinstance(current, Tokens):
        raise HiveError(ErrorCode.INVALID_RECORD, "Claude usage is missing")
    previous: object = connection.execute(
        "SELECT host,usage,model,modifiers,complete,flags,last_observed FROM responses WHERE response=?",
        (usage.response,),
    ).fetchone()
    flags = set(event.flags)
    complete = event.complete
    last_observed = usage.observed.astimezone(UTC)
    if previous is not None:
        host, raw_usage, model, modifiers, old_complete, raw_flags, old_time = row(
            previous, 7
        )
        last_observed = max(last_observed, timestamp(old_time))
        old = tokens(parse(string(raw_usage, "stored Claude usage")))
        if (
            host != Host.CLAUDE
            or model != event.model
            or parse(string(modifiers, "stored modifiers")) != event.modifiers.value()
            or (
                old.input,
                old.cached_input,
                old.cache_write_input,
                old.cache_write_1h_input,
            )
            != (
                current.input,
                current.cached_input,
                current.cache_write_input,
                current.cache_write_1h_input,
            )
        ):
            raise HiveError(
                ErrorCode.INVALID_RECORD,
                "Conflicting Claude response identity or usage",
            )
        if (
            old.output > current.output
            or old.reasoning_output > current.reasoning_output
        ):
            seen: object = connection.execute(
                "SELECT 1 FROM claude_response_counts WHERE response=? AND output=? AND reasoning=?",
                (usage.response, current.output, current.reasoning_output),
            ).fetchone()
            if seen is not None:
                return
            raise HiveError(
                ErrorCode.INVALID_RECORD, "Decreasing Claude output counters"
            )
        from hive.jsonvalue import sequence

        flags.update(
            string(flag, "usage flag")
            for flag in sequence(parse(string(raw_flags, "flags")), "flags")
        )
        if "thinking_unmeasured" not in event.flags:
            flags.discard("thinking_unmeasured")
        complete |= bool(integer(old_complete, "complete response"))
    from hive.price_evidence import adopt_event_rates, apply_updates, usage_updates

    adopt_event_rates(connection, usage.response, event.model, event.modifiers, current)
    updates = usage_updates(connection, usage.response, current)
    connection.execute(
        "INSERT OR IGNORE INTO claude_response_counts(response,output,reasoning) VALUES (?,?,?)",
        (usage.response, current.output, current.reasoning_output),
    )
    connection.execute(
        "INSERT INTO responses(response,task,turn,observed,usage,input,cached,cache_write,output,reasoning,host,agent,model,modifier_key,modifiers,cache_write_1h,complete,skill,flags,last_observed) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(response) DO UPDATE SET "
        "usage=excluded.usage,output=excluded.output,reasoning=excluded.reasoning,complete=excluded.complete,flags=excluded.flags,last_observed=excluded.last_observed",
        (
            usage.response,
            usage.owner.thread,
            usage.owner.turn,
            usage.observed.isoformat(),
            json.dumps(current.value(), sort_keys=True),
            current.input,
            current.cached_input,
            current.cache_write_input,
            current.output,
            current.reasoning_output,
            Host.CLAUDE,
            usage.owner.agent,
            event.model,
            event.modifiers.key,
            json.dumps(event.modifiers.value(), sort_keys=True),
            current.cache_write_1h_input,
            int(complete),
            event.skill,
            json.dumps(sorted(flags)),
            last_observed.isoformat(),
        ),
    )

    apply_updates(connection, updates)


def collect(
    store: UsageStore,
    thread: ThreadId,
    path: Path,
    *,
    budget: int = MAX_BATCH,
    from_start: bool = False,
) -> dict[str, object]:
    connection: sqlite3.Connection
    device: int
    inode: int
    key: str = file_key(thread, path)
    agent = key.removeprefix("agent-") if key else None
    with store.connect() as connection:
        previous: object = connection.execute(
            "SELECT device,inode,position,skipping FROM sources WHERE task=? AND file=?",
            (thread, key),
        ).fetchone()
        device, inode, position, skipping = (
            (0, 0, 0, 0)
            if previous is None
            else tuple(integer(v, "source cursor") for v in row(previous, 4))
        )
        remaining: int | None = None
        incomplete: bool | None = None
        error: str | None = None
        read_bytes = 0

        def gap(offset: int, detail: str) -> None:
            connection.execute(
                "INSERT OR IGNORE INTO gaps(task,file,device,inode,position,detail) VALUES (?,?,?,?,?,?)",
                (thread, key, device, inode, offset, detail),
            )

        facts_before = connection.total_changes
        try:
            regular(path, parents=3 if agent is not None else 1)
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("A transcript must be a regular file")
                if (device, inode) != (
                    info.st_dev,
                    info.st_ino,
                ) or info.st_size < position:
                    if previous is not None and position:
                        gap(
                            position,
                            "Transcript replaced or truncated; earlier coverage may be missing",
                        )
                    device, inode, position, skipping = info.st_dev, info.st_ino, 0, 0
                if from_start:
                    position, skipping = 0, 0
                start = position
                chunk = read(stream, position, bool(skipping), budget)
                read_bytes = chunk.read_bytes
                # Validate identity before persisting any request or advancing a cursor.
                valid_session = position > 0
                valid_agent = agent is None or position > 0
                records: list[tuple[int, dict[str, object]] | Oversize] = []
                for line in chunk.records:
                    if not isinstance(line, Line):
                        if line.first:
                            gap(line.offset, "Oversized transcript record was skipped")
                        records.append(line)
                        continue
                    try:
                        raw = record(parse(line.data.decode("utf-8")), "Claude record")
                    except (HiveError, UnicodeError) as failure:
                        gap(line.offset, str(failure))
                        continue
                    if "sessionId" in raw:
                        if raw["sessionId"] != thread:
                            raise ValueError("Transcript is for another task")
                        valid_session = True
                    if agent is not None and "agentId" in raw:
                        if raw["agentId"] != agent:
                            raise ValueError("Transcript is for another subagent")
                        valid_agent = True
                    records.append((line.offset, raw))
                if not valid_session or not valid_agent:
                    raise ValueError(
                        "Claude transcript identity not yet observed; cursor retained"
                    )
                for item in records:
                    from hive.tool_store import observe as observe_parts

                    if isinstance(item, Oversize):
                        from hive.tool_store import oversized

                        oversized(connection, thread, key, item.offset, item.size)
                        continue
                    offset, raw = item
                    try:
                        if agent is not None and (
                            raw.get("isSidechain") is not True
                            or raw.get("agentId") != agent
                        ):
                            raise HiveError(
                                ErrorCode.INVALID_RECORD,
                                "Invalid Claude sidechain identity",
                            )
                        if agent is None and raw.get("type") == "cost-state":
                            from hive.claude_cost_state import save as save_cost_state

                            save_cost_state(connection, thread, raw)
                        event = decode(raw, thread)
                        if event is not None:
                            save(connection, event)
                        from hive.agent_store import observe

                        observe(connection, thread, raw)
                        observe_parts(connection, thread, key, offset, raw, event)
                    except HiveError as failure:
                        gap(offset, str(failure))
                position, skipping = chunk.position, int(chunk.skipping)
                size = os.fstat(stream.fileno()).st_size
                remaining = max(0, size - position)
                incomplete = (
                    chunk.incomplete or chunk.skipping
                ) and size <= start + read_bytes
        except (OSError, ValueError, HiveError) as failure:
            error = str(failure)
        facts_changed = connection.total_changes != facts_before
        connection.execute(
            "INSERT INTO sources(task,file,path,device,inode,position,skipping,scanned,remaining,incomplete,error,host) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task,file) DO UPDATE SET "
            "path=excluded.path,device=excluded.device,inode=excluded.inode,position=excluded.position,skipping=excluded.skipping,"
            "scanned=excluded.scanned,remaining=excluded.remaining,incomplete=excluded.incomplete,error=excluded.error,host=excluded.host",
            (
                thread,
                key,
                str(path),
                device,
                inode,
                position,
                skipping,
                datetime.now(UTC).isoformat(),
                remaining,
                None if incomplete is None else int(incomplete),
                error,
                Host.CLAUDE,
            ),
        )
        from hive.tool_allocation_store import refresh as refresh_allocations

        if facts_changed:
            refresh_allocations(connection, thread)
    if error is None:
        metadata(store, thread, path)
    return {
        "code": "TranscriptCollected",
        "task": thread,
        "host": Host.CLAUDE,
        "file": key,
        "read_bytes": read_bytes,
        "position": position,
        "remaining_bytes": remaining,
        "incomplete_tail": incomplete,
        "error": error,
    }


def files(store: UsageStore, thread: ThreadId, main: Path) -> tuple[Path, ...]:
    candidates = [
        main,
        *sorted((main.parent / str(thread) / "subagents").glob("agent-*.jsonl")),
    ]
    with store.connect(write=False) as connection:
        values: object = connection.execute(
            "SELECT file,scanned FROM sources WHERE task=?", (thread,)
        ).fetchall()
        from hive.jsonvalue import sequence

        attempted = {
            string(row(value, 2)[0], "source file", empty=True): string(
                row(value, 2)[1], "scan time"
            )
            for value in sequence(values, "source scans")
        }
    return tuple(
        sorted(
            candidates,
            key=lambda path: (
                attempted.get(file_key(thread, path), ""),
                file_key(thread, path),
            ),
        )
    )


def metadata(store: UsageStore, thread: ThreadId, path: Path) -> None:
    key = file_key(thread, path)
    if not key:
        return
    agent = key.removeprefix("agent-")
    tool: str | None = None
    kind: str | None = None
    description: str | None = None
    depth: int | None = None
    try:
        meta = path.with_suffix(".meta.json")
        regular(meta)
        with meta.open("rb") as stream:
            body = stream.read(MAX_LINE + 1)
        if len(body) > MAX_LINE:
            raise ValueError("Oversized agent metadata")
        data = record(parse(body.decode("utf-8")), "agent metadata")
        tool = (
            None
            if data.get("toolUseId") is None
            else string(data["toolUseId"], "spawning tool")
        )
        kind = (
            None
            if data.get("agentType") is None
            else string(data["agentType"], "agent type")
        )
        description = (
            None
            if data.get("description") is None
            else string(data["description"], "agent description", empty=True)
        )
        depth = (
            None
            if data.get("spawnDepth") is None
            else integer(data["spawnDepth"], "spawn depth")
        )
    except (OSError, ValueError, UnicodeError, HiveError):
        tool, kind, description, depth = None, None, None, None
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO claude_agents(task,agent,tool_use_id,agent_type,description,spawn_depth) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(task,agent) DO UPDATE SET tool_use_id=excluded.tool_use_id,agent_type=excluded.agent_type,description=excluded.description,spawn_depth=excluded.spawn_depth",
            (thread, agent, tool, kind, description, depth),
        )
