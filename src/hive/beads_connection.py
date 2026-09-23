"""Explicit local server routing, independent of a worker's code checkout."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import integer, parse, record, string


@dataclass(frozen=True)
class BeadsConnection:
    directory: Path
    host: str
    port: int
    database: str
    user: str

    @classmethod
    def read(cls, directory: Path) -> BeadsConnection:
        directory = directory.expanduser().resolve()
        try:
            data = record(parse((directory / ".beads/metadata.json").read_text()))
        except OSError as error:
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"Cannot read Beads configuration: {error}",
            ) from error
        if data.get("backend") != "dolt" or data.get("dolt_mode") != "server":
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Hive requires explicit server-mode Beads"
            )
        host = string(data.get("dolt_server_host"), "server host")
        port = integer(data.get("dolt_server_port"), "server port", minimum=1)
        if host not in {"127.0.0.1", "localhost", "::1"} or port > 65535:
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Hive requires a valid local server endpoint"
            )
        if data.get("dolt_server_socket") or data.get("dolt_server_tls"):
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Hive uses explicit local TCP server routing"
            )
        return cls(
            directory,
            host,
            port,
            string(data.get("dolt_database"), "database"),
            string(data.get("dolt_server_user", "root"), "server user"),
        )

    def environment(self) -> dict[str, str]:
        """Discard ambient repository routing; retain only explicit auth input."""
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("BEADS_", "BD_", "DOLT_", "GIT_"))
        }
        password = os.environ.get("BEADS_DOLT_PASSWORD")
        if password is not None:
            environment["BEADS_DOLT_PASSWORD"] = password
        environment.update(
            {
                "BEADS_DIR": str(self.directory / ".beads"),
                "BEADS_DOLT_SERVER_MODE": "1",
                "BEADS_DOLT_AUTO_START": "0",
                "BEADS_DOLT_SERVER_HOST": self.host,
                "BEADS_DOLT_SERVER_PORT": str(self.port),
                "BEADS_DOLT_SERVER_DATABASE": self.database,
                "BEADS_DOLT_SERVER_USER": self.user,
                "BD_NON_INTERACTIVE": "1",
                "BD_NO_HOOKS": "true",
                "DO_NOT_TRACK": "1",
            }
        )
        return environment
