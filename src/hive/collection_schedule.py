"""Prioritize changed files, then new sessions, with bounded idle sampling."""

import sqlite3
import time
from pathlib import Path

from hive.identity import ThreadId
from hive.jsonvalue import integer, sequence, string
from hive.usage_store import row


def select(
    connection: sqlite3.Connection,
    limit: int,
    *,
    idle: bool = True,
    deadline: float | None = None,
) -> tuple[ThreadId, ...]:
    values: object = connection.execute(
        "SELECT t.task,t.attempted,s.path,s.mtime_ns,s.size,s.remaining,s.device,s.inode FROM collection_tasks t LEFT JOIN sources s ON t.task=s.task ORDER BY t.attempted,t.task"
    ).fetchall()
    changed: dict[str, int] = {}
    new: dict[str, None] = {}
    older: dict[str, None] = {}
    known: dict[str, set[str]] = {}
    for value in sequence(values, "scheduled paths"):
        task, _, path, _, _, _, _, _ = row(value, 8)
        if path is not None:
            known.setdefault(string(task, "task"), set()).add(string(path, "path"))
    for value in sequence(values, "scheduled sessions"):
        if deadline is not None and time.monotonic() >= deadline:
            break
        task, attempted, raw_path, mtime, size, remaining, device, inode = row(value, 8)
        task = string(task, "scheduled task")
        if attempted is None:
            new[task] = None
        else:
            older[task] = None
        if raw_path is None:
            continue
        path = Path(string(raw_path, "source path"))
        known.setdefault(task, set()).add(str(path))
        try:
            info = path.stat()
            if (info.st_mtime_ns, info.st_size, info.st_dev, info.st_ino) != (
                mtime,
                size,
                device,
                inode,
            ) or (remaining is not None and integer(remaining, "remaining") > 0):
                changed[task] = max(changed.get(task, 0), info.st_mtime_ns)
            if path.name == task + ".jsonl":
                for child in (path.parent / task / "subagents").glob("agent-*.jsonl"):
                    if str(child) not in known[task]:
                        changed[task] = max(
                            changed.get(task, 0), child.stat().st_mtime_ns
                        )
        except OSError:
            # Missing files need a retry, without displacing real new work.
            pass
    # Newly discovered Codex children also make an already-idle root runnable.
    child_rows: object = connection.execute(
        "SELECT r.root,p.path FROM codex_roots r JOIN project_sessions p ON r.thread=p.thread JOIN collection_tasks t ON t.task=r.root WHERE r.thread<>r.root"
    ).fetchall()
    for value in sequence(child_rows, "scheduled children"):
        task, path = row(value, 2)
        task, path = string(task, "root"), string(path, "child path")
        if path not in known.get(task, set()):
            changed[task] = max(changed.get(task, 0), 1)
    result = sorted(changed, key=lambda t: (-changed[t], t))
    result.extend(t for t in new if t not in changed)
    if idle:
        result.extend([t for t in older if t not in changed and t not in new][:4])
    return tuple(ThreadId(t) for t in result[:limit])
