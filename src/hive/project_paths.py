"""Resolve repository membership using Git identity and bounded path fallback."""

import hashlib
import sqlite3
import subprocess
from pathlib import Path

from hive.project_config import Project


def common(path: Path) -> Path | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            timeout=0.1,
        )
        return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def resolve(
    connection: sqlite3.Connection, cwd: str, projects: tuple[Project, ...]
) -> tuple[str | None, bool]:
    fingerprint = hashlib.sha256(repr(projects).encode()).hexdigest()
    saved: object = connection.execute(
        "SELECT project,unresolved FROM cwd_projects WHERE cwd=? AND configuration=?",
        (cwd, fingerprint),
    ).fetchone()
    if isinstance(saved, tuple) and len(saved) == 2:
        project, unresolved = saved
        if project is None or isinstance(project, str):
            return project, bool(unresolved)
    path = Path(cwd)
    matched: str | None = None
    unresolved = not path.is_absolute()
    if not unresolved:
        path = path.resolve()
        identity = common(path)
        # Nested registrations choose the most specific repository consistently.
        for project in sorted(
            projects, key=lambda p: len(p.repository.parts), reverse=True
        ):
            repository = project.repository.resolve()
            if identity is not None and identity == common(repository):
                matched = project.id
                break
            if path.is_relative_to(repository):
                matched = project.id
                break
        unresolved = matched is None and not path.exists()
    connection.execute(
        "INSERT INTO cwd_projects VALUES (?,?,?,?) ON CONFLICT(cwd) DO UPDATE SET configuration=excluded.configuration, project=excluded.project, unresolved=excluded.unresolved",
        (cwd, fingerprint, matched, int(unresolved)),
    )
    return matched, unresolved
