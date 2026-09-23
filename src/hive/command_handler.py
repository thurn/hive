"""Dispatch typed commands; transport adapters do not own workflow policy."""

from typing import assert_never

from hive import commands as c
from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore
from hive.configuration import Configuration, configuration_metadata
from hive.configuration_store import ConfigurationStore
from hive.delivery_commands import candidate_value
from hive.errors import ErrorCode, HiveError
from hive.filing import Filing
from hive.launch_context import LaunchContext
from hive.locking import Guards
from hive.model import Bead
from hive.session import value as session_value
from hive.session_store import SessionStore
from hive.task_service import TaskService
from hive.task_status import read_status, status_value, task_value
from hive.tollgate import Tollgate
from hive.tollgate_process import TollgateProcess


def configuration_result(value: Configuration) -> dict[str, object]:
    return {
        "code": "Configured",
        "id": value.id,
        **configuration_metadata(value.projects, value.capacity),
    }


def transition(service: TaskService, request: c.Change) -> Bead:
    bead, project, event = request.bead, request.project, request.transition
    if isinstance(event, c.Advance):
        return service.advance(bead, project, event.owner, event.phase)
    if isinstance(event, c.Defer):
        return service.defer(
            bead,
            project,
            event.reason,
            event.note,
            expected_owner=event.expected_owner,
        )
    if isinstance(event, c.Resume):
        return service.resume(
            bead, project, reason=event.reason, user_authorized=event.user_authorized
        )
    if isinstance(event, c.Settle):
        return service.settle(bead, project, event.owner)
    if isinstance(event, c.Complete):
        return service.complete(
            bead, project, event.owner, event.summary, event.delivery
        )
    if isinstance(event, c.Cancel):
        return service.cancel(bead, project, event.reason)
    if isinstance(event, c.EnterTurn):
        return service.enter_turn(bead, project, event.previous, event.current)
    assert_never(event)


def handle(request: c.Request, context: LaunchContext) -> dict[str, object]:
    if isinstance(
        request,
        (
            c.CollectTranscript,
            c.ReadUsage,
            c.SweepCollection,
            c.WatchCollection,
            c.CollectionStatus,
        ),
    ):
        # Observation is independent of Beads and imports no collector on the
        # normal task path. The CLI has already released the maintenance guard.
        import sqlite3

        from hive.usage_store import UsageStore

        try:
            if isinstance(request, c.WatchCollection):
                from hive.collection_transport import run

                return run(
                    context.state, request.index, request.limit, request.interval
                )
            if isinstance(request, c.SweepCollection):
                from hive.collection import sweep

                return sweep(context, request.index, request.limit)
            if isinstance(request, c.CollectionStatus):
                from hive.collection_registry import CollectionRegistry

                return CollectionRegistry(
                    UsageStore(context.state / "telemetry.sqlite3")
                ).status()
            observations = UsageStore(context.state / "telemetry.sqlite3")
            return (
                observations.collect(request.task, request.path, budget=request.budget)
                if isinstance(request, c.CollectTranscript)
                else observations.report(request.task)
            )
        except (OSError, sqlite3.Error) as error:
            raise HiveError(
                ErrorCode.PROVIDER_UNAVAILABLE, f"Telemetry unavailable: {error}"
            ) from error
    if isinstance(request, c.SourceRequest):
        return {
            "code": "SourceSelected",
            "commit": context.commit,
            "directory": str(context.source),
        }
    store = BeadsStore(BeadsProcess(BeadsConnection.read(context.beads), "hive"))
    guards = Guards(context.state / "locks")
    configuration = ConfigurationStore(store, guards)
    service = TaskService(store, guards)
    if isinstance(request, (c.EnterSession, c.RecordName, c.ListSessions)):
        registry = SessionStore(store, guards)
        if isinstance(request, c.ListSessions):
            return {
                "code": "Sessions",
                "sessions": [session_value(s) for s in registry.list(request.project)],
            }
        session = (
            registry.enter(
                request.task,
                request.project,
                request.focus,
                inline_bead=request.inline_bead,
            )
            if isinstance(request, c.EnterSession)
            else registry.record_name(request.task, request.title, request.result)
        )
        return {"code": "Session", "session": session_value(session)}
    if isinstance(request, (c.CreateWorkspace, c.SubmitWork, c.ApproveWork)):
        raise HiveError(
            ErrorCode.INVALID_INPUT,
            "Provider effects require unlocked external dispatch",
        )
    if isinstance(request, (c.WaitDelivery, c.InspectDelivery)):
        project = configuration.read().project(request.project)
        provider = Tollgate(TollgateProcess(project.repository))
        if isinstance(request, c.WaitDelivery):
            return candidate_value(
                provider.wait(request.candidate, timeout=request.timeout),
                delivered=True,
            )
        return candidate_value(provider.inspect(request.candidate))
    if isinstance(request, c.ReadConfiguration):
        return configuration_result(configuration.read())
    if isinstance(request, c.Initialize):
        return configuration_result(configuration.initialize())
    if isinstance(request, c.Register):
        old = configuration.read()
        projects = tuple(p for p in old.projects if p.id != request.project.id) + (
            request.project,
        )
        return configuration_result(
            configuration.replace(projects, old.capacity, observed=old)
        )
    if isinstance(request, c.SetCapacity):
        old = configuration.read()
        return configuration_result(
            configuration.replace(old.projects, request.capacity, observed=old)
        )
    if isinstance(request, c.Status):
        return status_value(read_status(store, request.project), request.project)
    if isinstance(request, c.Ready):
        return {
            "code": "Ready",
            "tasks": [task_value(bead) for bead in service.ready(request.project)],
        }
    if isinstance(request, c.Prioritize):
        service.prioritize(request.bead, request.project, request.priority)
        return {"code": "Updated", "id": request.bead, "priority": request.priority}
    bead: Bead
    code: str
    if isinstance(request, c.Show):
        bead, code = store.get(request.bead), "Task"
    elif isinstance(request, c.Add):
        bead = Filing(store, guards).file(request.task, request.dependencies)
        code = "Filed"
    elif isinstance(request, c.Claim):
        bead = service.claim(request.project, request.owner, request.bead)
        code = "Claimed"
    elif isinstance(request, c.Change):
        bead, code = transition(service, request), "Changed"
    elif isinstance(request, c.Dependency):
        bead = Filing(store, guards).dependency(
            request.bead, request.project, request.prerequisite, remove=request.remove
        )
        code = "DependencyChanged"
    else:
        assert_never(request)
    return {"code": code, "task": task_value(bead)}
