"""Validated identities and the inherited maintenance guard for one invocation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.identity import SourceCommit


@dataclass(frozen=True)
class LaunchContext:
    commit: SourceCommit
    source: Path
    state: Path
    beads: Path
    guard: int

    @classmethod
    def read(cls) -> LaunchContext:
        try:
            descriptor = int(os.environ["HIVE_MUTATION_GUARD_FD"])
            if descriptor < 0:
                raise ValueError("Invalid maintenance guard")
            os.fstat(descriptor)
            return cls(
                SourceCommit(os.environ["HIVE_SELECTED_COMMIT"]),
                Path(os.environ["HIVE_SELECTED_DIRECTORY"]),
                Path(os.environ["HIVE_STATE_DIRECTORY"]),
                Path(os.environ["HIVE_BEADS_DIRECTORY"]),
                descriptor,
            )
        except (KeyError, ValueError, OSError) as error:
            raise HiveError(
                ErrorCode.INVALID_INPUT,
                "Enter Hive through its source-selecting launcher",
            ) from error

    def check_mutations_allowed(self) -> None:
        if (self.state / "locks/maintenance.stop").exists():
            raise HiveError(ErrorCode.PAUSED, "State maintenance has stopped mutations")

    def release(self) -> None:
        """Read and provider-wait commands release before any long external call."""
        os.close(self.guard)
