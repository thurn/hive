"""Bead-stored creator and assignee references, including closed work."""

import re
from dataclasses import dataclass
from uuid import UUID

from hive.beads_process import BeadsProcess
from hive.errors import HiveError
from hive.identity import CodexTaskId
from hive.jsonvalue import record, sequence, string


@dataclass(frozen=True, order=True)
class ThreadLink:
    task: CodexTaskId
    bead: str
    relation: str
    collected: bool


def thread_id(value: object) -> CodexTaskId | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return CodexTaskId(value) if str(parsed) == value else None


def decode(value: object) -> tuple[tuple[ThreadLink, ...], tuple[str, ...]]:
    links: set[ThreadLink] = set()
    gaps: set[str] = set()
    for raw in sequence(value, "beads"):
        bead = record(raw, "bead")
        identifier = string(bead.get("id"), "bead ID")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", identifier):
            gaps.add(f"{identifier}: invalid bead ID")
            continue
        try:
            metadata = record(bead.get("metadata", {}), "bead metadata")
        except HiveError:
            gaps.add(f"{identifier}: invalid metadata")
            metadata = {}
        for relation, candidate in (
            ("creator", metadata.get("hive_origin_thread")),
            ("executor", bead.get("assignee")),
        ):
            if candidate in (None, ""):
                if relation == "creator":
                    gaps.add(f"{identifier}: missing creator thread")
                continue
            task = thread_id(candidate)
            if task is None:
                gaps.add(f"{identifier}: invalid {relation} thread")
            else:
                links.add(ThreadLink(task, identifier, relation, False))
    return tuple(sorted(links)), tuple(sorted(gaps))


def read(process: BeadsProcess) -> tuple[tuple[ThreadLink, ...], tuple[str, ...]]:
    return decode(process.list_all())
