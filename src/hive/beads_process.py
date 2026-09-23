"""Narrow, read-only native Beads access for observation."""

import subprocess
from dataclasses import dataclass

from hive.beads_connection import BeadsConnection
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse


@dataclass(frozen=True)
class BeadsProcess:
    connection: BeadsConnection
    timeout: float = 10

    def list_all(self) -> object:
        command = [
            "bd",
            "-C",
            str(self.connection.directory),
            "--sandbox",
            "--dolt-auto-commit",
            "off",
            "--json",
            "list",
            "--all",
            "--limit",
            "0",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=self.connection.directory,
                env=self.connection.environment(),
                capture_output=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE, f"Beads unavailable: {error}"
            ) from error
        if result.returncode:
            detail = (
                (result.stderr or result.stdout)[-2000:]
                .decode(errors="replace")
                .strip()
            )
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"bd exited {result.returncode}: {detail}",
            )
        try:
            return parse(result.stdout.decode("utf-8"))
        except (HiveError, UnicodeDecodeError) as error:
            raise HiveError(ErrorCode.INVALID_RECORD, "Invalid Beads JSON") from error
