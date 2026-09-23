"""Native-shaped replies around a real subprocess and isolated Git workspace."""

import json
import shlex
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from test_source_selection import commit, git

from hive.identity import CandidateId, SourceCommit, WorktreePath
from hive.jsonvalue import parse, record
from hive.tollgate import Tollgate
from hive.tollgate_process import TollgateProcess

CANDIDATE: CandidateId = CandidateId("candidate-1")


def oid(source: SourceCommit) -> dict[str, object]:
    return {"format": "sha1", "bytes": source}


def status(
    source: SourceCommit,
    state: str = "promoted",
    remote: str = "synchronized",
    *,
    authorized: bool = True,
) -> dict[str, object]:
    return {
        "item": {
            "id": CANDIDATE,
            "repository_id": "project-1",
            "current_generation_id": "generation-1",
            "source_oid": oid(source),
            "state": state,
            "remote_state": remote,
            "promotion_authorized": authorized,
            "terminal_reason": None,
        },
        "attempts": [],
        "buildset": None,
        "generation": {
            "id": "generation-1",
            "item_id": CANDIDATE,
            "tested_oid": oid(source),
            "configuration_digest": "native-policy-1",
        },
    }


def reply(value: object, exit_code: int = 0) -> dict[str, object]:
    return {"output": json.dumps(value), "exit": exit_code}


@dataclass(frozen=True)
class ProviderFixture:
    root: Path
    repository: Path
    workspace: WorktreePath
    source: SourceCommit
    provider: Tollgate

    def configure(self, **commands: object) -> None:
        commands.setdefault(
            "repository",
            reply(
                {
                    "state": {
                        "id": "project-1",
                        "path": str(self.repository),
                        "active_configuration_digest": "native-policy-1",
                    }
                }
            ),
        )
        (self.root / "fixture.json").write_text(json.dumps(commands))

    def calls(self) -> list[dict[str, object]]:
        path = self.root / "calls.jsonl"
        return (
            []
            if not path.exists()
            else [record(parse(s)) for s in path.read_text().splitlines()]
        )


@contextmanager
def provider_fixture() -> Iterator[ProviderFixture]:
    with tempfile.TemporaryDirectory(prefix="hive provider protocol ") as temporary:
        root = Path(temporary).resolve()
        repository = root / "project"
        repository.mkdir()
        git(repository, "init", "-b", "master")
        git(repository, "config", "user.name", "Hive test")
        git(repository, "config", "user.email", "test@localhost")
        (repository / "README.md").write_text("Isolated provider test\n")
        source = SourceCommit(commit(repository, "feat: fixture"))
        workspace = WorktreePath(root / "workspace")
        git(repository, "worktree", "add", "-b", "codex/fixture", str(workspace))
        (repository / ".tollgate").mkdir()
        (repository / ".tollgate/config.toml").write_text("sync_user_master = true\n")
        script = root / "tg"
        stub = root / "tollgate_stub.py"
        shutil.copyfile(Path(__file__).with_name("tollgate_stub.py"), stub)
        # A shebang cannot quote an interpreter path containing spaces (as in
        # Tollgate's Application Support cache). A shell exec preserves argv.
        script.write_text(
            "#!/bin/sh\nexec "
            + shlex.quote(sys.executable)
            + " "
            + shlex.quote(str(stub))
            + ' "$@"\n'
        )
        script.chmod(0o755)
        yield ProviderFixture(
            root,
            repository,
            workspace,
            source,
            Tollgate(TollgateProcess(repository, str(script))),
        )
