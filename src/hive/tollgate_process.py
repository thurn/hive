"""Bounded native commands and one foreground blocking wait, without polling."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse


@dataclass(frozen=True)
class Output:
    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True)
class TollgateProcess:
    repository: Path
    executable: str = "tg"

    def environment(self) -> dict[str, str]:
        return {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }

    def invoke(
        self,
        arguments: list[str],
        *,
        cwd: Path | None = None,
        timeout: float = 30,
        mutation: bool = False,
    ) -> Output:
        try:
            # --no-launch never starts or restarts the provider as a side effect.
            result = subprocess.run(
                [self.executable, "--no-launch", "--json", *arguments],
                cwd=self.repository if cwd is None else cwd,
                env=self.environment(),
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            # subprocess.run kills only this client. It does not cancel a
            # candidate or stop the Tollgate service/build process.
            raise HiveError(
                ErrorCode.DELIVERY_TIMEOUT,
                "Tollgate client timed out; inspect the retained candidate before retrying",
                uncertain=True,
            ) from error
        except OSError as error:
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE, f"Cannot start Tollgate: {error}"
            ) from error
        try:
            return Output(
                result.stdout.decode(), result.stderr.decode(), result.returncode
            )
        except UnicodeDecodeError as error:
            raise HiveError(
                ErrorCode.INVALID_RECORD,
                "Tollgate output is not UTF-8",
                uncertain=mutation,
            ) from error

    def run(
        self, arguments: list[str], *, cwd: Path | None = None, mutation: bool = False
    ) -> object:
        result = self.invoke(arguments, cwd=cwd, mutation=mutation)
        if result.returncode:
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                result.stderr.strip() or result.stdout.strip() or "Tollgate failed",
                uncertain=mutation,
            )
        try:
            return parse(result.stdout)
        except HiveError as error:
            raise HiveError(
                ErrorCode.INVALID_RECORD, error.detail, uncertain=mutation
            ) from error
