"""Observe provider settlement before leaving a retained delivery phase."""

from collections.abc import Callable

from hive import commands as c
from hive import transitions
from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore
from hive.configuration import Project
from hive.configuration_store import ConfigurationStore
from hive.errors import ErrorCode, HiveError
from hive.launch_context import LaunchContext
from hive.locking import Guards
from hive.model import (
    ArtifactDelivery,
    Bead,
    Deferred,
    Draining,
    Implementing,
    Owned,
    WaitingForDelivery,
)
from hive.task_service import TaskService
from hive.task_status import task_value
from hive.tollgate import Tollgate
from hive.tollgate_model import CandidateState
from hive.tollgate_process import TollgateProcess


def prepare(
    request: c.Change, context: LaunchContext
) -> Callable[[], dict[str, object]] | None:
    """Validate under the launch guard, inspect unlocked, then compare state.

    This checks provider activity, not the executor's local writer inventory.
    Stopping local commands remains required before settlement.
    """
    event: c.Transition = request.transition
    if not isinstance(event, (c.Advance, c.Settle, c.Complete)):
        return None
    if isinstance(event, c.Advance) and not isinstance(event.phase, Implementing):
        return None
    if isinstance(event, c.Complete) and isinstance(event.delivery, ArtifactDelivery):
        return None
    guards: Guards = Guards(context.state / "locks")
    store: BeadsStore = BeadsStore(
        BeadsProcess(BeadsConnection.read(context.beads), "hive"),
        guards.write_barrier,
    )
    with guards.admission():
        bead: Bead = store.get(request.bead)
        if bead.project != request.project:
            raise HiveError(ErrorCode.INVALID_INPUT, "Task is outside this project")
        state = bead.state
        phase = (
            state.phase
            if isinstance(state, Owned)
            else (
                state.work.phase
                if isinstance(state, Deferred) and isinstance(state.work, Draining)
                else None
            )
        )
        if not isinstance(phase, WaitingForDelivery):
            return None
        changed: Bead
        if isinstance(event, c.Advance):
            changed = transitions.advance(bead, event.owner, event.phase)
        elif isinstance(event, c.Settle):
            changed = transitions.settle(bead, event.owner)
        else:
            changed = transitions.complete(
                bead, event.owner, event.summary, event.delivery
            )
        project: Project = (
            ConfigurationStore(store, guards).read().project(request.project)
        )
    waiting: WaitingForDelivery = phase

    def finish() -> dict[str, object]:
        provider = Tollgate(TollgateProcess(project.repository))
        outcome = provider.inspect_settled(waiting.candidate, waiting.source)
        if outcome.state == CandidateState.EXTERNALLY_INTEGRATED and not isinstance(
            event, c.Settle
        ):
            raise HiveError(
                ErrorCode.RECOVERY_REQUIRED,
                "Tollgate integrated this source through another base; defer and reconcile delivery",
            )
        if isinstance(event, c.Complete) and outcome.state != CandidateState.PROMOTED:
            raise HiveError(ErrorCode.VALIDATION_FAILED, "Candidate was not delivered")
        if isinstance(event, c.Advance) and outcome.state == CandidateState.PROMOTED:
            raise HiveError(
                ErrorCode.INVALID_INPUT,
                "Candidate is already delivered; complete it instead of repeating implementation",
            )
        with context.reenter_mutation():
            saved = TaskService(store, guards).finish_observed_change(bead, changed)
        return {"code": "Changed", "task": task_value(saved)}

    return finish
