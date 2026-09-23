"""Use provider outcomes without reproducing its CI proof or scheduling machinery."""

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.identity import CandidateId, SourceCommit, WorktreePath
from hive.jsonvalue import parse, record, sequence, string
from hive.local_sync import candidate_configuration, require_local_sync
from hive.tollgate_model import (
    Approval,
    Candidate,
    CandidateState,
    RemoteState,
    Workspace,
    decode_candidate,
    decode_workspace,
    source_oid,
)
from hive.tollgate_process import TollgateProcess


@dataclass(frozen=True)
class Tollgate:
    process: TollgateProcess

    def create_workspace(self, branch: str) -> Workspace:
        value = self.process.run(["worktree", "create", branch], mutation=True)
        try:
            return decode_workspace(value, branch)
        except HiveError as error:
            raise HiveError(error.code, error.detail, uncertain=True) from error

    def submit(self, workspace: WorktreePath, source: SourceCommit) -> CandidateId:
        self.require_workspace(workspace)
        value = self.process.run(["candidate", source], cwd=workspace, mutation=True)
        try:
            result = record(value, "candidate submission")
            if source_oid(result.get("source_oid")) != source:
                raise HiveError(
                    ErrorCode.INVALID_RECORD,
                    "Submitted source does not match reviewed source",
                )
            return CandidateId(string(result.get("item_id"), "candidate ID"))
        except HiveError as error:
            raise HiveError(error.code, error.detail, uncertain=True) from error

    def require_workspace(self, workspace: WorktreePath) -> None:
        def git_path(directory: Path, option: str) -> Path:
            try:
                value = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(directory),
                        "rev-parse",
                        "--path-format=absolute",
                        option,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                    env=self.process.environment(),
                )
                return Path(value.stdout.strip()).resolve()
            except (OSError, subprocess.SubprocessError) as error:
                raise HiveError(
                    ErrorCode.INVALID_INPUT,
                    f"Cannot identify workspace repository: {error}",
                ) from error

        root = git_path(workspace, "--show-toplevel")
        main_root = git_path(self.process.repository, "--show-toplevel")
        if root == main_root or root != workspace.resolve():
            raise HiveError(
                ErrorCode.INVALID_INPUT,
                "Implementation requires the root of an isolated Tollgate workspace",
            )
        if git_path(root, "--git-common-dir") != git_path(
            main_root, "--git-common-dir"
        ):
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Workspace belongs to a different project"
            )

    def inspect(self, candidate: CandidateId) -> Candidate:
        raw = self.process.run(["status", candidate])
        retained = decode_candidate(raw, candidate)
        candidate_configuration(self.process, raw)
        return retained

    def inspect_settled(
        self, candidate: CandidateId, source: SourceCommit
    ) -> Candidate:
        """Terminal item state alone is insufficient while an attempt drains."""
        raw = record(self.process.run(["status", candidate]), "candidate status")
        retained = decode_candidate(raw, candidate)
        applied = candidate_configuration(self.process, raw)
        if retained.source != source:
            raise HiveError(ErrorCode.INVALID_RECORD, "Candidate source changed")
        failures = {
            CandidateState.FAILED,
            CandidateState.MERGE_CONFLICT,
            CandidateState.DEPENDENCY_FAILED,
            CandidateState.CANCELED,
            CandidateState.SUPERSEDED,
            CandidateState.INFRASTRUCTURE_EXHAUSTED,
        }
        if retained.state not in failures | {
            CandidateState.PROMOTED,
            CandidateState.EXTERNALLY_INTEGRATED,
        }:
            raise HiveError(
                ErrorCode.RECOVERY_REQUIRED,
                f"{candidate}: {retained.state}; retain ownership and inspect or wait",
            )
        attempts = sequence(raw.get("attempts"), "candidate attempts")
        if "buildset" not in raw:
            raise HiveError(ErrorCode.INVALID_RECORD, "Missing current buildset")
        if raw["buildset"] is not None:
            attempts = [*attempts, raw["buildset"]]
        for value in attempts:
            state = string(
                record(value, "candidate attempt").get("state"), "attempt state"
            )
            if state in {"pending", "preparing", "running"}:
                raise HiveError(
                    ErrorCode.RECOVERY_REQUIRED,
                    f"{candidate}: a provider attempt is still {state}; retain ownership",
                )
            if state not in {
                "passed",
                "passed-with-warnings",
                "failed",
                "interrupted",
                "canceled",
                "invalidated",
                "infrastructure-exhausted",
            }:
                raise HiveError(
                    ErrorCode.INVALID_RECORD, "Unknown provider attempt state"
                )
        if retained.remote in {RemoteState.PUSHING, RemoteState.PUSH_BLOCKED}:
            raise HiveError(
                ErrorCode.SYNCHRONIZATION_REQUIRED,
                f"{candidate}: remote state is {retained.remote}; retain ownership",
            )
        if retained.state == CandidateState.PROMOTED:
            if retained.remote not in {RemoteState.DISABLED, RemoteState.SYNCHRONIZED}:
                raise HiveError(
                    ErrorCode.SYNCHRONIZATION_REQUIRED,
                    f"{candidate}: remote state is {retained.remote}",
                )
            require_local_sync(self.process, retained, raw, applied)
        return retained

    def approve(self, candidate: CandidateId, source: SourceCommit) -> Approval:
        before = self.inspect(candidate)
        if before.source != source:
            raise HiveError(
                ErrorCode.INVALID_INPUT,
                "Candidate differs from retained reviewed source",
            )
        if before.authorized:
            return Approval(candidate, source, True)
        value = self.process.run(["approve", candidate], mutation=True)
        try:
            result = record(value, "approval result")
            already = result.get("already_authorized")
            authorized = sequence(
                result.get("authorized_item_ids"), "authorized candidates"
            )
            if (
                result.get("item_id") != candidate
                or source_oid(result.get("source_oid")) != source
                or not isinstance(already, bool)
                or (candidate not in authorized and not already)
            ):
                raise HiveError(
                    ErrorCode.INVALID_RECORD,
                    "Approval did not acknowledge this candidate",
                )
            return Approval(candidate, source, already)
        except HiveError as error:
            raise HiveError(error.code, error.detail, uncertain=True) from error

    def wait(self, candidate: CandidateId, *, timeout: float = 3600) -> Candidate:
        if not math.isfinite(timeout) or timeout <= 0:
            raise HiveError(
                ErrorCode.INVALID_INPUT, "Wait timeout must be positive and finite"
            )
        try:
            result = self.process.invoke(["wait", candidate], timeout=timeout)
        except HiveError as error:
            raise HiveError(
                error.code, f"{candidate}: {error.detail}", uncertain=True
            ) from error
        last: object = None
        try:
            for line in result.stdout.splitlines():
                if line.strip():
                    last = parse(line)
                    decode_candidate(last, candidate)
        except HiveError as error:
            raise HiveError(
                ErrorCode.INVALID_RECORD, f"{candidate}: {error.detail}", uncertain=True
            ) from error
        if last is None:
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                result.stderr.strip() or "Tollgate wait returned no candidate state",
                uncertain=True,
            )
        retained = decode_candidate(last, candidate)
        if retained.state == CandidateState.PROMOTED:
            if retained.remote not in {RemoteState.DISABLED, RemoteState.SYNCHRONIZED}:
                raise HiveError(
                    ErrorCode.SYNCHRONIZATION_REQUIRED,
                    f"{candidate}: remote state is {retained.remote}",
                )
            if result.returncode:
                raise HiveError(
                    ErrorCode.UNRESOLVED_OUTCOME,
                    f"{candidate}: provider exited after reporting promotion; inspect status",
                    uncertain=True,
                )
            raw = self.process.run(["status", candidate])
            applied = candidate_configuration(self.process, raw)
            require_local_sync(self.process, retained, raw, applied)
            return retained
        failures = {
            CandidateState.FAILED: ErrorCode.VALIDATION_FAILED,
            CandidateState.CHECK_FAILED: ErrorCode.VALIDATION_FAILED,
            CandidateState.MERGE_CONFLICT: ErrorCode.MERGE_CONFLICT,
            CandidateState.CANCELED: ErrorCode.CANCELLED,
            CandidateState.SUPERSEDED: ErrorCode.CANCELLED,
            CandidateState.DEPENDENCY_FAILED: ErrorCode.VALIDATION_FAILED,
            CandidateState.INFRASTRUCTURE_EXHAUSTED: ErrorCode.PROVIDER_UNAVAILABLE,
        }
        code = failures.get(retained.state, ErrorCode.UNRESOLVED_OUTCOME)
        raise HiveError(
            code,
            f"{candidate}: {retained.reason or retained.state}",
            uncertain=code == ErrorCode.UNRESOLVED_OUTCOME,
        )
