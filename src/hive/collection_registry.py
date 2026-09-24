"""Disposable bead-link cache and bounded collector health."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hive.identity import CodexTaskId
from hive.jsonvalue import integer, sequence, string
from hive.thread_links import ThreadLink
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
                "CREATE TABLE IF NOT EXISTS collection_links (task TEXT NOT NULL, bead TEXT NOT NULL, relation TEXT NOT NULL, PRIMARY KEY(task,bead,relation))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS collection_gaps (detail TEXT PRIMARY KEY)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS collection_health (singleton INTEGER PRIMARY KEY CHECK(singleton=1), refreshed TEXT, error TEXT)"
            )
            yield connection

    def refresh(
        self,
        links: tuple[ThreadLink, ...] | None,
        gaps: tuple[str, ...] | None,
        error: str | None,
    ) -> None:
        with self.connect() as connection:
            if links is not None and gaps is not None:
                tasks = {link.task for link in links if link.collected}
                previous: object = connection.execute(
                    "SELECT task FROM collection_tasks"
                ).fetchall()
                old = {
                    CodexTaskId(string(row(value, 1)[0], "cached task"))
                    for value in sequence(previous, "cached tasks")
                }
                connection.executemany(
                    "DELETE FROM collection_tasks WHERE task=?",
                    [(task,) for task in old - tasks],
                )
                connection.executemany(
                    "INSERT OR IGNORE INTO collection_tasks(task) VALUES (?)",
                    [(task,) for task in tasks],
                )
                connection.execute("DELETE FROM collection_links")
                connection.executemany(
                    "INSERT INTO collection_links VALUES (?,?,?)",
                    [(link.task, link.bead, link.relation) for link in links],
                )
                connection.execute("DELETE FROM collection_gaps")
                connection.executemany(
                    "INSERT INTO collection_gaps VALUES (?)", [(gap,) for gap in gaps]
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
                CodexTaskId(string(row(value, 1)[0], "linked task"))
                for value in sequence(values, "linked tasks")
            )

    def associations(self, task: CodexTaskId) -> list[dict[str, str]]:
        with self.connect() as connection:
            values: object = connection.execute(
                "SELECT bead, relation FROM collection_links WHERE task=? ORDER BY bead, relation",
                (task,),
            ).fetchall()
            return [
                {
                    "bead": string(row(value, 2)[0], "bead"),
                    "relation": string(row(value, 2)[1], "relation"),
                }
                for value in sequence(values, "associations")
            ]

    def gaps(self) -> list[str]:
        with self.connect() as connection:
            values: object = connection.execute(
                "SELECT detail FROM collection_gaps ORDER BY detail LIMIT 100"
            ).fetchall()
            return [
                string(row(value, 1)[0], "association gap")
                for value in sequence(values, "association gaps")
            ]

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
                "UPDATE collection_tasks SET attempted=?, error=?, validated_path=COALESCE(?,validated_path) WHERE task=?",
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
                "SELECT refreshed,error FROM collection_health WHERE singleton=1"
            ).fetchone()
            refreshed, failure = (None, None) if health is None else row(health, 2)
            counts: object = connection.execute(
                "SELECT COUNT(*), COUNT(attempted), MIN(attempted), MAX(attempted) FROM collection_tasks"
            ).fetchone()
            total, tried, oldest, newest = row(counts, 4)
            failures: object = connection.execute(
                "SELECT task,error FROM collection_tasks WHERE error IS NOT NULL ORDER BY attempted DESC LIMIT 20"
            ).fetchall()
            uncollected: object = connection.execute(
                "SELECT COUNT(DISTINCT task) FROM collection_links WHERE task NOT IN (SELECT task FROM collection_tasks)"
            ).fetchone()
            gap_count: object = connection.execute(
                "SELECT COUNT(*) FROM collection_gaps"
            ).fetchone()
            return {
                "code": "CollectorStatus",
                "linked_threads": integer(total, "linked threads"),
                "database_bytes": self.usage.path.stat().st_size,
                "never_attempted": integer(total, "linked threads")
                - integer(tried, "attempted threads"),
                "registry_refreshed": refreshed,
                "registry_error": failure,
                "uncollected_threads": integer(
                    row(uncollected, 1)[0], "uncollected threads"
                ),
                "association_gaps": integer(row(gap_count, 1)[0], "association gaps"),
                "oldest_attempt": oldest,
                "latest_attempt": newest,
                "recent_failures": [
                    {
                        "task": string(row(value, 2)[0], "task"),
                        "error": string(row(value, 2)[1], "error"),
                    }
                    for value in sequence(failures, "failures")
                ],
            }
