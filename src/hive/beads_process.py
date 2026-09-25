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
        return self._run(("list", "--all", "--limit", "0"))

    def assigned(self, actor: str) -> object:
        return self._run(("list", "--all", "--limit", "0", "--assignee", actor), actor)

    def ready(self, project: str, actor: str) -> object:
        return self._run(
            ("ready", "--limit", "0", "--metadata-field", f"hive_project={project}"),
            actor,
        )

    def event_page(self, after: str) -> object:
        from hive.bead_queries import page

        return self._run(("sql", page(after)))

    def bead_events(self, identifier: str) -> object:
        from hive.bead_queries import bead

        return self._run(("sql", bead(identifier)))

    def _run(self, arguments: tuple[str, ...], actor: str | None = None) -> object:
        command = [
            "bd",
            "--sandbox",
            "--dolt-auto-commit",
            "off",
            "--json",
            *(("--actor", actor) if actor is not None else ()),
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
