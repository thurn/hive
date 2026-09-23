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
            if not isinstance(key, str) or key not in {"repository", "state", "beads"}:
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
    return Settings(repository, state, beads)
