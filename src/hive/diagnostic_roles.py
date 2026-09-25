"""Role inference reads the skill inventory from this call's immutable source."""

import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from hive.jsonvalue import string
from hive.usage_store import row


@lru_cache(maxsize=1)
def inventory() -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    source = Path(__file__).resolve().parents[2]
    names = tuple(sorted(p.parent.name for p in (source / "skills").glob("*/SKILL.md")))
    entry = (source / "skills/shared/entry.md").read_text()
    emblems = tuple(
        (match[1], match[2])
        for match in re.finditer(r"([^\w\s,()]+) ([a-z]+)", entry)
        if match[2] in names
    )
    return names, emblems


def detect(text: str, *, description: bool = False) -> str | None:
    names, _ = inventory()
    for name in names:
        if re.search(r"(?:\$|/)" + re.escape(name) + r"\b", text) or (
            description and re.search(r"\b" + re.escape(name) + r"\b", text, re.I)
        ):
            return name
    return None


def title_role(title: str) -> str | None:
    return next(
        (role for emblem, role in inventory()[1] if title.startswith(emblem)), None
    )


def at(
    connection: sqlite3.Connection,
    thread: str,
    agent: str,
    when: str,
    seen: frozenset[str] = frozenset(),
) -> tuple[str, bool]:
    if agent in seen:
        return "ad_hoc", True
    value: object = connection.execute(
        "SELECT role,inherited FROM role_spans WHERE thread=? AND agent=? AND start<=? AND evidence='explicit' ORDER BY start DESC,role LIMIT 1",
        (thread, agent, when),
    ).fetchone()
    if value is not None:
        role, inherited = row(value, 2)
        return string(role, "role"), bool(inherited)
    if agent:
        spawn: object = connection.execute(
            "SELECT spawned FROM diagnostic_sessions WHERE thread=? AND agent=?",
            (thread, agent),
        ).fetchone()
        spawn_time = when if spawn is None else string(row(spawn, 1)[0], "spawn time")
        fallback_role = fallback(connection, thread, agent)
        if fallback_role is not None:
            return fallback_role, False
        role, _ = at(
            connection,
            thread,
            parent(connection, thread, agent),
            spawn_time,
            seen | {agent},
        )
        return role, True
    return fallback(connection, thread, agent) or "ad_hoc", False


def observe(
    connection: sqlite3.Connection, thread: str, agent: str, when: str, role: str | None
) -> bool:
    if role is None or role not in inventory()[0]:
        return False
    previous: object = connection.execute(
        "SELECT role FROM role_spans WHERE thread=? AND agent=? AND start<=? AND evidence='explicit' ORDER BY start DESC,role LIMIT 1",
        (thread, agent, when),
    ).fetchone()
    if previous is not None and row(previous, 1)[0] == role:
        return False
    connection.execute(
        "INSERT INTO role_spans VALUES (?,?,?,?,0,'explicit') ON CONFLICT(thread,agent,start,role) DO UPDATE SET evidence='explicit',inherited=0",
        (thread, agent, role, when),
    )
    return True


def fallback(connection: sqlite3.Connection, thread: str, agent: str) -> str | None:
    title: object = connection.execute(
        "SELECT role FROM diagnostic_titles WHERE thread=? AND agent=?", (thread, agent)
    ).fetchone()
    if title is not None and isinstance(row(title, 1)[0], str):
        return string(row(title, 1)[0], "title role")
    if agent:
        values: object = connection.execute(
            "SELECT agent_type,description FROM claude_agents WHERE task=? AND agent=?",
            (thread, agent),
        ).fetchone()
        if values is not None:
            for value in row(values, 2):
                if isinstance(value, str):
                    found = detect(value, description=True)
                    if found:
                        return found
    native: object = connection.execute(
        "SELECT title,agent_role FROM project_sessions WHERE thread=?",
        (agent or thread,),
    ).fetchone()
    if native is None:
        return None
    title, role = row(native, 2)
    if isinstance(role, str) and role in inventory()[0]:
        return role
    return title_role(title) if isinstance(title, str) else None


def finalize(connection: sqlite3.Connection, thread: str) -> None:
    from hive.jsonvalue import sequence

    values: object = connection.execute(
        "SELECT agent,spawned FROM diagnostic_sessions WHERE thread=? ORDER BY agent",
        (thread,),
    ).fetchall()
    for value in sequence(values, "session role evidence"):
        agent, start = row(value, 2)
        agent, start = string(agent, "agent", empty=True), string(start, "spawn")
        explicit = connection.execute(
            "SELECT 1 FROM role_spans WHERE thread=? AND agent=? AND evidence='explicit'",
            (thread, agent),
        ).fetchone()
        if explicit is not None:
            continue
        role = fallback(connection, thread, agent)
        inherited = role is None and bool(agent)
        if inherited:
            role, _ = at(connection, thread, parent(connection, thread, agent), start)
        if role is None:
            continue
        connection.execute(
            "DELETE FROM role_spans WHERE thread=? AND agent=? AND evidence<>'explicit' AND role<>?",
            (thread, agent, role),
        )
        connection.execute(
            "INSERT OR IGNORE INTO role_spans VALUES (?,?,?,?,?,?)",
            (
                thread,
                agent,
                role,
                start,
                int(inherited),
                "inherited" if inherited else "fallback",
            ),
        )


def parent(connection: sqlite3.Connection, thread: str, agent: str) -> str:
    value: object = connection.execute(
        "SELECT parent FROM project_sessions WHERE thread=?", (agent,)
    ).fetchone()
    if value is None:
        value = connection.execute(
            "SELECT parent_agent FROM claude_agent_parents WHERE task=? AND agent=?",
            (thread, agent),
        ).fetchone()
    candidate = None if value is None else row(value, 1)[0]
    return (
        candidate
        if isinstance(candidate, str) and candidate not in {thread, agent, "unknown"}
        else ""
    )
