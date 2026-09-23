"""Validated identities and the inherited maintenance guard for one invocation."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
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
                ErrorCode.INVALID_INPUT,
                "Enter Hive through its source-selecting launcher",
            ) from error

    def check_mutations_allowed(self) -> None:
        if (self.state / "locks/maintenance.stop").exists():
            raise HiveError(ErrorCode.PAUSED, "State maintenance has stopped mutations")

    def release(self) -> None:
        """Read and provider-wait commands release before any long external call."""
        os.close(self.guard)

    @contextmanager
    def reenter_mutation(self) -> Iterator[None]:
        """Refuse stale code after an unlocked provider observation.

        Maintenance may have converted state while the provider was queried.
        Reacquire its guard before checking master or reading Beads again.
        A changed source requires a new invocation, not a mixed-code mutation.
        """
        from hive.locking import Guards

        with Guards(self.state / "locks").mutation():
            try:
                current = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(self.repository),
                        "rev-parse",
                        "--verify",
                        "refs/heads/master^{commit}",
                    ],
                    env={
                        k: v for k, v in os.environ.items() if not k.startswith("GIT_")
                    },
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError) as error:
                raise HiveError(
                    ErrorCode.RECOVERY_REQUIRED,
                    f"Cannot recheck selected source before mutation: {error}",
                ) from error
            if current != self.commit:
                raise HiveError(
                    ErrorCode.RECOVERY_REQUIRED,
                    "Hive source changed during provider inspection; repeat this command",
                )
            yield
