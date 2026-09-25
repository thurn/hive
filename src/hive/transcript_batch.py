"""One bounded file/cursor transaction shared by native transcript adapters.

The lifecycle owns opening, replacement/replay resets and cursor persistence.
Adapters preflight identity before saving requests. Rejected/pending batches
retain their starting cursor, while SQLite failures roll back observations and
cursor together. Every file (including each subagent) has its own cursor.
"""

import os
import sqlite3
import stat
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Protocol

from hive.errors import ErrorCode, HiveError
from hive.identity import Host, ThreadId
from hive.jsonvalue import integer, parse, record
from hive.telemetry_store import TelemetryStore, row
from hive.transcript_chunks import MAX_BATCH, MAX_LINE, Chunk, Line, Oversize, read


@dataclass(frozen=True)
class FileIdentity:
    task: ThreadId
    file: str
    path: Path
    host: Host
    ancestors: int = 0


@dataclass(frozen=True)
class Cursor:
    device: int = 0
    inode: int = 0
    position: int = 0
    skipping: bool = False


def gap(
    connection: sqlite3.Connection,
    source: FileIdentity,
    cursor: Cursor,
    offset: int,
    detail: str,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO gaps(task,file,device,inode,position,detail) VALUES (?,?,?,?,?,?)",
        (source.task, source.file, cursor.device, cursor.inode, offset, detail),
    )


@dataclass(frozen=True)
class Record:
    offset: int
    value: dict[str, object]


@dataclass(frozen=True)
class Batch:
    source: FileIdentity
    cursor: Cursor
    chunk: Chunk

    def gap(self, connection: sqlite3.Connection, offset: int, detail: str) -> None:
        gap(connection, self.source, self.cursor, offset, detail)

    def records(
        self, connection: sqlite3.Connection, label: str
    ) -> tuple[Record | Oversize, ...]:
        records: list[Record | Oversize] = []
        for line in self.chunk.records:
            if isinstance(line, Line):
                try:
                    records.append(
                        Record(
                            line.offset, record(parse(line.data.decode("utf-8")), label)
                        )
                    )
                except (HiveError, UnicodeError) as error:
                    self.gap(connection, line.offset, str(error))
            else:
                if line.first:
                    self.gap(
                        connection,
                        line.offset,
                        "Oversized transcript record was skipped",
                    )
                records.append(line)
        return tuple(records)


class Adapter(Protocol):
    @property
    def source(self) -> FileIdentity: ...

    def preflight(self, stream: BinaryIO) -> None: ...
    def replay_requested(self, connection: sqlite3.Connection) -> bool: ...
    def consume(self, connection: sqlite3.Connection, batch: Batch) -> None: ...
    def finalize(self, connection: sqlite3.Connection, facts_changed: bool) -> None: ...


@dataclass(frozen=True)
class Scanned:
    cursor: Cursor
    read_bytes: int
    remaining: int
    incomplete: bool


@dataclass(frozen=True)
class FailedScan:
    cursor: Cursor
    read_bytes: int
    error: str


Scan = Scanned | FailedScan


def regular(path: Path, *, parents: int = 0) -> None:
    if any(part.is_symlink() for part in (path, *list(path.parents)[:parents])):
        raise ValueError("Transcript symlinks are not allowed")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("A transcript must be a regular file")


def collect(
    store: TelemetryStore, adapter: Adapter, *, budget: int, from_start: bool
) -> Scan:
    if not MAX_LINE < budget <= MAX_BATCH:
        raise HiveError(ErrorCode.INVALID_INPUT, "Invalid transcript byte budget")
    source = adapter.source
    with store.connect() as connection:
        previous: object = connection.execute(
            "SELECT device,inode,position,skipping FROM sources WHERE task=? AND file=?",
            (source.task, source.file),
        ).fetchone()
        cursor = Cursor()
        if previous is not None:
            device, inode, position, skipping = tuple(
                integer(v, "source cursor") for v in row(previous, 4)
            )
            cursor = Cursor(device, inode, position, bool(skipping))
        diagnostic_replay = (
            connection.execute(
                "SELECT 1 FROM diagnostic_replays WHERE task=? AND file=?",
                (source.task, source.file),
            ).fetchone()
            is not None
        )
        replay = from_start or diagnostic_replay or adapter.replay_requested(connection)
        read_bytes = mtime_ns = size = 0
        facts_before = connection.total_changes
        outcome: Scan
        try:
            regular(source.path, parents=source.ancestors)
            descriptor = os.open(
                source.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("A transcript must be a regular file")
                adapter.preflight(stream)
                mtime_ns, size = info.st_mtime_ns, info.st_size
                if (cursor.device, cursor.inode) != (
                    info.st_dev,
                    info.st_ino,
                ) or size < cursor.position:
                    if previous is not None and cursor.position:
                        gap(
                            connection,
                            source,
                            cursor,
                            cursor.position,
                            "Transcript replaced or truncated; earlier coverage may be missing",
                        )
                    cursor = Cursor(info.st_dev, info.st_ino)
                if replay:
                    cursor = replace(cursor, position=0, skipping=False)
                chunk = read(stream, cursor.position, cursor.skipping, budget)
                read_bytes = chunk.read_bytes
                adapter.consume(connection, Batch(source, cursor, chunk))
                connection.execute(
                    "DELETE FROM diagnostic_replays WHERE task=? AND file=?",
                    (source.task, source.file),
                )
                start = cursor.position
                cursor = replace(
                    cursor, position=chunk.position, skipping=chunk.skipping
                )
                size = os.fstat(stream.fileno()).st_size
                outcome = Scanned(
                    cursor,
                    read_bytes,
                    max(0, size - cursor.position),
                    (chunk.incomplete or chunk.skipping) and size <= start + read_bytes,
                )
        except (OSError, ValueError, HiveError) as error:
            outcome = FailedScan(cursor, read_bytes, str(error))
        facts_changed = connection.total_changes != facts_before
        connection.execute(
            "INSERT INTO sources(task,file,path,device,inode,position,skipping,scanned,remaining,incomplete,error,host,mtime_ns,size) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task,file) DO UPDATE SET "
            "path=excluded.path,device=excluded.device,inode=excluded.inode,position=excluded.position,skipping=excluded.skipping,"
            "scanned=excluded.scanned,remaining=excluded.remaining,incomplete=excluded.incomplete,error=excluded.error,host=excluded.host,mtime_ns=excluded.mtime_ns,size=excluded.size",
            (
                source.task,
                source.file,
                str(source.path),
                cursor.device,
                cursor.inode,
                cursor.position,
                int(cursor.skipping),
                datetime.now(UTC).isoformat(),
                outcome.remaining if isinstance(outcome, Scanned) else None,
                int(outcome.incomplete) if isinstance(outcome, Scanned) else None,
                outcome.error if isinstance(outcome, FailedScan) else None,
                source.host,
                mtime_ns,
                size,
            ),
        )
        adapter.finalize(connection, facts_changed)
    return outcome


def value(task: ThreadId, outcome: Scan) -> dict[str, object]:
    return {
        "code": "TranscriptCollected",
        "task": task,
        "read_bytes": outcome.read_bytes,
        "position": outcome.cursor.position,
        "remaining_bytes": outcome.remaining if isinstance(outcome, Scanned) else None,
        "incomplete_tail": outcome.incomplete if isinstance(outcome, Scanned) else None,
        "error": outcome.error if isinstance(outcome, FailedScan) else None,
    }
