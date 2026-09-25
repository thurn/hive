"""Codex header authority, record decoding and response persistence."""

import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO

from hive import turn_model
from hive.diagnostic_store import Location
from hive.diagnostic_store import observe as observe_diagnostics
from hive.errors import ErrorCode, HiveError
from hive.identity import AgentId, Host, ThreadId
from hive.jsonvalue import parse, record, string
from hive.telemetry_store import row
from hive.transcript_batch import Batch, FileIdentity
from hive.transcript_chunks import MAX_LINE, Oversize
from hive.usage import MissingUsage, ResponseUsage, decode, tokens


@dataclass(frozen=True)
class CodexFile:
    task: ThreadId
    path: Path
    agent: ThreadId | None = None

    @property
    def source(self) -> FileIdentity:
        return FileIdentity(
            self.task,
            "" if self.agent is None else "codex-agent-" + self.agent,
            self.path,
            Host.CODEX,
        )

    def preflight(self, stream: BinaryIO) -> None:
        stream.seek(0)
        header = stream.readline(MAX_LINE + 1)
        if len(header) > MAX_LINE or not header.endswith(b"\n"):
            raise ValueError("Native session header is incomplete or oversized")
        first = record(parse(header.decode("utf-8")), "session header")
        if first.get("type") != "session_meta":
            raise ValueError("Transcript must start with native session metadata")
        decode(first, self.agent or self.task)

    def replay_requested(self, connection: sqlite3.Connection) -> bool:
        return False

    def consume(self, connection: sqlite3.Connection, batch: Batch) -> None:
        task, agent = self.task, self.agent
        identity = agent or task
        file = batch.source.file
        device, inode = batch.cursor.device, batch.cursor.inode
        for item in batch.records(connection, "native record"):
            if isinstance(item, Oversize):
                continue
            raw = item.value
            try:
                model = turn_model.decode(raw, identity)
                if model is not None:
                    model = replace(
                        model,
                        owner=replace(
                            model.owner,
                            thread=task,
                            agent=None if agent is None else AgentId(agent),
                        ),
                    )
                    turn_model.save(connection, model)
                event = decode(raw, identity)
                if event is not None:
                    event = replace(
                        event,
                        owner=replace(
                            event.owner,
                            thread=task,
                            agent=None if agent is None else AgentId(agent),
                        ),
                    )
                    save(connection, event)
                observe_diagnostics(
                    connection,
                    Location(
                        Host.CODEX,
                        task,
                        agent or "",
                        file,
                        item.offset,
                        device,
                        inode,
                    ),
                    raw,
                )
            except (HiveError, UnicodeError) as failure:
                batch.gap(connection, item.offset, str(failure))

    def finalize(self, connection: sqlite3.Connection, facts_changed: bool) -> None:
        from hive.diagnostic_roles import finalize

        finalize(connection, self.task)


def save(connection: sqlite3.Connection, event: ResponseUsage) -> None:
    observed_tokens = event.tokens
    usage = (
        None
        if isinstance(observed_tokens, MissingUsage)
        else json.dumps(observed_tokens.value(), sort_keys=True)
    )
    previous: object = connection.execute(
        "SELECT task, turn, usage, host, agent FROM responses WHERE response = ?",
        (event.response,),
    ).fetchone()
    if previous is not None:
        old_task, old_turn, old_usage, old_host, old_agent = row(previous, 5)
        if (old_task, old_turn, old_host, old_agent) != (
            event.owner.task,
            event.owner.turn,
            event.owner.host,
            event.owner.agent,
        ) or (
            old_usage is not None
            and usage is not None
            and tokens(parse(string(old_usage, "stored usage"))) != observed_tokens
        ):
            raise HiveError(
                ErrorCode.INVALID_RECORD,
                "Conflicting native response identity or usage",
            )
        if old_usage is not None or usage is None:
            return
    counters: tuple[int | None, ...] = (
        (None,) * 5 + (0,)
        if isinstance(observed_tokens, MissingUsage)
        else (
            observed_tokens.input,
            observed_tokens.cached_input,
            observed_tokens.cache_write_input,
            observed_tokens.output,
            observed_tokens.reasoning_output,
            observed_tokens.cache_write_1h_input,
        )
    )
    connection.execute(
        "INSERT INTO responses(response,task,turn,observed,usage,input,cached,cache_write,output,reasoning,cache_write_1h,host,agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(response) DO UPDATE SET usage=excluded.usage, input=excluded.input, "
        "cached=excluded.cached, cache_write=excluded.cache_write, "
        "output=excluded.output, reasoning=excluded.reasoning, cache_write_1h=excluded.cache_write_1h",
        (
            event.response,
            event.owner.task,
            event.owner.turn,
            event.observed.isoformat(),
            usage,
            *counters,
            event.owner.host,
            event.owner.agent,
        ),
    )
