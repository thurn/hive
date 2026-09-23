"""Native identities stay distinct without inventing tokens or fingerprints."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NewType

BeadId = NewType("BeadId", str)
ProjectId = NewType("ProjectId", str)
CodexProjectId = NewType("CodexProjectId", str)
CodexTaskId = NewType("CodexTaskId", str)
CodexTurnId = NewType("CodexTurnId", str)
ResponseId = NewType("ResponseId", str)
CandidateId = NewType("CandidateId", str)
SourceCommit = NewType("SourceCommit", str)
WorktreePath = NewType("WorktreePath", Path)

ModelId = NewType("ModelId", str)
UsdPicos = NewType("UsdPicos", int)


class PricingTier(StrEnum):
    STANDARD = "standard"
    FAST = "fast"
    BATCH = "batch"
    FLEX = "flex"


@dataclass(frozen=True)
class Owner:
    task: CodexTaskId
    turn: CodexTurnId
