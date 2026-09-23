"""Read-only native path lookup; no activity or ownership inference."""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.identity import CodexTaskId
from hive.jsonvalue import sequence, string
from hive.usage_store import row


@dataclass(frozen=True)
class NativeTranscripts:
    index: Path

    def find(self, tasks: tuple[CodexTaskId, ...]) -> dict[CodexTaskId, Path]:
        if not tasks:
            return {}
        if len(tasks) > 64:
            raise HiveError(ErrorCode.INVALID_INPUT, "Native lookup exceeds 64 tasks")
        deadline: float = time.monotonic() + 0.25
        connection = sqlite3.connect(
            self.index.as_uri() + "?mode=ro", uri=True, timeout=0.1
        )
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.set_progress_handler(
                lambda: int(time.monotonic() > deadline), 1000
            )
            values: object = connection.execute(
                "SELECT id, rollout_path FROM threads WHERE id IN ("
                + ",".join("?" for _ in tasks)
                + ")",
                tasks,
            ).fetchall()
        finally:
            connection.close()
        result: dict[CodexTaskId, Path] = {}
        for value in sequence(values, "native transcript paths"):
            identifier, raw_path = row(value, 2)
            task = CodexTaskId(string(identifier, "native task"))
            path = Path(string(raw_path, "native transcript path"))
            if task not in tasks or task in result or not path.is_absolute():
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Invalid native transcript index"
                )
            result[task] = path
        return result
