"""Claude per-file cursors and monotonic request updates share one transaction."""

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC
from pathlib import Path
from typing import BinaryIO

from hive.claude_usage import ClaudeResponse, Modifiers, decode
from hive.errors import ErrorCode, HiveError
from hive.identity import Host, ThreadId
from hive.jsonvalue import integer, parse, record, string
from hive.telemetry_store import TelemetryStore, row
from hive.transcript_batch import Batch, FileIdentity, regular
from hive.transcript_chunks import MAX_LINE, Oversize
from hive.usage import Tokens, timestamp, tokens


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
        event = replace(
            event,
            modifiers=Modifiers.read(
                parse(string(modifiers, "stored modifiers"))
            ).enrich(event.modifiers),
        )
        if (
            host != Host.CLAUDE
            or model != event.model
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
        "usage=excluded.usage,output=excluded.output,reasoning=excluded.reasoning,complete=excluded.complete,flags=excluded.flags,last_observed=excluded.last_observed,modifier_key=excluded.modifier_key,modifiers=excluded.modifiers",
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


@dataclass(frozen=True)
class ClaudeFile:
    task: ThreadId
    path: Path

    @property
    def source(self) -> FileIdentity:
        key = file_key(self.task, self.path)
        return FileIdentity(self.task, key, self.path, Host.CLAUDE, 3 if key else 1)

    def preflight(self, stream: BinaryIO) -> None:
        # Claude identity is distributed across records, not a fixed header.
        pass

    def replay_requested(self, connection: sqlite3.Connection) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM claude_modifier_replays WHERE task=? AND file=?",
                (self.task, self.source.file),
            ).fetchone()
            is not None
        )

    def consume(self, connection: sqlite3.Connection, batch: Batch) -> None:
        """Preflight the whole bounded chunk before persisting any request.

        A pending/mismatched session or child leaves the cursor at the start
        of the batch, including after inode replacement or an explicit replay.
        """
        thread, key = self.task, batch.source.file
        agent = key.removeprefix("agent-") if key else None
        device, inode = batch.cursor.device, batch.cursor.inode
        valid_session = batch.cursor.position > 0
        valid_agent = agent is None or batch.cursor.position > 0
        records = batch.records(connection, "Claude record")
        for item in records:
            if isinstance(item, Oversize):
                continue
            raw = item.value
            if "sessionId" in raw:
                if raw["sessionId"] != thread:
                    raise ValueError("Transcript is for another task")
                valid_session = True
            if agent is not None and "agentId" in raw:
                if raw["agentId"] != agent:
                    raise ValueError("Transcript is for another subagent")
                valid_agent = True
        if not valid_session or not valid_agent:
            raise ValueError(
                "Claude transcript identity not yet observed; cursor retained"
            )
        connection.execute(
            "DELETE FROM claude_modifier_replays WHERE task=? AND file=?", (thread, key)
        )
        for item in records:
            from hive.tool_store import observe as observe_parts

            if isinstance(item, Oversize):
                from hive.tool_store import oversized

                oversized(connection, thread, key, item.offset, item.size)
                continue
            offset, raw = item.offset, item.value
            try:
                if agent is not None and (
                    raw.get("isSidechain") is not True or raw.get("agentId") != agent
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
                    # Repair only this exact record's prior conflict;
                    # missing or replaced transcript evidence stays.
                    connection.execute(
                        "DELETE FROM gaps WHERE task=? AND file=? AND device=? AND inode=? AND position=? AND detail='Conflicting Claude response identity or usage'",
                        (thread, key, device, inode, offset),
                    )
                from hive.agent_store import observe

                observe(connection, thread, raw)
                observe_parts(connection, thread, key, offset, raw, event)
                if event is not None:
                    from hive.diagnostic_report import cache_event
                    from hive.diagnostic_store import Location
                    from hive.diagnostic_store import event as diagnostic_event

                    marker = cache_event(connection, thread, event.usage.response)
                    if marker is not None:
                        diagnostic_event(
                            connection,
                            Location(
                                Host.CLAUDE,
                                thread,
                                agent or "",
                                key,
                                offset,
                                device,
                                inode,
                            ),
                            marker[0],
                            marker[1],
                            ref=event.usage.response,
                        )

                from hive.diagnostic_store import Location
                from hive.diagnostic_store import observe as observe_diagnostics

                observe_diagnostics(
                    connection,
                    Location(
                        Host.CLAUDE,
                        thread,
                        agent or "",
                        key,
                        offset,
                        device,
                        inode,
                    ),
                    raw,
                )
            except HiveError as failure:
                batch.gap(connection, offset, str(failure))

    def finalize(self, connection: sqlite3.Connection, facts_changed: bool) -> None:
        if facts_changed:
            from hive.tool_allocation_store import refresh

            refresh(connection, self.task)


def files(store: TelemetryStore, thread: ThreadId, main: Path) -> tuple[Path, ...]:
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


def metadata(store: TelemetryStore, thread: ThreadId, path: Path) -> None:
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
