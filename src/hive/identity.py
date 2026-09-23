"""Native identities stay distinct without inventing tokens or fingerprints."""

from pathlib import Path
from typing import NewType

BeadId = NewType("BeadId", str)
ProjectId = NewType("ProjectId", str)
CodexTaskId = NewType("CodexTaskId", str)
CodexTurnId = NewType("CodexTurnId", str)
CandidateId = NewType("CandidateId", str)
SourceCommit = NewType("SourceCommit", str)
WorktreePath = NewType("WorktreePath", Path)
