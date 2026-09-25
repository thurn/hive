"""Bounded reloadable ingestion; raw files disappear only after durable rows."""

import json
import os
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hive.claude_events import decode
from hive.errors import HiveError
from hive.event_store import gap, save
from hive.identity import ThreadId
from hive.jsonvalue import integer, parse, record, sequence
from hive.otlp_storage import private_write
from hive.usage_store import UsageStore, row

MAX_INGEST = 16 * 1024 * 1024
MAX_FILE = 4 * 1024 * 1024


@dataclass(frozen=True)
class Entry:
    raw: object
    resource: object


def entries(value: object) -> tuple[Entry, ...]:
    result: list[Entry] = []
    for resource in sequence(
        record(value, "OTLP body").get("resourceLogs"), "resource logs"
    ):
        try:
            data = record(resource, "resource logs")
            attributes = record(data.get("resource", {}), "resource").get(
                "attributes", []
            )
            scopes = sequence(data.get("scopeLogs"), "scope logs")
        except HiveError:
            result.append(Entry(None, []))
            continue
        for scope in scopes:
            try:
                values = sequence(
                    record(scope, "scope logs").get("logRecords"), "log records"
                )
            except HiveError:
                result.append(Entry(None, attributes))
                continue
            result.extend(Entry(item, attributes) for item in values)
    return tuple(result)


def ingest(store: UsageStore, state: Path, deadline: float) -> dict[str, object]:
    deadline = min(deadline, time.monotonic() + 2)
    size = 0
    accepted = 0
    rejected = 0
    spool = state / "otlp-spool"
    if spool.is_symlink():
        return {"event_ingest_error": "OTLP spool must not be a symlink"}
    for path in sorted(spool.glob("*.json")):
        if time.monotonic() >= deadline:
            break
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            charged = min(info.st_size, MAX_FILE + 1)
            if size + charged > MAX_INGEST:
                break
            size += charged
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                actual = os.fstat(stream.fileno())
                if not stat.S_ISREG(actual.st_mode) or actual.st_size > MAX_FILE:
                    values: tuple[Entry, ...] | None = None
                else:
                    body = stream.read(MAX_FILE + 1)
                    try:
                        values = (
                            entries(parse(body.decode("utf-8")))
                            if len(body) <= MAX_FILE
                            else None
                        )
                    except (HiveError, UnicodeError):
                        values = None
            received = datetime.fromtimestamp(actual.st_mtime, UTC)
            with store.connect() as connection:
                previous: object = connection.execute(
                    "SELECT position,device,inode,size FROM otlp_ingest_files WHERE file=?",
                    (path.name,),
                ).fetchone()
                position = 0
                if previous is not None:
                    offset, device, inode, old_size = row(previous, 4)
                    if (device, inode, old_size) == (
                        actual.st_dev,
                        actual.st_ino,
                        actual.st_size,
                    ):
                        position = integer(offset, "event ingest cursor")
                    else:
                        gap(
                            connection,
                            path.name,
                            0,
                            "Spool file replaced during ingestion",
                            observed=received,
                        )
                if values is None:
                    gap(
                        connection,
                        path.name,
                        0,
                        "Invalid OTLP/JSON body",
                        observed=received,
                    )
                    rejected += 1
                    complete = True
                else:
                    while position < len(values) and time.monotonic() < deadline:
                        item = values[position]
                        task: ThreadId | None = None
                        try:
                            event = decode(item.raw, item.resource)
                            task = event.task
                            save(connection, event)
                            accepted += 1
                        except HiveError as error:
                            gap(
                                connection,
                                path.name,
                                position,
                                error.detail,
                                task,
                                received,
                            )
                            rejected += 1
                        position += 1
                    complete = position == len(values)
                connection.execute(
                    "INSERT INTO otlp_ingest_files(file,position,device,inode,size) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(file) DO UPDATE SET position=excluded.position,device=excluded.device,inode=excluded.inode,size=excluded.size",
                    (path.name, position, actual.st_dev, actual.st_ino, actual.st_size),
                )
            if complete:
                if values is None:
                    quarantine = spool / "rejected"
                    if quarantine.is_symlink():
                        raise ValueError(
                            "OTLP rejected directory must not be a symlink"
                        )
                    quarantine.mkdir(mode=0o700, exist_ok=True)
                    quarantine.chmod(0o700)
                    private_write(
                        quarantine / path.name,
                        json.dumps(
                            {"error": "Invalid OTLP/JSON body", "attributes": {}}
                        ).encode(),
                    )
                path.unlink(missing_ok=True)
                with store.connect() as connection:
                    connection.execute(
                        "DELETE FROM otlp_ingest_files WHERE file=?", (path.name,)
                    )
        except (OSError, HiveError, ValueError) as error:
            return {
                "event_ingest_error": str(error),
                "event_ingested_records": accepted,
                "event_rejected_records": rejected,
            }
    return {
        "event_ingest_error": None,
        "event_ingested_records": accepted,
        "event_rejected_records": rejected,
        "event_ingested_bytes": size,
    }
