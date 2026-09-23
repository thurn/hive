"""Prepare committed source once and retain it for the lifetime of each call."""

import fcntl
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from hive_bootstrap.settings import Settings


@dataclass(frozen=True)
class Source:
    commit: str
    directory: Path


def lock(path: Path, *, shared: bool = False) -> int:
    """Return an owned descriptor; callers close it or transfer it through exec."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + 2
    try:
        acquired = False
        while not acquired:
            try:
                mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Local source or maintenance lock is busy"
                    ) from None
                time.sleep(0.005)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def git(settings: Settings, *arguments: str) -> bytes:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    return subprocess.run(
        ["git", "-C", str(settings.repository), *arguments],
        check=True,
        capture_output=True,
        timeout=15,
        env=environment,
    ).stdout


def current_commit(settings: Settings) -> str:
    commit = git(settings, "rev-parse", "--verify", "refs/heads/master^{commit}")
    value = commit.decode().strip()
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is None:
        raise ValueError("Git returned an invalid master commit")
    return value


def prepare(settings: Settings, commit: str) -> Source:
    sources = settings.state / "sources"
    destination = sources / commit
    if destination.is_dir():
        return Source(commit, destination)
    descriptor = lock(settings.state / "source-preparation.lock")
    try:
        if destination.is_dir():
            return Source(commit, destination)
        sources.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="preparing-", dir=sources) as temporary:
            staged = Path(temporary) / "source"
            staged.mkdir()
            archive = git(settings, "archive", "--format=tar", commit)
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
                bundle.extractall(staged, filter="data")
            # Hive currently has no runtime dependencies. Adding any is explicit
            # environment maintenance, never an implicit install on ordinary calls.
            project: object = tomllib.loads(
                (staged / "pyproject.toml").read_text()
            ).get("project")
            if not isinstance(project, dict) or project.get("dependencies") != []:
                raise ValueError(
                    "Runtime dependency changes require environment maintenance"
                )
            if not (staged / "scripts/entry.py").is_file():
                raise ValueError("Selected source has no application entrypoint")
            try:
                subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        "import sys; sys.path.insert(0, sys.argv[1]); import hive.cli",
                        str(staged / "src"),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=15,
                )
            except subprocess.CalledProcessError as error:
                output: object = error.stderr
                detail = (
                    output.decode(errors="replace")
                    if isinstance(output, bytes)
                    else str(error)
                )
                raise RuntimeError(
                    f"Source {commit} failed preparation: {detail.strip()}"
                ) from error
            # No caller can observe a partially extracted or rejected snapshot.
            staged.rename(destination)
        return Source(commit, destination)
    finally:
        os.close(descriptor)


def select(settings: Settings) -> Source:
    # Recheck after preparation so a concurrent commit can supersede a slow
    # preparation. Do not fall back to previously selected source on failure.
    for _ in range(3):
        source = prepare(settings, current_commit(settings))
        if current_commit(settings) == source.commit:
            return source
    raise RuntimeError("Local master changed repeatedly during source preparation")
