"""Disposable bead-link cache and bounded collector health."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hive.bead_history import historical
from hive.bead_history import status as history_status
from hive.identity import CodexTaskId, Host
from hive.jsonvalue import integer, sequence, string
from hive.otlp_storage import status as otlp_status
from hive.thread_links import ThreadLink
from hive.usage_store import UsageStore, row


@dataclass(frozen=True)
class CollectionRegistry:
    usage: UsageStore

    @contextmanager
    def connect(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        with self.usage.connect(write=write) as connection:
            yield connection

    def refresh(
        self,
        links: tuple[ThreadLink, ...] | None,
        gaps: tuple[str, ...] | None,
        error: str | None,
    ) -> None:
        with self.connect() as connection:
            if links is not None and gaps is not None:
                links = tuple(sorted(set(links) | set(historical(connection))))
                tasks = {link.task for link in links}
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
                    "INSERT INTO collection_links(task,bead,relation) VALUES (?,?,?)",
                    [(link.task, link.bead, link.relation) for link in links],
                )
                connection.execute("DELETE FROM collection_gaps")
                connection.executemany(
                    "INSERT INTO collection_gaps(detail) VALUES (?)",
                    [(gap,) for gap in gaps],
                )
                connection.execute(
                    "INSERT INTO collection_health(singleton,refreshed,error) VALUES (1, ?, NULL) ON CONFLICT(singleton) DO UPDATE SET refreshed=excluded.refreshed, error=NULL",
                    (datetime.now(UTC).isoformat(),),
                )
            else:
                connection.execute(
                    "INSERT INTO collection_health(singleton,refreshed,error) VALUES (1, NULL, ?) ON CONFLICT(singleton) DO UPDATE SET error=excluded.error",
                    (error,),
                )

    def next(self, limit: int) -> tuple[CodexTaskId, ...]:
        with self.connect(write=False) as connection:
            values: object = connection.execute(
                "SELECT task FROM collection_tasks ORDER BY attempted, task LIMIT ?",
                (limit,),
            ).fetchall()
            return tuple(
                CodexTaskId(string(row(value, 1)[0], "linked task"))
                for value in sequence(values, "linked tasks")
            )

    def associations(self, task: CodexTaskId) -> list[dict[str, str]]:
        with self.connect(write=False) as connection:
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
        with self.connect(write=False) as connection:
            values: object = connection.execute(
                "SELECT detail FROM collection_gaps ORDER BY detail LIMIT 100"
            ).fetchall()
            return [
                string(row(value, 1)[0], "association gap")
                for value in sequence(values, "association gaps")
            ]

    def cached_path(self, task: CodexTaskId) -> Path | None:
        with self.connect(write=False) as connection:
            value: object = connection.execute(
                "SELECT validated_path FROM collection_tasks WHERE task=?", (task,)
            ).fetchone()
            return (
                None
                if value is None or row(value, 1)[0] is None
                else Path(string(row(value, 1)[0], "cached transcript"))
            )

    def cached_host(self, task: CodexTaskId) -> Host | None:
        with self.connect(write=False) as connection:
            value: object = connection.execute(
                "SELECT host FROM collection_tasks WHERE task=?", (task,)
            ).fetchone()
            return (
                None
                if value is None or row(value, 1)[0] is None
                else Host(string(row(value, 1)[0], "cached host"))
            )

    def attempted(
        self,
        task: CodexTaskId,
        error: str | None,
        validated_path: Path | None,
        host: Host | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE collection_tasks SET attempted=?, error=?, validated_path=COALESCE(?,validated_path), host=? WHERE task=?",
                (
                    datetime.now(UTC).isoformat(),
                    error,
                    None if validated_path is None else str(validated_path),
                    host,
                    task,
                ),
            )

    def status(self) -> dict[str, object]:
        with self.connect(write=False) as connection:
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
                "SELECT COUNT(*) FROM collection_tasks WHERE host IS NULL"
            ).fetchone()
            gap_count: object = connection.execute(
                "SELECT COUNT(*) FROM collection_gaps"
            ).fetchone()
            return {
                "code": "CollectorStatus",
                **history_status(connection),
                **otlp_status(self.usage.path.parent),
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
