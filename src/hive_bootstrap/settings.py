"""Local routing needed before application code or task storage can be read."""

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    repository: Path
    state: Path
    beads: Path
    projects: tuple["Project", ...]


@dataclass(frozen=True)
class Project:
    id: str
    repository: Path
    invariants: Path
    native_id: str | None


def read_settings() -> Settings:
    configured = os.environ.get("HIVE_BOOTSTRAP_CONFIG")
    path = (
        Path(configured).expanduser()
        if configured is not None
        else Path.home() / ".config/hive/bootstrap.json"
    )
    values: dict[str, object] = {}
    if configured is not None or path.exists():
        data: object = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("Bootstrap settings must be a JSON object")
        for key, value in data.items():
            if not isinstance(key, str) or key not in {
                "repository",
                "state",
                "beads",
                "projects",
            }:
                raise ValueError("Unknown bootstrap setting")
            values[key] = value

    def location(key: str, default: Path) -> Path:
        value = values.get(key, str(default))
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be an absolute path")
        result = Path(value).expanduser()
        if not result.is_absolute():
            raise ValueError(f"{key} must be an absolute path")
        return result.resolve()

    repository = location("repository", Path.home() / "hive")
    state = location("state", Path.home() / ".local/state/hive")
    beads = location("beads", Path.home() / "brain/hive")
    if state == beads or state.is_relative_to(beads):
        raise ValueError(
            "Local state and locks must be outside synchronized Beads data"
        )
    projects_value = values.get("projects", [])
    if not isinstance(projects_value, list):
        raise ValueError("projects must be an array")
    projects: list[Project] = []
    for item in projects_value:
        if not isinstance(item, dict) or set(item) - {
            "id",
            "repository",
            "invariants",
            "native_id",
        }:
            raise ValueError("Invalid project configuration")
        identifier = item.get("id")
        native_id = item.get("native_id")
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or (
                native_id is not None
                and (not isinstance(native_id, str) or not native_id.strip())
            )
        ):
            raise ValueError("Invalid project identity")
        locations = []
        for name in ("repository", "invariants"):
            value = item.get(name)
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError(f"Project {name} must be absolute")
            locations.append(Path(value).resolve())
        projects.append(Project(identifier, locations[0], locations[1], native_id))
    if len({project.id for project in projects}) != len(projects):
        raise ValueError("Duplicate project identity")
    return Settings(repository, state, beads, tuple(projects))
