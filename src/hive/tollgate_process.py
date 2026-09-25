"""Read native Tollgate JSON without launching the application or its broker."""

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import parse, record


@dataclass(frozen=True)
class TollgateProcess:
    directory: Path
    timeout: float

    def read(self, arguments: tuple[str, ...], repository: str | None = None) -> object:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                result = subprocess.run(
                    [
                        "tg",
                        "--json",
                        "--no-launch",
                        *(
                            ("--repository", repository)
                            if repository is not None
                            else ()
                        ),
                        *arguments,
                    ],
                    cwd=self.directory,
                    stdout=output,
                    stderr=errors,
                    timeout=max(0.001, self.timeout),
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise HiveError(
                    ErrorCode.PROVIDER_UNAVAILABLE, "tollgate_unavailable"
                ) from error
            output.seek(0)
            data = output.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Tollgate response exceeds bound"
                )
            try:
                if result.returncode:
                    try:
                        failure = record(parse(data.decode())).get("error")
                        if (
                            isinstance(failure, dict)
                            and record(failure).get("code") == "not-found"
                        ):
                            raise HiveError(
                                ErrorCode.INVALID_INPUT, "tollgate_candidate_not_found"
                            )
                    except HiveError as error:
                        if error.detail == "tollgate_candidate_not_found":
                            raise
                    raise HiveError(
                        ErrorCode.PROVIDER_UNAVAILABLE, "tollgate_unavailable"
                    )
                return parse(data.decode())
            except UnicodeError as error:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Invalid Tollgate JSON"
                ) from error
