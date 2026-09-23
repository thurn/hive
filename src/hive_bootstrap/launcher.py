"""Acquire the maintenance guard before selecting application source."""

import json
import os
import runpy
import subprocess
import sys

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
        os.environ.update(
            HIVE_REPOSITORY_DIRECTORY=str(settings.repository),
            HIVE_SELECTED_COMMIT=source.commit,
            HIVE_SELECTED_DIRECTORY=str(source.directory),
            HIVE_BEADS_DIRECTORY=str(settings.beads),
            HIVE_STATE_DIRECTORY=str(settings.state),
            HIVE_MUTATION_GUARD_FD=str(descriptor),
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
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
        if descriptor is not None:
            os.close(descriptor)
        return 1

    # Only bootstrap and standard-library modules have loaded. Select one fixed
    # application path before importing any policy, reusing this interpreter.
    sys.path[0] = str(source.directory / "src")
    for name in tuple(sys.modules):
        if name == "hive_bootstrap" or name.startswith("hive_bootstrap."):
            del sys.modules[name]
    # The selected entrypoint now owns the guard and exits the process. Do not
    # close its old descriptor number here: the app may have released/reused it.
    runpy.run_path(str(source.directory / "scripts/entry.py"), run_name="__main__")
    raise RuntimeError("Selected application returned without an exit status")
