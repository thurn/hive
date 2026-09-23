"""Native configured-model observations, not proof of upstream response settings."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from hive.identity import CodexTaskId, CodexTurnId
from hive.jsonvalue import record, string
from hive.model import Owner
from hive.pricing import ModelId
from hive.usage import timestamp


@dataclass(frozen=True)
class TurnModel:
    owner: Owner
    model: ModelId
    observed: datetime


def decode(value: object, task: CodexTaskId) -> TurnModel | None:
    raw = record(value, "native record")
    if raw.get("type") != "turn_context":
        return None
    data = record(raw.get("payload"), "turn context")
    return TurnModel(
        Owner(task, CodexTurnId(string(data.get("turn_id"), "native turn"))),
        ModelId(string(data.get("model"), "configured model")),
        timestamp(raw.get("timestamp")),
    )


def save(connection: sqlite3.Connection, value: TurnModel) -> None:
    # A live turn can change models. Without response-start evidence, conflicting
    # context observations invalidate estimates for the whole turn. Replay cannot
    # silently pick the last model or clear this uncertainty.
    connection.execute(
        "INSERT INTO turn_models VALUES (?, ?, ?, ?, 0) "
        "ON CONFLICT(task,turn) DO UPDATE SET "
        "conflicted=turn_models.conflicted OR turn_models.model!=excluded.model",
        (value.owner.task, value.owner.turn, value.model, value.observed.isoformat()),
    )
