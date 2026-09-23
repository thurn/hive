"""A real disposable Dolt server; never discovers an existing Beads database."""

import os
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hive.beads_connection import BeadsConnection


@contextmanager
def private_server() -> Iterator[tuple[BeadsConnection, subprocess.Popen[bytes]]]:
    with tempfile.TemporaryDirectory(prefix="hive-server-test-") as temporary:
        root = Path(temporary).resolve()
        data, project = root / "data", root / "project"
        data.mkdir()
        project.mkdir()
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("BEADS_", "BD_", "DOLT_", "GIT_"))
        }
        environment.update(BD_NON_INTERACTIVE="1", DO_NOT_TRACK="1")
        for args in (
            ["init", "-q"],
            ["config", "user.name", "Hive Test"],
            ["config", "user.email", "hive-test@example.invalid"],
        ):
            subprocess.run(
                ["git", *args],
                cwd=project,
                env=environment,
                check=True,
                capture_output=True,
                timeout=10,
            )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with (root / "server.log").open("w") as log:
            server = subprocess.Popen(
                [
                    "dolt",
                    "sql-server",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--data-dir",
                    str(data),
                    "--loglevel",
                    "warning",
                ],
                cwd=data,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 10
                while True:
                    if server.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Private server failed: "
                            + (root / "server.log").read_text()
                        )
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.05)
                result = subprocess.run(
                    [
                        "bd",
                        "--sandbox",
                        "--dolt-auto-commit",
                        "off",
                        "init",
                        "--prefix",
                        "hv",
                        "--skip-hooks",
                        "--skip-agents",
                        "--non-interactive",
                        "--server",
                        "--external",
                        "--server-host",
                        "127.0.0.1",
                        "--server-port",
                        str(port),
                    ],
                    cwd=project,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode:
                    raise RuntimeError(result.stdout + result.stderr)
                yield BeadsConnection.read(project), server
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
