"""Explicit, local executor opt-in; never an ownership or dispatch registry."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from hive.jsonvalue import parse, record, string
from hive.locking import file_lock
from hive.thread_links import thread_id


@dataclass(frozen=True)
class ExecutorState:
    project: str
    active: bool
    reason: str
    corrected_turn: str
    revision: str
    continuation: str = ""


@dataclass(frozen=True)
class ExecutorStore:
    directory: Path
    session: str

    def __post_init__(self) -> None:
        if thread_id(self.session) is None:
            raise ValueError("Executor requires a native Codex session UUID")

    @property
    def path(self) -> Path:
        return self.directory / "executors" / f"{self.session}.json"

    @property
    def lock_path(self) -> Path:
        return self.directory / "locks" / f"executor-{self.session}.lock"

    def read(self) -> ExecutorState | None:
        try:
            value = record(parse(self.path.read_text()))
        except FileNotFoundError:
            return None
        active = value.get("active")
        if not isinstance(active, bool):
            raise ValueError("Invalid executor activation")
        return ExecutorState(
            string(value.get("project"), "project"),
            active,
            string(value.get("reason"), "reason", empty=True),
            string(value.get("corrected_turn"), "corrected turn", empty=True),
            string(value.get("revision"), "revision"),
            string(value.get("continuation", ""), "continuation", empty=True),
        )

    def save(self, state: ExecutorState) -> None:
        """Caller holds the per-session lock; readers see one complete revision."""
        import json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid4()}.tmp")
        try:
            with open(temporary, "x", opener=_private) as output:
                output.write(json.dumps(asdict(state)))
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def start(self, project: str) -> None:
        with file_lock(self.lock_path, timeout=0.2):
            previous = self.read()
            if previous is not None and previous.project != project:
                raise ValueError("Executor project binding cannot change in a session")
            self.save(
                ExecutorState(
                    project,
                    True,
                    "",
                    previous.corrected_turn if previous else "",
                    str(uuid4()),
                    previous.continuation if previous else "",
                )
            )

    def stop(self, reason: str, *, new_input: str | None = None) -> None:
        with file_lock(self.lock_path, timeout=0.2):
            previous = self.read()
            if previous is not None:
                if (
                    previous.active
                    and previous.continuation
                    and new_input == previous.continuation
                ):
                    return
                self.save(
                    ExecutorState(
                        previous.project,
                        False,
                        reason,
                        "" if new_input is not None else previous.corrected_turn,
                        str(uuid4()),
                    )
                )


def _private(path: str, flags: int) -> int:
    return os.open(path, flags, 0o600)
