"""Native identities stay distinct without inventing tokens or fingerprints."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NewType

BeadId = NewType("BeadId", str)
ProjectId = NewType("ProjectId", str)
CodexProjectId = NewType("CodexProjectId", str)
ThreadId = NewType("ThreadId", str)
TurnId = NewType("TurnId", str)
AgentId = NewType("AgentId", str)
# Compatibility aliases while observation callers migrate.
CodexTaskId = ThreadId
CodexTurnId = TurnId
ResponseId = NewType("ResponseId", str)
CandidateId = NewType("CandidateId", str)
SourceCommit = NewType("SourceCommit", str)
WorktreePath = NewType("WorktreePath", Path)

ModelId = NewType("ModelId", str)
UsdPicos = NewType("UsdPicos", int)


class Host(StrEnum):
    CODEX = "codex"
    CLAUDE = "claude"


class PricingTier(StrEnum):
    STANDARD = "standard"
    FAST = "fast"
    BATCH = "batch"
    FLEX = "flex"


@dataclass(frozen=True)
class Owner:
    thread: ThreadId
    turn: TurnId | None
    host: Host = Host.CODEX
    agent: AgentId | None = None

    @property
    def task(self) -> ThreadId:
        return self.thread
