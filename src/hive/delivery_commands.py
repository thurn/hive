"""Prepare provider effects under ownership checks, then run them without locks."""

from collections.abc import Callable

from hive import commands as c
from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore
from hive.configuration_store import ConfigurationStore
from hive.errors import ErrorCode, HiveError
from hive.launch_context import LaunchContext
from hive.locking import Guards
from hive.model import Bead, Owned, Phase, Preparing, Reviewing, WaitingForDelivery
from hive.tollgate import Tollgate
from hive.tollgate_model import Candidate
from hive.tollgate_process import TollgateProcess
from hive.transitions import require_owner


def candidate_value(
    candidate: Candidate, *, delivered: bool = False
) -> dict[str, object]:
    return {
        "code": "Delivered" if delivered else "Candidate",
        "candidate": candidate.id,
        "source": candidate.source,
        "state": candidate.state,
        "remote": candidate.remote,
        "authorized": candidate.authorized,
        "reason": candidate.reason,
    }


def prepare_external(
    request: c.ExternalRequest, context: LaunchContext
) -> Callable[[], dict[str, object]]:
    """No returned action mutates Beads. Its next transition needs fresh CLI code."""
    store = BeadsStore(BeadsProcess(BeadsConnection.read(context.beads), "hive"))
    guards = Guards(context.state / "locks")
    with guards.admission():
        project = ConfigurationStore(store, guards).read().project(request.project)
        bead: Bead = store.get(request.bead)
        if bead.project != request.project:
            raise HiveError(ErrorCode.INVALID_INPUT, "Task is outside this project")
        require_owner(bead, request.owner)
        if not isinstance(bead.state, Owned):
            raise HiveError(
                ErrorCode.PAUSED, "Deferred work cannot start provider effects"
            )
        phase: Phase = bead.state.phase
    provider: Tollgate = Tollgate(TollgateProcess(project.repository))
    if isinstance(request, c.CreateWorkspace) and isinstance(phase, Preparing):
        preparing: Preparing = phase

        def create() -> dict[str, object]:
            workspace = provider.create_workspace(preparing.branch)
            return {
                "code": "WorkspaceCreated",
                "bead": bead.id,
                "workspace": str(workspace.path),
                "branch": workspace.branch,
                "base": workspace.base,
            }

        return create
    if isinstance(request, c.SubmitWork) and isinstance(phase, Reviewing):
        reviewing: Reviewing = phase

        def submit() -> dict[str, object]:
            candidate = provider.submit(reviewing.workspace, reviewing.source)
            return {
                "code": "Submitted",
                "bead": bead.id,
                "candidate": candidate,
                "source": reviewing.source,
            }

        return submit
    if isinstance(request, c.ApproveWork) and isinstance(phase, WaitingForDelivery):
        waiting: WaitingForDelivery = phase

        def approve() -> dict[str, object]:
            approval = provider.approve(waiting.candidate, waiting.source)
            return {
                "code": "Authorized",
                "bead": bead.id,
                "candidate": approval.candidate,
                "source": approval.source,
                "already_authorized": approval.already_authorized,
            }

        return approve
    raise HiveError(
        ErrorCode.INVALID_INPUT, "Task phase does not permit this provider operation"
    )
