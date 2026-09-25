"""Read native session metadata incrementally, including old spawned-thread forms."""

import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from hive.identity import ThreadId
from hive.jsonvalue import integer, parse, sequence, string
from hive.project_config import Project
from hive.project_paths import resolve
from hive.thread_links import thread_id
from hive.usage_store import row


def parent(source: object) -> str | None:
    if source is None:
        return None
    value = (
        parse(source) if isinstance(source, str) and source.startswith("{") else source
    )
    if not isinstance(value, dict):
        return None
    subagent = value.get("subagent")
    if not isinstance(subagent, dict):
        return None
    spawn = subagent.get("thread_spawn")
    if not isinstance(spawn, dict):
        return None
    identity = spawn.get("parent_thread_id")
    if identity is None:
        return None
    parsed = thread_id(identity)
    if parsed is None:
        raise ValueError("Invalid Codex parent identity")
    return parsed


def resolve_root(
    native: sqlite3.Connection,
    cache: sqlite3.Connection,
    thread: str,
    has_source: bool,
    *,
    refresh: bool = False,
) -> str:
    saved: object = cache.execute(
        "SELECT root FROM codex_roots WHERE thread=?", (thread,)
    ).fetchone()
    if saved is not None and not refresh:
        return string(row(saved, 1)[0], "cached root")
    chain: list[str] = []
    current = thread
    while True:
        if current in chain or len(chain) >= 64:
            raise ValueError("Cyclic or excessive Codex parent chain")
        chain.append(current)
        raw: object = (
            native.execute(
                "SELECT source FROM threads WHERE id=?", (current,)
            ).fetchone()
            if has_source
            else None
        )
        if raw is None:
            if len(chain) > 1:
                raise ValueError("Codex spawned parent is missing from native index")
            return thread
        next_parent = parent(row(raw, 1)[0])
        if next_parent is None:
            break
        current = next_parent
    for member in chain:
        cache.execute(
            "INSERT INTO codex_roots VALUES (?,?) ON CONFLICT(thread) DO UPDATE SET root=excluded.root",
            (member, current),
        )
    return current


def state(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    value: object = connection.execute(
        "SELECT value FROM discovery_state WHERE key=?", (key,)
    ).fetchone()
    if value is None:
        return default
    result = row(value, 1)[0]
    if not isinstance(result, str):
        raise ValueError("Invalid discovery state")
    return result


def set_state(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO discovery_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def discover(
    cache: sqlite3.Connection,
    index: Path,
    projects: tuple[Project, ...],
    linked: tuple[ThreadId, ...],
    deadline: float,
) -> bool:
    native = sqlite3.connect(index.as_uri() + "?mode=ro", uri=True, timeout=0.05)
    try:
        native.execute("PRAGMA query_only=ON")
        native.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        columns = {
            string(row(r, 6)[1], "index column")
            for r in sequence(
                native.execute("PRAGMA table_info(threads)").fetchall(), "index columns"
            )
        }
        for identity in linked:
            if time.monotonic() >= deadline:
                return True
            resolve_root(native, cache, identity, "source" in columns)
        enabled = tuple(p for p in projects if p.observe_since is not None)
        required = {
            "id",
            "rollout_path",
            "cwd",
            "created_at_ms",
            "updated_at_ms",
            "source",
        }
        if not required <= columns:
            if not enabled:
                return False
            raise ValueError("Native index lacks project discovery columns")
        # Changing the observation boundary requires reclassifying old sessions.
        config = repr(projects)
        if state(cache, "codex_config") != config:
            set_state(cache, "codex_config", config)
            set_state(cache, "codex_watermark", "0")
            set_state(cache, "codex_page_time", "0")
            set_state(cache, "codex_page_id", "")
        watermark = int(state(cache, "codex_watermark", "0"))
        after_time = int(
            state(cache, "codex_page_time", str(max(0, watermark - 600000)))
        )
        after_id = state(cache, "codex_page_id")
        title = "title" if "title" in columns else "NULL"
        role = "agent_role" if "agent_role" in columns else "NULL"
        rows: object = native.execute(
            f"SELECT id,rollout_path,cwd,created_at_ms,updated_at_ms,source,{title},{role} FROM threads WHERE (updated_at_ms,id)>(?,?) ORDER BY updated_at_ms,id LIMIT 256",
            (after_time, after_id),
        ).fetchall()
        values = sequence(rows, "native project sessions")
        consumed = 0
        for value in values:
            if time.monotonic() >= deadline:
                break
            identity, path, cwd, created, updated, source, title_value, role_value = (
                row(value, 8)
            )
            task = thread_id(identity)
            if task is None:
                raise ValueError("Invalid native session identity")
            location = string(cwd, "session cwd")
            project, _ = resolve(cache, location, projects)
            started = datetime.fromtimestamp(
                integer(created, "creation time") / 1000, UTC
            )
            eligible = next(
                (
                    p
                    for p in enabled
                    if p.id == project
                    and p.observe_since is not None
                    and started.date() >= p.observe_since
                ),
                None,
            )
            resolve_root(native, cache, task, True, refresh=True)
            cache.execute(
                "INSERT INTO project_sessions VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(thread) DO UPDATE SET project=excluded.project,cwd=excluded.cwd,path=excluded.path,updated=excluded.updated,parent=excluded.parent,title=excluded.title,agent_role=excluded.agent_role",
                (
                    task,
                    "codex",
                    None if eligible is None else eligible.id,
                    location,
                    string(path, "rollout path"),
                    started.isoformat(),
                    integer(updated, "updated time"),
                    parent(source),
                    title_value,
                    role_value,
                ),
            )
            after_time, after_id = integer(updated, "updated time"), task
            watermark = max(watermark, after_time)
            consumed += 1
        set_state(cache, "codex_watermark", str(watermark))
        behind = consumed < len(values) or len(values) == 256
        set_state(
            cache,
            "codex_page_time",
            str(after_time if behind else max(0, watermark - 600000)),
        )
        set_state(cache, "codex_page_id", after_id if behind else "")
        return behind
    finally:
        native.close()
