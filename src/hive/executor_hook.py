"""Bounded stop feedback for explicitly opted-in Codex executors."""

import os
import shlex
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.errors import HiveError
from hive.executor_state import ExecutorStore, StopKind
from hive.jsonvalue import parse, record, sequence, string
from hive.launch_context import LaunchContext
from hive.locking import file_lock
from hive_bootstrap.settings import read_settings


def start(context: LaunchContext, project: str) -> dict[str, object]:
    settings = read_settings()
    if project not in {item.id for item in settings.projects}:
        raise ValueError("Executor project must be registered in bootstrap settings")
    store = ExecutorStore(context.state, os.environ.get("CODEX_THREAD_ID", ""))
    store.start(project)
    return {"code": "ExecutorStarted", "project": project, "session": store.session}


def stop(
    context: LaunchContext, reason: str, kind: StopKind, recovery: str | None = None
) -> dict[str, object]:
    """Require a recovery attestation for impediments, without judging its truth.

    Validation precedes disarming: a legacy free-form blocker stop cannot disable
    continuation. Native input/interrupt hooks still disarm immediately.
    """
    if not reason.strip():
        raise ValueError("Stopping requires a concrete reason")
    if kind in {StopKind.BLOCKED, StopKind.PRESSURE} and (
        recovery is None or not recovery.strip()
    ):
        raise ValueError(
            "Enter justiciar recovery in this task before stopping for a blocker "
            "or resource pressure; --recovery must record inspected evidence, "
            "attempted repair or authorized relaxation, and the remaining external "
            "dependency or capacity limit. Resume executor work when recovery succeeds."
        )
    evidence = f"{kind.value}: {reason}"
    if recovery is not None:
        evidence += f"\nJusticiar recovery: {recovery}"
    store = ExecutorStore(context.state, os.environ.get("CODEX_THREAD_ID", ""))
    store.stop(evidence)
    return {
        "code": "ExecutorStopped",
        "kind": kind.value,
        "reason": reason,
        "recovery": recovery,
    }


def handle(context: LaunchContext, payload: str) -> dict[str, object]:
    """Always emit valid hook JSON; unavailable evidence warns without looping."""
    try:
        return _handle(context, record(parse(payload), "hook input"))
    except (HiveError, OSError, ValueError) as error:
        return {"systemMessage": f"Hive executor stop check unavailable: {error}"}


def _handle(context: LaunchContext, value: dict[str, object]) -> dict[str, object]:
    event = string(value.get("hook_event_name"), "hook event")
    if event not in {"Stop", "Interrupt", "UserPromptSubmit"}:
        return {}
    # Native hook identity is authoritative, never the inherited shell identity.
    store = ExecutorStore(context.state, string(value.get("session_id"), "session"))
    if event != "Stop":
        store.stop(
            f"{event}: executor must explicitly opt in again",
            new_input=(
                string(value.get("prompt"), "prompt")
                if event == "UserPromptSubmit"
                else None
            ),
        )
        return {}
    state = store.read()
    if state is None or not state.active:
        return {}
    turn = string(value.get("turn_id"), "turn")
    active = value.get("stop_hook_active")
    if not isinstance(active, bool):
        raise ValueError("Stop requires stop_hook_active")
    process = BeadsProcess(BeadsConnection.read(context.beads), timeout=2)
    assigned = _ids(process.assigned(store.session), state.project, store.session)
    ready = _ids(process.ready(state.project, store.session), state.project, None)
    if not assigned and not ready:
        return {}
    stop_command = _command(
        context, "stop", "--kind", "KIND", "--reason", "CONCRETE EVIDENCE"
    )
    message = (
        f"Hive executor {state.project}: unfinished assignments: "
        f"{', '.join(assigned) or 'none'}; unclaimed ready candidates: "
        f"{', '.join(ready) or 'none'}. "
        "Reconcile remaining acceptance, writers and delivery before stopping; "
        "promotion alone does not complete acceptance. Inspect candidate scope, "
        "prerequisite outcomes, approvals, ownership and resource pressure before "
        "claiming. Continue eligible work under the executor skill. Before treating "
        "a blocker, retry cutoff, timing miss or resource pressure as a reason to "
        "end work, invoke the justiciar skill in this same task: inspect evidence, "
        "repair or relax agent-imposed constraints within existing authority, "
        "record changes and consequences, then resume executor work. Never waive "
        "explicit user acceptance or broaden authorization. A retry limit ends "
        "identical retries, not useful recovery. For a genuine remaining external "
        "dependency or capacity limit, checkpoint and settle writers/delivery, "
        "then consider independent eligible work. Record an intentional stop with "
        f"`{stop_command}`; blocked/pressure kinds also require `--recovery` "
        "with evidence of that recovery and what remains beyond your authority. "
        "Other kinds are pause, approval, drained and scope; classify truthfully "
        "and explain unfinished work to the user. Honor explicit pauses and "
        "approval waits immediately; unanswered "
        "optional feedback alone is not a blocker. Never release an assignment "
        "until its writers and external effects are settled."
    )
    # Interrupt/new input must win over a slow read of Beads.
    with file_lock(store.lock_path, timeout=0.2):
        if store.read() != state:
            return {}
        if active or state.corrected_turn:
            return {
                "systemMessage": "Hive executor still has unresolved work. " + message
            }
        store.save(
            replace(
                state, corrected_turn=turn, revision=str(uuid4()), continuation=message
            )
        )
    return {"decision": "block", "reason": message}


def _ids(value: object, project: str, owner: str | None) -> tuple[str, ...]:
    result: list[str] = []
    for raw in sequence(value, "beads"):
        bead = record(raw, "bead")
        metadata = record(bead.get("metadata", {}), "bead metadata")
        if metadata.get("hive_project") != project:
            continue
        status = bead.get("status")
        if owner is None:
            if status != "open" or bead.get("assignee") not in (None, ""):
                continue
        elif bead.get("assignee") != owner or status not in {
            "open",
            "in_progress",
            "blocked",
        }:
            continue
        result.append(string(bead.get("id"), "bead id"))
    return tuple(sorted(result))


def configuration(context: LaunchContext) -> dict[str, object]:
    """Emit mergeable host settings without changing hooks or their trust state."""
    command = _command(context, "hook", "--json")
    return {
        "hooks": {
            event: [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "timeout": 10 if event == "Stop" else 3,
                            "statusMessage": "Checking Hive executor continuation",
                        }
                    ]
                }
            ]
            for event in ("Stop", "UserPromptSubmit", "Interrupt")
        }
    }


def _command(context: LaunchContext, *arguments: str) -> str:
    config = (
        Path(
            os.environ.get(
                "HIVE_BOOTSTRAP_CONFIG", str(Path.home() / "brain/hive.json")
            )
        )
        .expanduser()
        .resolve()
    )
    return shlex.join(
        (
            "env",
            f"HIVE_BOOTSTRAP_CONFIG={config}",
            str(context.repository / "bin/hive"),
            "executor",
            *arguments,
        )
    )
