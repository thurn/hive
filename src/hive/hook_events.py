"""Validate the released native Stop/Interrupt payload at the hook boundary."""

from dataclasses import dataclass

from hive.errors import ErrorCode, HiveError
from hive.identity import CodexTaskId, CodexTurnId
from hive.jsonvalue import record, string
from hive.model import Owner


@dataclass(frozen=True)
class Stop:
    owner: Owner
    already_continued: bool


@dataclass(frozen=True)
class Interrupt:
    owner: Owner


type HookEvent = Stop | Interrupt


def decode(value: object) -> HookEvent:
    data = record(value, "native hook event")
    owner = Owner(
        CodexTaskId(string(data.get("session_id"), "native task")),
        CodexTurnId(string(data.get("turn_id"), "native turn")),
    )
    event = data.get("hook_event_name")
    if event == "Interrupt":
        return Interrupt(owner)
    if event == "Stop":
        active = data.get("stop_hook_active")
        if not isinstance(active, bool):
            raise HiveError(ErrorCode.INVALID_INPUT, "Missing native stop-hook state")
        return Stop(owner, active)
    raise HiveError(ErrorCode.INVALID_INPUT, "Only Stop and Interrupt are supported")
