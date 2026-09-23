"""Derived usage and incremental offsets commit together, outside task locks."""

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hive import turn_model
from hive.errors import ErrorCode, HiveError
from hive.identity import CodexTaskId
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
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0.1)
        try:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sources (
                    task TEXT PRIMARY KEY, path TEXT NOT NULL,
                    device INTEGER NOT NULL, inode INTEGER NOT NULL,
                    position INTEGER NOT NULL, skipping INTEGER NOT NULL,
                    scanned TEXT NOT NULL, remaining INTEGER,
                    incomplete INTEGER, error TEXT);
                CREATE TABLE IF NOT EXISTS responses (
                    response TEXT PRIMARY KEY, task TEXT NOT NULL,
                    turn TEXT NOT NULL, observed TEXT NOT NULL,
                    usage TEXT, input INTEGER, cached INTEGER, cache_write INTEGER,
                    output INTEGER, reasoning INTEGER);
                CREATE INDEX IF NOT EXISTS responses_task ON responses(task);
                CREATE TABLE IF NOT EXISTS turn_models (
                    task TEXT NOT NULL, turn TEXT NOT NULL, model TEXT NOT NULL,
                    observed TEXT NOT NULL, conflicted INTEGER NOT NULL,
                    PRIMARY KEY(task, turn));
                CREATE TABLE IF NOT EXISTS response_estimates (
                    response TEXT NOT NULL, tier TEXT NOT NULL, quote TEXT NOT NULL,
                    PRIMARY KEY(response, tier));
                CREATE TABLE IF NOT EXISTS gaps (
                    task TEXT NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL,
                    position INTEGER NOT NULL, detail TEXT NOT NULL,
                    PRIMARY KEY (task, device, inode, position, detail));
            """)
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
        finally:
            connection.close()

    def collect(
        self,
        task: CodexTaskId,
        path: Path,
        *,
        budget: int = MAX_BATCH,
        from_start: bool = False,
    ) -> dict[str, object]:
        if not MAX_LINE < budget <= MAX_BATCH:
            raise HiveError(ErrorCode.INVALID_INPUT, "Invalid transcript byte budget")
        connection: sqlite3.Connection
        device: int
        inode: int
        with self.connect() as connection:
            previous: object = connection.execute(
                "SELECT device, inode, position, skipping FROM sources WHERE task = ?",
                (task,),
            ).fetchone()
            device, inode, position, skipping = (
                (0, 0, 0, 0)
                if previous is None
                else tuple(integer(v, "source cursor") for v in row(previous, 4))
            )
            remaining: int | None = None
            incomplete: bool | None = None
            read_bytes = 0
            error: str | None = None

            def gap(offset: int, detail: str) -> None:
                connection.execute(
                    "INSERT OR IGNORE INTO gaps VALUES (?, ?, ?, ?, ?)",
                    (task, device, inode, offset, detail),
                )

            try:
                # Nonblocking open lets the regular-file check reject pipes.
                descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
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
                    decode(first, task)
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
                    if from_start:
                        position, skipping = 0, 0
                    chunk_start = position
                    chunk = read(stream, position, bool(skipping), budget)
                    for line in chunk.records:
                        if not isinstance(line, Line):
                            gap(line.offset, "Oversized transcript record was skipped")
                            continue
                        try:
                            raw = parse(line.data.decode("utf-8"))
                            model = turn_model.decode(raw, task)
                            if model is not None:
                                turn_model.save(connection, model)
                            event = decode(raw, task)
                            if event is not None:
                                self.save(connection, event)
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
                "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(task) DO UPDATE SET path=excluded.path, device=excluded.device, "
                "inode=excluded.inode, position=excluded.position, skipping=excluded.skipping, "
                "scanned=excluded.scanned, remaining=excluded.remaining, "
                "incomplete=excluded.incomplete, error=excluded.error",
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
                ),
            )
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
            "SELECT task, turn, usage FROM responses WHERE response = ?",
            (event.response,),
        ).fetchone()
        if previous is not None:
            old_task, old_turn, old_usage = row(previous, 3)
            if (old_task, old_turn) != (event.owner.task, event.owner.turn) or (
                old_usage is not None and usage is not None and old_usage != usage
            ):
                raise HiveError(
                    ErrorCode.INVALID_RECORD,
                    "Conflicting native response identity or usage",
                )
            if old_usage is not None or usage is None:
                return
        counters: tuple[int | None, ...] = (
            (None,) * 5
            if isinstance(observed_tokens, MissingUsage)
            else (
                observed_tokens.input,
                observed_tokens.cached_input,
                observed_tokens.cache_write_input,
                observed_tokens.output,
                observed_tokens.reasoning_output,
            )
        )
        connection.execute(
            "INSERT INTO responses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(response) DO UPDATE SET usage=excluded.usage, input=excluded.input, "
            "cached=excluded.cached, cache_write=excluded.cache_write, "
            "output=excluded.output, reasoning=excluded.reasoning",
            (
                event.response,
                event.owner.task,
                event.owner.turn,
                event.observed.isoformat(),
                usage,
                *counters,
            ),
        )

    def report(self, task: CodexTaskId) -> dict[str, object]:
        with self.connect() as connection:
            cursor = connection.execute(
                "SELECT input, cached, cache_write, output, reasoning FROM responses WHERE task = ?",
                (task,),
            )
            observed, known = 0, 0
            totals = [0] * 5
            # Bounded result batches and Python integers preserve exact totals
            # even when many valid native counters exceed SQLite's sum range.
            while True:
                fetched: object = cursor.fetchmany(512)
                batch = sequence(fetched, "usage batch")
                if not batch:
                    break
                for raw in batch:
                    values = row(raw, 5)
                    observed += 1
                    if all(value is None for value in values):
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
            )
            summed: dict[str, object] | None = None
            if known:
                summed = tokens(dict(zip(counter_names, totals, strict=True))).value()
            return {
                "code": "ObservedUsage",
                "task": task,
                "observed_responses": observed,
                "responses_with_usage": known,
                "responses_missing_usage": observed - known,
                "known_tokens": summed,
                **source_status(connection, task),
                "api_equivalent_usd": None,
                "coverage": "Usage counters only; use hive cost --task for estimates. Bead attribution remains unavailable",
            }


def source_status(
    connection: sqlite3.Connection, task: CodexTaskId
) -> dict[str, object]:
    source: object = connection.execute(
        "SELECT scanned, remaining, incomplete, error FROM sources WHERE task = ?",
        (task,),
    ).fetchone()
    scan = None if source is None else row(source, 4)
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
        "last_scan": None if scan is None else string(scan[0], "scan time"),
        "remaining_bytes": (
            None
            if scan is None or scan[1] is None
            else integer(scan[1], "remaining bytes")
        ),
        "incomplete_tail": (
            None
            if scan is None or scan[2] is None
            else bool(integer(scan[2], "incomplete tail"))
        ),
        "source_error": (
            None if scan is None or scan[3] is None else string(scan[3], "source error")
        ),
    }
