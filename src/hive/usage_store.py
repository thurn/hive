"""Derived usage and incremental offsets commit together, outside task locks."""

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from hive import telemetry_schema, turn_model
from hive.errors import ErrorCode, HiveError
from hive.identity import AgentId, CodexTaskId, Host
from hive.jsonvalue import integer, parse, record, sequence, string
from hive.transcript_chunks import MAX_BATCH, MAX_LINE, Line, read
from hive.usage import MissingUsage, ResponseUsage, decode, tokens


def row(value: object, length: int) -> tuple[object, ...]:
    if not isinstance(value, tuple) or len(value) != length:
        raise HiveError(ErrorCode.INVALID_RECORD, "Invalid telemetry row")
    return tuple(value)


@dataclass(frozen=True)
class UsageStore:
    path: Path

    @contextmanager
    def connect(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        # Readers take no write lock. Contention that outlasts the bounded
        # timeout, for readers or writers, is reported as Busy.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0.1)
        try:
            telemetry_schema.prepare(connection, write=write)
            with connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode & 0xFF not in (
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            ):
                raise
            raise HiveError(ErrorCode.BUSY, "Telemetry database is busy") from error
        finally:
            connection.close()

    def collect(
        self,
        task: CodexTaskId,
        path: Path,
        *,
        budget: int = MAX_BATCH,
        from_start: bool = False,
        host: Host | None = None,
        agent: CodexTaskId | None = None,
    ) -> dict[str, object]:
        if not MAX_LINE < budget <= MAX_BATCH:
            raise HiveError(ErrorCode.INVALID_INPUT, "Invalid transcript byte budget")
        if host == Host.CLAUDE or (
            host is None
            and (path.name == f"{task}.jsonl" or path.parent.name == "subagents")
        ):
            from hive.claude_store import collect

            return collect(self, task, path, budget=budget, from_start=from_start)
        with self.connect() as mapping:
            from hive.codex_folding import root

            canonical = root(mapping, task)
            if canonical != task:
                agent, task = task, canonical
        identity = agent or task
        file: str = "" if agent is None else "codex-agent-" + agent
        connection: sqlite3.Connection
        device: int
        inode: int
        with self.connect() as connection:
            previous: object = connection.execute(
                "SELECT device, inode, position, skipping FROM sources WHERE task = ? AND file = ?",
                (task, file),
            ).fetchone()
            device, inode, position, skipping = (
                (0, 0, 0, 0)
                if previous is None
                else tuple(integer(v, "source cursor") for v in row(previous, 4))
            )
            diagnostic_replay = (
                connection.execute(
                    "SELECT 1 FROM diagnostic_replays WHERE task=? AND file=?",
                    (task, file),
                ).fetchone()
                is not None
            )
            remaining: int | None = None
            incomplete: bool | None = None
            read_bytes = 0
            mtime_ns = size = 0
            error: str | None = None

            def gap(offset: int, detail: str) -> None:
                connection.execute(
                    "INSERT OR IGNORE INTO gaps(task,file,device,inode,position,detail) VALUES (?, ?, ?, ?, ?, ?)",
                    (task, file, device, inode, offset, detail),
                )

            try:
                # Nonblocking open lets the regular-file check reject pipes.
                descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("A transcript must be a regular file")
                    header = stream.readline(MAX_LINE + 1)
                    if len(header) > MAX_LINE or not header.endswith(b"\n"):
                        raise ValueError(
                            "Native session header is incomplete or oversized"
                        )
                    first = record(parse(header.decode("utf-8")), "session header")
                    if first.get("type") != "session_meta":
                        raise ValueError(
                            "Transcript must start with native session metadata"
                        )
                    decode(first, identity)
                    mtime_ns, size = info.st_mtime_ns, info.st_size
                    if (device, inode) != (
                        info.st_dev,
                        info.st_ino,
                    ) or info.st_size < position:
                        if previous is not None and position:
                            gap(
                                position,
                                "Transcript replaced or truncated; earlier coverage may be missing",
                            )
                        device, inode, position, skipping = (
                            info.st_dev,
                            info.st_ino,
                            0,
                            0,
                        )
                    if from_start or diagnostic_replay:
                        position, skipping = 0, 0
                    connection.execute(
                        "DELETE FROM diagnostic_replays WHERE task=? AND file=?",
                        (task, file),
                    )
                    chunk_start = position
                    chunk = read(stream, position, bool(skipping), budget)
                    for line in chunk.records:
                        if not isinstance(line, Line):
                            if line.first:
                                gap(
                                    line.offset,
                                    "Oversized transcript record was skipped",
                                )
                            continue
                        try:
                            raw = record(
                                parse(line.data.decode("utf-8")), "native record"
                            )
                            from hive.diagnostic_store import Location
                            from hive.diagnostic_store import (
                                observe as observe_diagnostics,
                            )

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
                                self.save(connection, event)
                            observe_diagnostics(
                                connection,
                                Location(
                                    Host.CODEX,
                                    task,
                                    agent or "",
                                    file,
                                    line.offset,
                                    device,
                                    inode,
                                ),
                                raw,
                            )
                        except (HiveError, UnicodeError) as failure:
                            gap(line.offset, str(failure))
                    position, skipping = chunk.position, int(chunk.skipping)
                    read_bytes = chunk.read_bytes
                    size = os.fstat(stream.fileno()).st_size
                    remaining = max(0, size - position)
                    incomplete = (
                        chunk.incomplete or chunk.skipping
                    ) and size <= chunk_start + read_bytes
            except (OSError, ValueError, HiveError) as failure:
                error = str(failure)
                remaining, incomplete = None, None
            connection.execute(
                "INSERT INTO sources(task,path,device,inode,position,skipping,scanned,remaining,incomplete,error,file,mtime_ns,size) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(task,file) DO UPDATE SET path=excluded.path, device=excluded.device, "
                "inode=excluded.inode, position=excluded.position, skipping=excluded.skipping, "
                "scanned=excluded.scanned, remaining=excluded.remaining, "
                "incomplete=excluded.incomplete, error=excluded.error, mtime_ns=excluded.mtime_ns,size=excluded.size",
                (
                    task,
                    str(path),
                    device,
                    inode,
                    position,
                    skipping,
                    datetime.now(UTC).isoformat(),
                    remaining,
                    None if incomplete is None else int(incomplete),
                    error,
                    file,
                    mtime_ns,
                    size,
                ),
            )
            from hive.diagnostic_roles import finalize as finalize_roles

            finalize_roles(connection, task)
        return {
            "code": "TranscriptCollected",
            "task": task,
            "read_bytes": read_bytes,
            "position": position,
            "remaining_bytes": remaining,
            "incomplete_tail": incomplete,
            "error": error,
        }

    @staticmethod
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

    def report(self, task: CodexTaskId) -> dict[str, object]:
        with self.connect(write=False) as connection:
            cursor = connection.execute(
                "SELECT input, cached, cache_write, output, reasoning, cache_write_1h FROM responses WHERE task = ?",
                (task,),
            )
            observed, known = 0, 0
            totals = [0] * 6
            # Bounded result batches and Python integers preserve exact totals
            # even when many valid native counters exceed SQLite's sum range.
            while True:
                fetched: object = cursor.fetchmany(512)
                batch = sequence(fetched, "usage batch")
                if not batch:
                    break
                for raw in batch:
                    values = row(raw, 6)
                    observed += 1
                    if all(value is None for value in values[:5]):
                        continue
                    for index, value in enumerate(values):
                        totals[index] += integer(value, "stored token counter")
                    known += 1
            counter_names = (
                "input_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "output_tokens",
                "reasoning_output_tokens",
                "cache_write_1h_input_tokens",
            )
            host = stored_host(connection, task)
            summed: dict[str, object] | None = None
            if known:
                summed = tokens(dict(zip(counter_names, totals, strict=True))).value()
                # Keep the Codex usage report shape stable during migration.
                if host != Host.CLAUDE:
                    summed.pop("cache_write_1h_input_tokens")
            return {
                "code": "ObservedUsage",
                "host": host,
                "by_agent": agent_usage(connection, task),
                "task": task,
                "observed_responses": observed,
                "responses_with_usage": known,
                "responses_missing_usage": observed - known,
                "known_tokens": summed,
                **source_status(connection, task),
                "api_equivalent_usd": None,
                "coverage": "Usage counters only; use hive cost --task for thread estimates or hive cost --bead for ownership-based attribution",
            }


def source_status(
    connection: sqlite3.Connection, task: CodexTaskId
) -> dict[str, object]:
    fetched: object = connection.execute(
        "SELECT file,scanned,remaining,incomplete,error FROM sources WHERE task=? ORDER BY file",
        (task,),
    ).fetchall()
    sources = [row(value, 5) for value in sequence(fetched, "source files")]
    errors = [
        string(value[4], "source error") for value in sources if value[4] is not None
    ]
    remaining = [
        integer(value[2], "remaining bytes")
        for value in sources
        if value[2] is not None
    ]
    gap_count: object = connection.execute(
        "SELECT COUNT(*) FROM gaps WHERE task = ?", (task,)
    ).fetchone()
    gaps = integer(row(gap_count, 1)[0], "gap count")
    recent: object = connection.execute(
        "SELECT position, detail FROM gaps WHERE task = ? ORDER BY rowid DESC LIMIT 20",
        (task,),
    ).fetchall()
    details: list[dict[str, object]] = []
    for raw in sequence(recent, "recent gaps"):
        offset, detail = row(raw, 2)
        details.append(
            {
                "offset": integer(offset, "gap offset"),
                "detail": string(detail, "gap detail"),
            }
        )
    return {
        "parse_gaps": gaps,
        "recent_gaps": details,
        "last_scan": max(
            (string(value[1], "scan time") for value in sources), default=None
        ),
        "remaining_bytes": (
            sum(remaining) if sources and len(remaining) == len(sources) else None
        ),
        "incomplete_tail": (
            any(bool(value[3]) for value in sources) if sources else None
        ),
        "source_error": "; ".join(errors) if errors else None,
        "source_files": [
            {"file": value[0], "remaining_bytes": value[2], "error": value[4]}
            for value in sources
        ],
    }


def stored_host(connection: sqlite3.Connection, task: CodexTaskId) -> Host | None:
    values: object = connection.execute(
        "SELECT DISTINCT host FROM sources WHERE task=? UNION SELECT 'claude' FROM claude_request_events WHERE task=?",
        (task, task),
    ).fetchall()
    hosts = {
        string(row(value, 1)[0], "source host")
        for value in sequence(values, "source hosts")
    }
    return Host(next(iter(hosts))) if len(hosts) == 1 else None


def agent_usage(
    connection: sqlite3.Connection, task: CodexTaskId
) -> list[dict[str, object]]:
    values: object = connection.execute(
        "SELECT agent,usage FROM responses WHERE task=?", (task,)
    ).fetchall()
    groups: dict[str | None, tuple[int, dict[str, int]]] = {}
    for value in sequence(values, "agent usage"):
        agent, raw_usage = row(value, 2)
        name = None if agent is None else string(agent, "agent")
        count, totals = groups.get(name, (0, {}))
        if raw_usage is not None:
            for key, counter in (
                tokens(parse(string(raw_usage, "usage"))).value().items()
            ):
                totals[key] = totals.get(key, 0) + integer(counter, key)
        groups[name] = count + 1, totals
    result: list[dict[str, object]] = []
    for name, (count, totals) in groups.items():
        result.append(
            {"agent": name, "responses": count, "known_tokens": totals or None}
        )
    return result
