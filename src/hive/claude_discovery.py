"""Discover session cwd and start time without retaining transcript text."""

import os
import sqlite3
import stat
import time
from pathlib import Path

from hive.claude_store import regular
from hive.codex_discovery import set_state, state
from hive.jsonvalue import integer, parse, record, sequence, string
from hive.project_config import Project
from hive.project_paths import resolve
from hive.thread_links import thread_id
from hive.transcript_chunks import MAX_BATCH, Line, read
from hive.usage import timestamp
from hive.usage_store import row


def inspect(
    connection: sqlite3.Connection, path: Path, projects: tuple[Project, ...]
) -> None:
    regular(path, parents=1)
    info = path.stat()
    saved: object = connection.execute(
        "SELECT device,inode,size,position,matched FROM claude_discovery_files WHERE path=?",
        (str(path),),
    ).fetchone()
    before = (
        None
        if saved is None
        else tuple(integer(v, "Claude discovery cursor") for v in row(saved, 5))
    )
    if (
        before is not None
        and before[:2] == (info.st_dev, info.st_ino)
        and (before[4] or before[2] == info.st_size)
    ):
        return
    position = (
        before[3]
        if before is not None
        and before[:2] == (info.st_dev, info.st_ino)
        and before[3] <= info.st_size
        else 0
    )
    matched = False
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Discovery transcript is not regular")
        chunk = read(stream, position, False, MAX_BATCH)
        for line in chunk.records:
            if not isinstance(line, Line):
                continue
            value = record(parse(line.data.decode()), "Claude discovery record")
            if not isinstance(value.get("cwd"), str) or value.get("timestamp") is None:
                continue
            task = thread_id(path.stem)
            if task is None or value.get("sessionId") != task:
                raise ValueError("Claude discovery identity mismatch")
            cwd = string(value["cwd"], "cwd")
            started = timestamp(value["timestamp"])
            project, _ = resolve(connection, cwd, projects)
            eligible = next(
                (
                    p
                    for p in projects
                    if p.id == project
                    and p.observe_since is not None
                    and started.date() >= p.observe_since
                ),
                None,
            )
            connection.execute(
                "INSERT INTO project_sessions VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(thread) DO UPDATE SET project=excluded.project,cwd=excluded.cwd,path=excluded.path,updated=excluded.updated",
                (
                    task,
                    "claude",
                    None if eligible is None else eligible.id,
                    cwd,
                    str(path),
                    started.isoformat(),
                    info.st_mtime_ns // 1000000,
                    None,
                    None,
                    None,
                ),
            )
            matched = True
            break
    connection.execute(
        "INSERT INTO claude_discovery_files VALUES (?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET device=excluded.device,inode=excluded.inode,size=excluded.size,position=excluded.position,matched=excluded.matched",
        (
            str(path),
            info.st_dev,
            info.st_ino,
            info.st_size if matched or chunk.position >= info.st_size else -1,
            chunk.position,
            int(matched),
        ),
    )


def discover(
    connection: sqlite3.Connection,
    directory: Path,
    projects: tuple[Project, ...],
    deadline: float,
) -> bool:
    if state(connection, "claude_config") != repr(projects):
        connection.execute("DELETE FROM claude_discovery_files")
        connection.execute("DELETE FROM discovery_state WHERE key LIKE 'claude_dir:%'")
        set_state(connection, "claude_config", repr(projects))
    if not any(p.observe_since is not None for p in projects) or not directory.exists():
        return False
    # Incomplete first records can grow without changing their directory mtime.
    pending: object = connection.execute(
        "SELECT path FROM claude_discovery_files WHERE matched=0"
    ).fetchall()
    for value in sequence(pending, "pending Claude discovery"):
        if time.monotonic() >= deadline:
            return True
        path = Path(string(row(value, 1)[0], "discovery path"))
        if path.exists():
            inspect(connection, path, projects)
    for folder in sorted(directory.iterdir()):
        if time.monotonic() >= deadline:
            return True
        if folder.is_symlink() or not folder.is_dir():
            continue
        stamp = str(folder.stat().st_mtime_ns)
        key = "claude_dir:" + str(folder)
        if state(connection, key) == stamp:
            continue
        for path in sorted(folder.glob("*.jsonl")):
            if time.monotonic() >= deadline:
                return True
            if thread_id(path.stem) is not None:
                inspect(connection, path, projects)
        set_state(connection, key, stamp)
    return False
