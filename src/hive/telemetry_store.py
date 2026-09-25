"""Host-neutral transaction ownership for the observation database."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from hive import telemetry_schema
from hive.errors import ErrorCode, HiveError


def row(value: object, length: int) -> tuple[object, ...]:
    if not isinstance(value, tuple) or len(value) != length:
        raise HiveError(ErrorCode.INVALID_RECORD, "Invalid telemetry row")
    return tuple(value)


@dataclass(frozen=True)
class TelemetryStore:
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
