"""Bounded stop feedback for explicitly opted-in Codex and Claude Code executors."""

import os
import shlex
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.errors import HiveError
from hive.executor_diagnostics import Receipt, append, identity
from hive.executor_queue import (
    WAIT_TIMEOUT_SECONDS,
    await_work,
    ids,
    peers,
    settled_ready,
)
from hive.executor_state import ExecutorState, ExecutorStore, StopKind
from hive.jsonvalue import parse, record, sequence, string
from hive.launch_context import LaunchContext
from hive.locking import file_lock
from hive_bootstrap.settings import read_settings

HOSTS = ("codex", "claude-code")


# Claude Code fires UserPromptSubmit for prompts the host injects as well; only
# prompts from a person (or the SDK host relaying one) are new input.
INJECTED_PROMPT_SOURCES: frozenset[str] = frozenset(
    {"system", "loop_wakeup", "schedule_wakeup", "poll_event"}
)
# Claude Code 2.1.293 sends no `source`, so a finished background subagent, shell
# or monitor is recognized by the notification it submits as the prompt.
TASK_NOTIFICATION = "<task-notification>"
# Claude Code also lists ambient work (memory dreams, auto-mode scans,
# teammates) that never wakes the session; only these task types report back.
WAKING_TASK_TYPES: frozenset[str] = frozenset(
    {"shell", "subagent", "monitor", "workflow"}
)


def _host_session(explicit: str | None) -> str:
    """The invoking agent's own native session, never a guess between hosts.

    Pass it explicitly; the environment is a fallback only when exactly one host's
    variable is present, because Claude Code can inherit CODEX_THREAD_ID.
    """
    if explicit is not None:
        return explicit
    codex = os.environ.get("CODEX_THREAD_ID", "")
    claude = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if codex and claude and codex != claude:
        raise ValueError(
            "Both CODEX_THREAD_ID and CLAUDE_CODE_SESSION_ID are set; pass "
            "--session with your own host's session ID"
        )
    return codex or claude


def start(
    context: LaunchContext,
    project: str,
    *,
    continuous: bool = False,
    session: str | None = None,
) -> dict[str, object]:
    settings = read_settings()
    if project not in {item.id for item in settings.projects}:
        raise ValueError("Executor project must be registered in bootstrap settings")
    store = ExecutorStore(context.state, _host_session(session))
    store.start(project, continuous=continuous)
    return _receipt(
        context,
        Receipt("start", "activated", store.session),
        {
            "code": "ExecutorStarted",
            "project": project,
            "session": store.session,
            "continuous": continuous,
        },
    )


