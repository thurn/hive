"""One invocation's selected source and inherited maintenance guard."""

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
    repository: Path

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
                Path(os.environ["HIVE_REPOSITORY_DIRECTORY"]),
            )
        except (KeyError, ValueError, OSError) as error:
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Enter through the Hive launcher"
            ) from error

    def release(self) -> None:
        os.close(self.guard)
