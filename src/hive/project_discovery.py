"""Bounded discovery preserves cached membership on native-source failure."""

import sqlite3
import time
from pathlib import Path

from hive import claude_discovery, codex_discovery
from hive.codex_folding import normalize
from hive.errors import HiveError
from hive.identity import ThreadId
from hive.launch_context import LaunchContext
from hive.usage_store import UsageStore


def discover(
    store: UsageStore,
    context: LaunchContext,
    index: Path,
    linked: tuple[ThreadId, ...],
    deadline: float,
) -> dict[str, object]:
    if time.monotonic() >= deadline:
        return {"discovery_behind": True, "discovery_error": None}
    with store.connect() as connection:
        enabled = tuple(p.id for p in context.projects if p.observe_since is not None)
        if enabled:
            connection.execute(
                "UPDATE project_sessions SET project=NULL WHERE project NOT IN ("
                + ",".join("?" for _ in enabled)
                + ")",
                enabled,
            )
        else:
            connection.execute("UPDATE project_sessions SET project=NULL")
    errors: list[str] = []
    behind = False
    for name in ("codex", "claude"):
        try:
            with store.connect() as connection:
                if name == "codex":
                    behind |= codex_discovery.discover(
                        connection, index, context.projects, linked, deadline
                    )
                    normalize(connection)
                else:
                    behind |= claude_discovery.discover(
                        connection, context.claude_projects, context.projects, deadline
                    )
        except (HiveError, OSError, ValueError, sqlite3.Error) as error:
            errors.append(f"{name}: {error}")
    with store.connect() as connection:
        codex_discovery.set_state(connection, "behind", str(int(behind)))
        codex_discovery.set_state(connection, "error", "; ".join(errors))
    return {"discovery_behind": behind, "discovery_error": "; ".join(errors) or None}
