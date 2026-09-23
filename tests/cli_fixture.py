"""Run the actual source-selecting command against isolated committed source."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from test_source_selection import commit, git

from hive.beads_connection import BeadsConnection
from hive.jsonvalue import parse, record

ROOT: Path = Path(__file__).resolve().parents[1]
SCOPE: tuple[str, ...] = ("--project", "search")
WORKER: tuple[str, ...] = ("--owner", "task-1", "--turn", "turn-1")


@dataclass(frozen=True)
class Cli:
    state: Path
    environment: dict[str, str]

    def run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-S", str(ROOT / "scripts/hive.py"), *arguments],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def call(self, *arguments: str, expected: str | None = None) -> dict[str, object]:
        result = self.run(*arguments, "--json")
        if result.returncode and expected is None:
            raise AssertionError(result.stderr)
        value = record(
            parse(result.stdout if result.returncode == 0 else result.stderr)
        )
        if expected is not None and value.get("code") != expected:
            raise AssertionError(f"Expected {expected}: {result.stdout}{result.stderr}")
        return value

    def add(self, title: str, *arguments: str) -> str:
        result = self.call(
            "task",
            "add",
            *SCOPE,
            "--title",
            title,
            "--description",
            "Produce a useful result",
            "--acceptance",
            "Evidence answers the question",
            "--kind",
            "artifact",
            *arguments,
        )
        identifier = record(result["task"])["id"]
        if not isinstance(identifier, str):
            raise AssertionError("Missing bead identity")
        return identifier


@contextmanager
def cli_fixture(connection: BeadsConnection) -> Iterator[Cli]:
    with tempfile.TemporaryDirectory(prefix="hive-cli-test-") as temporary:
        root = Path(temporary).resolve()
        repository = root / "source"
        repository.mkdir()
        shutil.copytree(
            ROOT / "src",
            repository / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
        )
        (repository / "scripts").mkdir()
        for script in ("entry.py", "hive.py"):
            shutil.copyfile(ROOT / "scripts" / script, repository / "scripts" / script)
        shutil.copyfile(ROOT / "pyproject.toml", repository / "pyproject.toml")
        git(repository, "init", "-b", "master")
        git(repository, "config", "user.name", "Hive test")
        git(repository, "config", "user.email", "test@localhost")
        commit(repository, "feat: source under test")
        config = root / "bootstrap.json"
        state = root / "state"
        config.write_text(
            json.dumps(
                {
                    "repository": str(repository),
                    "state": str(state),
                    "beads": str(connection.directory),
                }
            )
        )
        cli = Cli(state, {**os.environ, "HIVE_BOOTSTRAP_CONFIG": str(config)})
        cli.call("config", "initialize", expected="Configured")
        cli.call(
            "config",
            "register",
            *SCOPE,
            "--repository",
            str(root / "project"),
            "--invariants",
            str(root / "project/invariants.md"),
            "--native-id",
            "native-search",
            expected="Configured",
        )
        yield cli
