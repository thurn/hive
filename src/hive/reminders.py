"""Local reminder delivery and native interruption observations, not task state."""

import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from hive.identity import BeadId
from hive.model import Bead, Deferred, Done, Owner, PauseReason, Queued, State, owner_of


@dataclass(frozen=True)
class ReminderGuard:
    path: Path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=0.05)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS reminders ("
                "task TEXT NOT NULL, turn TEXT NOT NULL, completed TEXT, "
                "reminded TEXT, has_reminded INTEGER NOT NULL DEFAULT 0, "
                "interrupted INTEGER NOT NULL DEFAULT 0, interrupted_bead TEXT, "
                "PRIMARY KEY (task, turn), "
                "CHECK (has_reminded IN (0, 1)), CHECK (interrupted IN (0, 1)))"
            )
            with connection:
                yield connection
        finally:
            connection.close()

    def completed(self, owner: Owner, bead: BeadId) -> None:
        """Called under admission exclusion after acknowledged Beads completion."""
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO reminders (task, turn, completed) VALUES (?, ?, ?) "
                "ON CONFLICT (task, turn) DO UPDATE SET completed = excluded.completed",
                (owner.task, owner.turn, bead),
            )

    def interrupt(self, owner: Owner, bead: BeadId | None = None) -> None:
        """Retain the native stop observation even if Beads is unavailable."""
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO reminders (task, turn, interrupted, interrupted_bead) "
                "VALUES (?, ?, 1, ?) ON CONFLICT (task, turn) DO UPDATE SET "
                "interrupted = 1, interrupted_bead = "
                "COALESCE(excluded.interrupted_bead, reminders.interrupted_bead)",
                (owner.task, owner.turn, bead),
            )

    def resumed(self, bead: BeadId) -> None:
        """A successful explicit user resumption resolves its observed stop."""
        if not self.path.exists():
            return
        with self.connect() as connection:
            connection.execute(
                "UPDATE reminders SET interrupted = 0, interrupted_bead = NULL "
                "WHERE interrupted_bead = ?",
                (bead,),
            )

    def interrupted(self, owner: Owner) -> bool:
        if not self.path.exists():
            return False
        with self.connect() as connection:
            value: object = connection.execute(
                "SELECT interrupted FROM reminders WHERE task = ? AND turn = ?",
                (owner.task, owner.turn),
            ).fetchone()
        if value is None or value == (0,):
            return False
        if value == (1,):
            return True
        raise ValueError("Invalid native interruption observation")

    def reserve(self, owner: Owner, *, already_continued: bool) -> bool:
        """Commit before returning permission to emit, including the initial case.

        A continued native turn with no observed completion cannot start another
        reminder chain, even if Codex assigns its continuation a different turn.
        Errors propagate so the hook can report them and allow the stop.
        """
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO reminders (task, turn) VALUES (?, ?)",
                (owner.task, owner.turn),
            )
            changed = connection.execute(
                "UPDATE reminders SET reminded = completed, has_reminded = 1 "
                "WHERE task = ? AND turn = ? AND interrupted = 0 "
                "AND (has_reminded = 0 OR reminded IS NOT completed) "
                "AND (? = 0 OR completed IS NOT NULL)",
                (owner.task, owner.turn, int(already_continued)),
            ).rowcount
        return changed == 1

    def discard(self, owner: Owner) -> None:
        """Call only after native inspection proves this turn has ended."""
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM reminders WHERE task = ? AND turn = ?",
                (owner.task, owner.turn),
            )


def report_failure(detail: str) -> None:
    """Diagnostics cannot reverse an acknowledged mutation, even with a lost pipe."""
    try:
        print(detail, file=sys.stderr)
    except (OSError, ValueError):
        pass


def user_paused(state: State) -> bool:
    return isinstance(state, Deferred) and any(
        condition.reason == PauseReason.USER for condition in state.conditions
    )


def observe_transition(path: Path, before: Bead, after: Bead) -> None:
    """An advisory write cannot fail an already acknowledged task mutation."""
    try:
        guard = ReminderGuard(path)
        if (
            user_paused(before.state)
            and isinstance(after.state, (Queued, Deferred))
            and not user_paused(after.state)
        ):
            guard.resumed(after.id)
        owner = owner_of(before.state)
        if owner is not None:
            if isinstance(after.state, Done):
                guard.completed(owner, after.id)
            elif user_paused(after.state):
                guard.interrupt(owner, after.id)
    except (OSError, ValueError, sqlite3.Error) as error:
        report_failure(f"Hive hook observation was not saved: {error}")
