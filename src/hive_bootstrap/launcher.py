"""Acquire the maintenance guard before selecting application source."""

import json
import os
import subprocess
import sys
import tarfile

from hive_bootstrap.settings import read_settings
from hive_bootstrap.source import lock, select


def main() -> int:
    descriptor: int | None = None
    try:
        if sys.version_info[:2] != (3, 12):
            raise ValueError("Hive requires Python 3.12")
        settings = read_settings()
        descriptor = lock(settings.state / "locks/maintenance.lock", shared=True)
        source = select(settings)
        environment = dict(os.environ)
        environment.update(
            HIVE_REPOSITORY_DIRECTORY=str(settings.repository),
            HIVE_SELECTED_COMMIT=source.commit,
            HIVE_SELECTED_DIRECTORY=str(source.directory),
            HIVE_BEADS_DIRECTORY=str(settings.beads),
            HIVE_STATE_DIRECTORY=str(settings.state),
            HIVE_MUTATION_GUARD_FD=str(descriptor),
        )
        os.set_inheritable(descriptor, True)
        os.execve(
            sys.executable,
            [
                sys.executable,
                "-I",
                str(source.directory / "scripts/entry.py"),
                *sys.argv[1:],
            ],
            environment,
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        tarfile.TarError,
        subprocess.SubprocessError,
    ) as error:
        if isinstance(error, subprocess.CalledProcessError):
            output: object = error.stderr
            detail = (
                output.decode(errors="replace")
                if isinstance(output, bytes)
                else str(error)
            )
        else:
            detail = str(error)
        print(
            json.dumps(
                {
                    "code": (
                        "Busy"
                        if isinstance(error, TimeoutError)
                        else "SourceUnavailable"
                    ),
                    "detail": detail.strip(),
                    "uncertain": False,
                }
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return 1
