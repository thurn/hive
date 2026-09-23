"""Derived enrollment cache and collection health; Beads remains authoritative."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hive.identity import CodexTaskId
from hive.jsonvalue import integer, sequence, string
from hive.usage_store import UsageStore, row


@dataclass(frozen=True)
class CollectionRegistry:
    usage: UsageStore

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self.usage.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS collection_tasks (task TEXT PRIMARY KEY, attempted TEXT, error TEXT, validated_path TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS collection_health (singleton INTEGER PRIMARY KEY CHECK(singleton=1), refreshed TEXT, error TEXT)"
            )
            yield connection

    def refresh(self, tasks: tuple[CodexTaskId, ...] | None, error: str | None) -> None:
        with self.connect() as connection:
            if tasks is not None:
                previous: object = connection.execute(
                    "SELECT task FROM collection_tasks"
                ).fetchall()
                old = {
                    CodexTaskId(string(row(v, 1)[0], "enrolled task"))
                    for v in sequence(previous, "enrolled tasks")
                }
                connection.executemany(
                    "DELETE FROM collection_tasks WHERE task=?",
                    [(task,) for task in old - set(tasks)],
                )
                connection.executemany(
                    "INSERT OR IGNORE INTO collection_tasks(task) VALUES (?)",
                    [(task,) for task in tasks],
                )
                connection.execute(
                    "INSERT INTO collection_health VALUES (1, ?, NULL) ON CONFLICT(singleton) DO UPDATE SET refreshed=excluded.refreshed, error=NULL",
                    (datetime.now(UTC).isoformat(),),
                )
            else:
                connection.execute(
                    "INSERT INTO collection_health VALUES (1, NULL, ?) ON CONFLICT(singleton) DO UPDATE SET error=excluded.error",
                    (error,),
                )

    def next(self, limit: int) -> tuple[CodexTaskId, ...]:
        with self.connect() as connection:
            values: object = connection.execute(
                "SELECT task FROM collection_tasks ORDER BY attempted, task LIMIT ?",
                (limit,),
            ).fetchall()
            return tuple(
                CodexTaskId(string(row(v, 1)[0], "enrolled task"))
                for v in sequence(values, "collection tasks")
            )

    def cached_path(self, task: CodexTaskId) -> Path | None:
        with self.connect() as connection:
            value: object = connection.execute(
                "SELECT validated_path FROM collection_tasks WHERE task=?", (task,)
            ).fetchone()
            return (
                None
                if value is None or row(value, 1)[0] is None
                else Path(string(row(value, 1)[0], "cached transcript"))
            )

    def attempted(
        self, task: CodexTaskId, error: str | None, validated_path: Path | None
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE collection_tasks SET attempted=?, error=?, "
                "validated_path=COALESCE(?, validated_path) WHERE task=?",
                (
                    datetime.now(UTC).isoformat(),
                    error,
                    None if validated_path is None else str(validated_path),
                    task,
                ),
            )

    def status(self) -> dict[str, object]:
        with self.connect() as connection:
            health: object = connection.execute(
                "SELECT refreshed, error FROM collection_health WHERE singleton=1"
            ).fetchone()
            refreshed, failure = (None, None) if health is None else row(health, 2)
            counts: object = connection.execute(
                "SELECT COUNT(*), COUNT(attempted), MIN(attempted), MAX(attempted) FROM collection_tasks"
            ).fetchone()
            total, tried, oldest, newest = row(counts, 4)
            failures: object = connection.execute(
                "SELECT task,error FROM collection_tasks WHERE error IS NOT NULL ORDER BY attempted DESC LIMIT 20"
            ).fetchall()
            return {
                "code": "CollectorStatus",
                "enrolled": integer(total, "enrolled tasks"),
                "never_attempted": integer(total, "enrolled tasks")
                - integer(tried, "attempted tasks"),
                "registry_refreshed": (
                    None if refreshed is None else string(refreshed, "registry refresh")
                ),
                "registry_error": (
                    None if failure is None else string(failure, "registry failure")
                ),
                "oldest_attempt": (
                    None if oldest is None else string(oldest, "oldest attempt")
                ),
                "latest_attempt": (
                    None if newest is None else string(newest, "latest attempt")
                ),
                "recent_failures": [
                    {
                        "task": string(row(v, 2)[0], "task"),
                        "error": string(row(v, 2)[1], "collection error"),
                    }
                    for v in sequence(failures, "collection failures")
                ],
            }
