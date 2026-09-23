"""Supported bd subprocesses with bounded waits and honest uncertain writes."""

import subprocess
from dataclasses import dataclass

from hive.beads_connection import BeadsConnection
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse


@dataclass(frozen=True)
class BeadsProcess:
    connection: BeadsConnection
    actor: str
    executable: str = "bd"
    timeout: float = 10

    def run(self, arguments: list[str], *, mutation: bool = False) -> object:
        command = [
            self.executable,
            "-C",
            str(self.connection.directory),
            "--sandbox",
            "--dolt-auto-commit",
            "off",
            "--json",
            "--actor",
            self.actor,
            *arguments,
        ]
        try:
            result = subprocess.run(
                command,
                cwd=self.connection.directory,
                env=self.connection.environment(),
                capture_output=True,
                timeout=self.timeout,
            )
        except OSError as error:
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE, f"Cannot start bd: {error}"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Beads request timed out; inspect the task before retrying a write",
                uncertain=mutation,
            ) from error
        if result.returncode:
            detail = (
                (result.stderr or result.stdout)[-2000:]
                .decode("utf-8", errors="replace")
                .strip()
            )
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"bd exited {result.returncode}: {detail}",
                uncertain=mutation,
            )
        try:
            return parse(result.stdout.decode("utf-8"))
        except (HiveError, UnicodeDecodeError) as error:
            raise HiveError(
                ErrorCode.INVALID_RECORD,
                "bd returned invalid JSON; inspect the task before retrying a write",
                uncertain=mutation,
            ) from error
