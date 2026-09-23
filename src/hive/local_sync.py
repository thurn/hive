"""Observe delivery's local branch result without requiring historical receipts."""

import subprocess
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import record, string
from hive.tollgate_model import (
    Candidate,
    CandidateState,
    RemoteState,
    decode_candidate,
    source_oid,
)
from hive.tollgate_process import TollgateProcess


def candidate_configuration(process: TollgateProcess, view: object) -> str:
    """Bind global candidate lookup to the selected native repository."""
    item = record(record(view, "candidate status").get("item"), "candidate")
    applied = record(record(process.run(["status"])).get("state"), "repository state")
    path = Path(string(applied.get("path"), "registered repository path"))
    if not path.is_absolute() or path.resolve() != process.repository.resolve():
        raise HiveError(
            ErrorCode.INVALID_INPUT,
            "Selected project must match Tollgate's registered repository root",
        )
    repository = string(item.get("repository_id"), "candidate repository ID")
    if repository != string(applied.get("id"), "selected repository ID"):
        raise HiveError(
            ErrorCode.INVALID_INPUT, "Candidate belongs to another Tollgate project"
        )
    return string(
        applied.get("active_configuration_digest"), "applied configuration identity"
    )


def require_local_sync(
    process: TollgateProcess,
    candidate: Candidate,
    view: object,
    applied_configuration: str,
) -> None:
    """A promoted native generation must reach master, or have applied opt-out.

    Native configuration identities are compared, never computed or persisted by
    Hive. Local config text alone cannot waive configured synchronization.
    """
    snapshot = record(view, "candidate status")
    current = decode_candidate(snapshot, candidate.id)
    if current.source != candidate.source or current.state != CandidateState.PROMOTED:
        raise HiveError(
            ErrorCode.UNRESOLVED_OUTCOME,
            "Candidate changed after its delivery wait",
            uncertain=True,
        )
    if current.remote not in {RemoteState.DISABLED, RemoteState.SYNCHRONIZED}:
        raise HiveError(
            ErrorCode.SYNCHRONIZATION_REQUIRED,
            f"{current.id}: remote state is {current.remote}",
        )
    item = record(snapshot.get("item"), "candidate")
    generation = record(snapshot.get("generation"), "promoted generation")
    generation_id = string(generation.get("id"), "generation ID")
    if (
        generation.get("item_id") != candidate.id
        or item.get("current_generation_id") != generation_id
    ):
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Promoted generation belongs to different work"
        )
    tested = source_oid(generation.get("tested_oid"))
    try:
        included = subprocess.run(
            ["git", "merge-base", "--is-ancestor", tested, "refs/heads/master"],
            cwd=process.repository,
            env={**process.environment(), "GIT_NO_REPLACE_OBJECTS": "1"},
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise HiveError(
            ErrorCode.PROVIDER_UNAVAILABLE, f"Cannot inspect local master: {error}"
        ) from error
    if included.returncode == 0:
        return

    policy = record(process.run(["config", "explain"]), "Tollgate configuration")
    enabled = policy.get("sync_user_master")
    if not isinstance(enabled, bool):
        raise HiveError(
            ErrorCode.INVALID_RECORD, "Missing local synchronization policy"
        )
    if enabled:
        raise HiveError(
            (
                ErrorCode.SYNCHRONIZATION_REQUIRED
                if included.returncode == 1
                else ErrorCode.UNRESOLVED_OUTCOME
            ),
            f"{candidate.id}: promoted commit {tested} has not been verified in local master; inspect Tollgate synchronization",
            uncertain=included.returncode != 1,
        )

    # Explain validates the file; repository status identifies the policy the
    # provider actually applied. Also require the candidate's native generation
    # to name that policy, rather than applying today's opt-out to older work.
    identity = string(policy.get("digest"), "native configuration identity")
    if (
        generation.get("configuration_digest") != identity
        or applied_configuration != identity
    ):
        raise HiveError(
            ErrorCode.UNRESOLVED_OUTCOME,
            f"{candidate.id}: disabled local synchronization is not the applied candidate policy",
            uncertain=True,
        )
