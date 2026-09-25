"""Host-specific locators and explicit collection-cache transitions."""

import json
from dataclasses import dataclass
from pathlib import Path

from hive.identity import Host, ThreadId
from hive.jsonvalue import parse, record, string
from hive.thread_links import thread_id


@dataclass(frozen=True)
class CodexTranscript:
    path: Path

    @property
    def host(self) -> Host:
        return Host.CODEX


@dataclass(frozen=True)
class ClaudeSession:
    """A session location validated by its main file or a matching child.

    This does not assert that the main transcript passed identity validation.
    Every main/child file must still pass its own collector preflight.
    """

    directory: Path
    thread: ThreadId

    @property
    def host(self) -> Host:
        return Host.CLAUDE

    @property
    def main(self) -> Path:
        return self.directory / f"{self.thread}.jsonl"


Source = CodexTranscript | ClaudeSession


@dataclass(frozen=True)
class Candidate:
    locator: Source


@dataclass(frozen=True)
class ValidatedSource:
    """A complete locator promoted only after successful collector preflight."""

    locator: Source

    def encode(self) -> str:
        source = self.locator
        if isinstance(source, CodexTranscript):
            return json.dumps({"codex": str(source.path)})
        return json.dumps({"claude": str(source.directory), "thread": source.thread})

    @staticmethod
    def decode(value: str, task: ThreadId) -> "ValidatedSource":
        data = record(parse(value), "validated source")
        if set(data) == {"codex"}:
            return ValidatedSource(CodexTranscript(Path(string(data["codex"], "path"))))
        if set(data) == {"claude", "thread"} and thread_id(data["thread"]) == task:
            return ValidatedSource(
                ClaudeSession(Path(string(data["claude"], "directory")), task)
            )
        raise ValueError("Invalid validated source identity")


@dataclass(frozen=True)
class Collected:
    source: ValidatedSource
    error: str | None = None


@dataclass(frozen=True)
class Unchanged:
    """No new identity evidence; keep any previously validated provenance."""

    error: str | None


@dataclass(frozen=True)
class Rejected:
    """Discovery conflicts or disappears: revoke the whole cached locator."""

    error: str


Attempt = Collected | Unchanged | Rejected