def stop(
    context: LaunchContext,
    reason: str,
    kind: StopKind,
    recovery: str | None = None,
    session: str | None = None,
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
    store = ExecutorStore(context.state, _host_session(session))
    outcome = store.stop(evidence)
    return _receipt(
        context,
        Receipt("stop", outcome, store.session, stop_kind=kind.value),
        {
            "code": "ExecutorStopped",
            "kind": kind.value,
            "reason": reason,
            "recovery": recovery,
        },
    )


def wait(
    context: LaunchContext,
    session: str | None = None,
    timeout: float = WAIT_TIMEOUT_SECONDS,
    interval: float = 30,
) -> dict[str, object]:
    """Block an armed executor until peer executors leave it work to claim.

    It reads the bound project's queue and never claims, so the agent still
    checks scope and prerequisite outcomes before claiming what it reports.
    """
    if timeout < 0 or interval <= 0:
        raise ValueError("Await requires a non-negative timeout and positive interval")
    store: ExecutorStore = ExecutorStore(context.state, _host_session(session))
    state = store.read()
    if state is None or not state.active:
        raise ValueError("Await requires an armed executor; run executor start first")

    revision: str = state.revision

    def armed() -> bool:
        # A stop, new input or a fresh start replaces the revision this wait
        # began under; Claude Code records a background wait without saving.
        current = store.read()
        return current is not None and current.revision == revision

    return await_work(
        BeadsProcess(BeadsConnection.read(context.beads), timeout=10),
        state.project,
        store.session,
        timeout=timeout,
        interval=interval,
        active=armed,
    )


def _receipt(
    context: LaunchContext, receipt: Receipt, response: dict[str, object]
) -> dict[str, object]:
    warning = append(context, receipt)
    if warning is None:
        return response
    message = str(response.get("systemMessage", ""))
    return {
        **response,
        "systemMessage": (
            message if warning in message else f"{message} {warning}".strip()
        ),
    }


def handle(context: LaunchContext, payload: str) -> dict[str, object]:
    """Retain invocation before native reads and its decision afterward."""
    value: dict[str, object] = {}
    try:
        value = record(parse(payload), "hook input")
    except (HiveError, ValueError):
        pass
    session = identity(value.get("session_id"))
    turn = identity(_turn(value))
    event = value.get("hook_event_name")
    name = (
        event
        if isinstance(event, str) and event in {"Stop", "Interrupt", "UserPromptSubmit"}
        else "invalid"
    )
    # Do not retain arbitrary event names, prompt text, error details or reasons.
    receipt = Receipt("hook-invoked", str(name), session, turn)
    warning = append(context, receipt)
    response: dict[str, object]
    try:
        response, outcome = _handle(context, value)
    except (HiveError, OSError, ValueError) as error:
        response = {"systemMessage": f"Hive executor stop check unavailable: {error}"}
        outcome = "unavailable"
    if warning is not None:
        response = {
            **response,
            "systemMessage": f"{response.get('systemMessage', '')} {warning}".strip(),
        }
    return _receipt(context, Receipt(str(name), outcome, session, turn), response)


def _handle(
    context: LaunchContext, value: dict[str, object]
) -> tuple[dict[str, object], str]:
    event = string(value.get("hook_event_name"), "hook event")
    if event not in {"Stop", "Interrupt", "UserPromptSubmit"}:
        return {}, "ignored"
    # Native hook identity is authoritative, never the inherited shell identity.
    store = ExecutorStore(context.state, string(value.get("session_id"), "session"))
    if event == "UserPromptSubmit" and _injected(value):
        return {}, "injected"
    if event != "Stop":
        outcome = store.stop(
            f"{event}: executor must explicitly opt in again",
            new_input=_prompt(value) if event == "UserPromptSubmit" else None,
        )
        return {}, outcome
    state = store.read()
    if state is None:
        return {}, "unbound"
    if not state.active:
        return {}, "inactive"
    # Claude Code ends a turn to wait for background work that will wake it.
    if _waiting(value):
        return {}, "waiting"
    # Both hosts send stop_hook_active; corrected_turn bounds correction regardless.
    active = value.get("stop_hook_active", False)
    if not isinstance(active, bool):
        raise ValueError("Stop requires a boolean stop_hook_active")
    process = BeadsProcess(BeadsConnection.read(context.beads), timeout=2)
    assigned = ids(process.assigned(store.session), state.project, store.session)
    ready = (
        ids(process.ready(state.project, store.session), state.project, None)
        if state.continuous
        else ()
    )
    stop_command = _command(
        context, "stop", "--kind", "KIND", "--reason", "CONCRETE EVIDENCE"
    )
    if not assigned and not ready:
        if not state.continuous:
            return {}, "drained"
        # Peer executors may still land work that unblocks the rest of the queue.
        found = peers(process, state.project, store.session, ready)
        ready = settled_ready(process, state.project, store.session, found)
        if not ready:
            if not found.pending:
                return {}, "drained"
            await_command = _command(
                context, "await", "--session", store.session, "--json"
            )
            message = (
                f"Hive executor {state.project}: no unfinished assignment or unclaimed "
                f"ready bead, but peer executors hold {', '.join(found.held)} while "
                f"{', '.join(found.waiting)} wait. The queue is not drained. Run "
                f"`{await_command}` (in Claude Code as a background command, then end "
                "the turn; in Codex retain one foreground wait for the default 65 "
                "minutes, using the executor skill's shared waiting guidance). "
                "Keep pending process-output collection inside one long-yield tool "
                "cell; do not shorten the native timeout to 60 seconds, poll Beads "
                "from the agent, or narrate unchanged state. Repeat only after the "
                "native long wait returns ExecutorAwaitTimeout, and "
                "resume executor work when it reports ready or assigned work. It "
                "reports a drained queue once peers hold nothing that open beads wait "
                "on. Stop instead only for an explicit pause, an outstanding approval, "
                "a user scope limit or a blocker that survived justiciar recovery, "
                f"recorded with `{stop_command}`."
            )
            return _correct(store, state, value, active, message)
    message = (
        f"Hive executor {state.project}: unfinished assignments: "
        f"{', '.join(assigned) or 'none'}; unclaimed ready candidates: "
        f"{', '.join(ready) or 'none'}. "
        "Reconcile remaining acceptance, writers and delivery before stopping; "
        "promotion alone does not complete acceptance. Inspect candidate scope, "
        "prerequisite outcomes, approvals, ownership and resource pressure before "
        "claiming. Continue eligible project work under the executor skill unless "
        "the user explicitly limited the request to named work. Before treating "
        "a blocker, retry cutoff, timing miss or resource pressure as a reason to "
        "end work, invoke the justiciar skill in this same task: inspect evidence, "
        "repair or relax agent-imposed constraints within existing authority, "
        "record changes and consequences, then resume executor work. Never waive "
        "explicit user acceptance or broaden authorization. A retry limit ends "
        "identical retries, not useful recovery. For a genuine remaining external "
        "dependency or capacity limit, checkpoint and settle writers/delivery, "
        "then consider independent eligible work in the project queue. "
        "Record an intentional stop with "
        f"`{stop_command}`; blocked/pressure kinds also require `--recovery` "
        "with evidence of that recovery and what remains beyond your authority. "
        "Other kinds are pause, approval, drained and scope; classify truthfully "
        "and explain unfinished work to the user. Honor explicit pauses and "
        "approval waits immediately; unanswered "
        "optional feedback alone is not a blocker. Never release an assignment "
        "until its writers and external effects are settled."
    )
    return _correct(store, state, value, active, message)


def _correct(
    store: ExecutorStore,
    state: ExecutorState,
    value: dict[str, object],
    active: bool,
    message: str,
) -> tuple[dict[str, object], str]:
    # Any marker bounds the correction when a host sends no turn identifier.
    raw_turn = _turn(value)
    turn = string(raw_turn, "turn") if raw_turn is not None else str(uuid4())
    # Interrupt/new input must win over a slow read of Beads.
    with file_lock(store.lock_path, timeout=0.2):
        if store.read() != state:
            return {}, "superseded"
        if active or state.corrected_turn:
            return {
                "systemMessage": "Hive executor still has unresolved work. " + message
            }, "warning"
        store.save(
            replace(
                state, corrected_turn=turn, revision=str(uuid4()), continuation=message
            )
        )
    return {"decision": "block", "reason": message}, "correction"


def _turn(value: dict[str, object]) -> object:
    """Codex sends turn_id; Claude Code identifies the prompt with prompt_id."""
    return value.get("turn_id", value.get("prompt_id"))


def _injected(value: dict[str, object]) -> bool:
    source = value.get("source")
    if isinstance(source, str) and source in INJECTED_PROMPT_SOURCES:
        return True
    return _prompt(value).lstrip().startswith(TASK_NOTIFICATION)


def _waiting(value: dict[str, object]) -> bool:
    """A Claude Code turn ended for background work or a one-shot wakeup.

    A recurring cron would hold the check off for the rest of the session, so
    only a single scheduled wakeup counts. Codex sends neither field.
    """
    tasks = (
        record(task, "background task") for task in _list(value, "background_tasks")
    )
    crons = (record(cron, "session cron") for cron in _list(value, "session_crons"))
    return any(_waking(task.get("type")) for task in tasks) or any(
        cron.get("recurring") is False for cron in crons
    )


def _waking(kind: object) -> bool:
    return isinstance(kind, str) and kind in WAKING_TASK_TYPES


def _list(value: dict[str, object], field: str) -> list[object]:
    return sequence(value[field], field) if field in value else []


def _prompt(value: dict[str, object]) -> str:
    """Prompt text for continuation matching; absent text still disarms."""
    return string(value["prompt"], "prompt", empty=True) if "prompt" in value else ""


def configuration(context: LaunchContext, host: str = "codex") -> dict[str, object]:
    """Emit mergeable host settings without changing hooks or their trust state.

    Claude Code has no interrupt hook; its next prompt disarms instead.
    """
    if host not in HOSTS:
        raise ValueError(f"Unknown executor hook host: {host}")
    events = (
        ("Stop", "UserPromptSubmit")
        if host == "claude-code"
        else ("Stop", "UserPromptSubmit", "Interrupt")
    )
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
            for event in events
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
