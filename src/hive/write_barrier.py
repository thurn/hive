"""One durable uncertainty stop for serialized Beads writes, never a task ledger."""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar, assert_never

from hive.errors import ErrorCode, HiveError
from hive.identity import BeadId
from hive.jsonvalue import parse, record, string

T = TypeVar("T")


@dataclass(frozen=True)
class RecordChange:
    bead: BeadId
    description: str
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DatabaseChange:
    description: str
    details: tuple[tuple[str, str], ...] = ()


type Intent = RecordChange | DatabaseChange


def value(intent: Intent) -> dict[str, object]:
    if isinstance(intent, RecordChange):
        return {
            "scope": "record",
            "bead": intent.bead,
            "change": intent.description,
            "details": dict(intent.details),
        }
    if isinstance(intent, DatabaseChange):
        return {
            "scope": "database",
            "change": intent.description,
            "details": dict(intent.details),
        }
    assert_never(intent)


def decode(raw: object) -> Intent:
    data = record(raw, "pending write")
    change = string(data.get("change"), "intended change")
    details = tuple(
        (key, string(item, f"write detail {key}", empty=True))
        for key, item in sorted(record(data.get("details"), "write details").items())
    )
    if data.get("scope") == "database":
        return DatabaseChange(change, details)
    if data.get("scope") == "record":
        from hive.bead_json import bead_id

        return RecordChange(bead_id(data.get("bead")), change, details)
    raise HiveError(ErrorCode.INVALID_RECORD, "Unknown pending write scope")


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class WriteBarrier:
    path: Path

    def blocked(self) -> bool:
        try:
            self.path.lstat()
            return True
        except FileNotFoundError:
            return False
        except OSError as error:
            raise HiveError(
                ErrorCode.RECOVERY_REQUIRED,
                f"Cannot inspect pending Beads write: {error}",
            ) from error

    def require_clear(self) -> None:
        if self.blocked():
            raise HiveError(
                ErrorCode.RECOVERY_REQUIRED,
                "A Beads write may still commit; use recovery inspect-write and "
                "establish stopped writers and server requests before repair",
            )

    def perform(self, intent: Intent, operation: Callable[[], T]) -> T:
        """Caller retains admission exclusion through validated acknowledgement.

        Keep the marker after process death, unknown errors, or ambiguous replies.
        Only a successful acknowledgement or a known no-effect refusal clears it.
        """
        self.require_clear()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(json.dumps(value(intent)))
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(self.path.parent)
        except OSError as error:
            raise HiveError(
                ErrorCode.RECOVERY_REQUIRED,
                f"Cannot persist the pending write marker: {error}",
            ) from error
        try:
            result = operation()
        except HiveError as error:
            if not error.uncertain:
                self._clear()
            raise
        self._clear()
        return result

    def _clear(self) -> None:
        try:
            self.path.unlink()
            sync_directory(self.path.parent)
        except OSError as error:
            raise HiveError(
                ErrorCode.RECOVERY_REQUIRED,
                f"Cannot finish the pending write marker cleanup: {error}",
                uncertain=True,
            ) from error

    def inspect(self) -> dict[str, object]:
        """Read without Beads or a lock; an in-flight partial marker is unknown."""
        base: dict[str, object] = {
            "code": "WriteInspection",
            "path": str(self.path),
            "blocked": True,
            "write": None,
            "problem": None,
        }
        try:
            if not self.blocked():
                return {**base, "blocked": False}
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor) as stream:
                text = stream.read(1_048_577)
            if len(text) > 1_048_576:
                raise ValueError("Pending write marker exceeds 1 MiB")
            return {**base, "write": value(decode(parse(text)))}
        except (OSError, ValueError, HiveError) as error:
            return {**base, "problem": str(error)}
