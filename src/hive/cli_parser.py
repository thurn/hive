"""Turn command-line input into validated requests at one explicit boundary."""

import argparse
import os
from pathlib import Path
from typing import NoReturn

from hive import commands as c
from hive.bead_json import bead_id
from hive.beads_store import NewTask
from hive.configuration import Project
from hive.errors import ErrorCode, HiveError
from hive.identity import (
    CandidateId,
    CodexProjectId,
    CodexTaskId,
    CodexTurnId,
    ProjectId,
    SourceCommit,
)
from hive.jsonvalue import integer, parse, record, sequence, string
from hive.model import (
    ArtifactDelivery,
    Capacity,
    CodeDelivery,
    Deferred,
    Delivery,
    Owner,
    PauseCondition,
    PauseReason,
    Queued,
    Unstarted,
    WorkKind,
)
from hive.phase_json import decode_phase
from hive.session import AppliedName, FailedName, Focus, Role


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise HiveError(ErrorCode.INVALID_INPUT, message)


def parser() -> Parser:
    root = Parser(prog="hive", description="Independent workers; shared Beads tasks.")
    root.add_argument(
        "--json", action="store_true", help="structured output; accepted anywhere"
    )
    groups = root.add_subparsers(dest="group", required=True)
    session = groups.add_parser("session").add_subparsers(dest="action", required=True)
    listing = session.add_parser("list", help="show enrolled tasks and title drift")
    listing.add_argument("--project")
    enter = session.add_parser("enter", help="enroll or record a role/bead transition")
    enter.add_argument("--task", default=os.environ.get("CODEX_THREAD_ID"))
    enter.add_argument("--project", required=True)
    enter.add_argument("--role", choices=[r.value for r in Role], required=True)
    enter.add_argument("--subject", required=True)
    enter.add_argument("--bead")
    enter.add_argument("--stage", default="")
    enter.add_argument("--inline-bead", action="store_true")
    named = session.add_parser("named", help="record the native title tool result")
    named.add_argument("--task", default=os.environ.get("CODEX_THREAD_ID"))
    named.add_argument("--title", required=True)
    outcome = named.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--applied", action="store_true")
    outcome.add_argument("--error")
    workspace = groups.add_parser("workspace").add_subparsers(
        dest="action", required=True
    )
    create = workspace.add_parser(
        "create", help="create the owned bead's Tollgate worktree"
    )
    create.add_argument("bead")
    for field in ("project", "owner", "turn"):
        create.add_argument(f"--{field}", required=True)
    delivery = groups.add_parser("delivery").add_subparsers(
        dest="action", required=True
    )
    for name in ("submit", "approve"):
        operation = delivery.add_parser(name)
        operation.add_argument("bead")
        for field in ("project", "owner", "turn"):
            operation.add_argument(f"--{field}", required=True)
    for name in ("wait", "inspect"):
        operation = delivery.add_parser(name)
        operation.add_argument("candidate")
        operation.add_argument("--project", required=True)
        if name == "wait":
            operation.add_argument("--timeout-seconds", type=int, default=3600)
    groups.add_parser("mcp", help="serve blocking tools over stdio")
    groups.add_parser("hook", help="handle one native Stop/Interrupt event on stdin")
    groups.add_parser("source", help="show the selected local-master source")
    status = groups.add_parser("status", help="show work and malformed records")
    status.add_argument("--project")
    config = groups.add_parser("config").add_subparsers(dest="action", required=True)
    config.add_parser("show", help="read project bindings and admission settings")
    config.add_parser(
        "initialize", help="initialize Hive records in an existing hv- server database"
    )
    register = config.add_parser("register", help="register or update one project")
    for name in ("project", "repository", "invariants", "native-id"):
        register.add_argument(f"--{name}", required=True)
    capacity = config.add_parser(
        "capacity", help="replace global and per-project limits"
    )
    capacity.add_argument("--global-limit", type=int, required=True)
    capacity.add_argument("--project-limits-json", default="{}")
    task = groups.add_parser("task").add_subparsers(dest="action", required=True)
    show = task.add_parser("show")
    show.add_argument("bead")
    ready = task.add_parser("ready", help="advisory ready list; does not claim work")
    ready.add_argument("--project", required=True)
    add = task.add_parser("add")
    for name in ("project", "title", "description", "acceptance"):
        add.add_argument(f"--{name}", required=True)
    add.add_argument("--priority", type=int, choices=range(5), default=2)
    add.add_argument("--kind", choices=[k.value for k in WorkKind], default="code")
    add.add_argument("--depends-on", action="append", default=[])
    add.add_argument("--defer-reason", choices=[r.value for r in PauseReason])
    add.add_argument("--note")
    for name in (
        "claim",
        "next",
        "advance",
        "defer",
        "resume",
        "settle",
        "complete",
        "cancel",
        "enter-turn",
        "dependency",
        "priority",
    ):
        command = task.add_parser(name)
        if name != "next":
            command.add_argument("bead")
        command.add_argument("--project", required=True)
        if name in {"claim", "next", "advance", "settle", "complete", "enter-turn"}:
            command.add_argument("--owner", required=True)
            command.add_argument("--turn", required=True)
        if name == "advance":
            command.add_argument("--phase-json", required=True)
        elif name == "defer":
            command.add_argument("--owner", help="expected native task owner")
            command.add_argument("--turn", help="expected native owning turn")
            command.add_argument(
                "--reason", choices=[r.value for r in PauseReason], required=True
            )
            command.add_argument("--note", required=True)
        elif name == "resume":
            command.add_argument("--reason", choices=[r.value for r in PauseReason])
            command.add_argument("--user-authorized", action="store_true")
        elif name == "complete":
            command.add_argument("--summary", required=True)
            command.add_argument("--delivery-json", required=True)
        elif name == "cancel":
            command.add_argument("--reason", required=True)
        elif name == "enter-turn":
            command.add_argument("--previous-turn", required=True)
        elif name == "dependency":
            command.add_argument("--prerequisite", required=True)
            command.add_argument("--remove", action="store_true")
        elif name == "priority":
            command.add_argument(
                "--priority", type=int, choices=range(5), required=True
            )
    return root


def owner(data: dict[str, object]) -> Owner:
    return Owner(
        CodexTaskId(string(data.get("owner"), "owner")),
        CodexTurnId(string(data.get("turn"), "turn")),
    )


def delivery(value: object) -> Delivery:
    data = record(value, "delivery")
    if data.get("kind") == "artifact" and set(data) == {"kind", "location"}:
        return ArtifactDelivery(string(data.get("location"), "artifact location"))
    if data.get("kind") == "code" and set(data) == {"kind", "source", "candidate"}:
        return CodeDelivery(
            SourceCommit(string(data.get("source"), "source")),
            CandidateId(string(data.get("candidate"), "candidate")),
        )
    raise HiveError(
        ErrorCode.INVALID_INPUT,
        "Delivery requires artifact location or code source and candidate",
    )


def decode(data: dict[str, object]) -> c.Request:
    group = data.get("group")
    if group == "mcp":
        raise HiveError(
            ErrorCode.INVALID_INPUT, "Start stdio MCP with hive mcp and no flags"
        )
    if group == "source":
        return c.SourceRequest()
    if group == "status":
        project = data.get("project")
        return c.Status(
            None if project is None else ProjectId(string(project, "project"))
        )
    action = data.get("action")
    if group == "session":
        if action == "list":
            project = data.get("project")
            return c.ListSessions(
                None if project is None else ProjectId(string(project, "project"))
            )
        task = CodexTaskId(
            string(data.get("task"), "native task; pass --task outside Codex")
        )
        if action == "named":
            outcome = (
                AppliedName()
                if data.get("applied") is True
                else FailedName(string(data.get("error"), "rename error"))
            )
            return c.RecordName(task, string(data.get("title"), "title"), outcome)
        return c.EnterSession(
            task,
            ProjectId(string(data.get("project"), "project")),
            Focus(
                Role(string(data.get("role"), "role")),
                string(data.get("subject"), "subject"),
                None if data.get("bead") is None else bead_id(data.get("bead")),
                string(data.get("stage"), "stage", empty=True),
            ),
            data.get("inline_bead") is True,
        )
    if group in {"workspace", "delivery"}:
        project = ProjectId(string(data.get("project"), "project"))
        if action == "wait":
            return c.WaitDelivery(
                CandidateId(string(data.get("candidate"), "candidate")),
                project,
                integer(data.get("timeout_seconds"), "wait timeout", minimum=1),
            )
        if action == "inspect":
            return c.InspectDelivery(
                CandidateId(string(data.get("candidate"), "candidate")), project
            )
        identifier = bead_id(data.get("bead"))
        if group == "workspace":
            return c.CreateWorkspace(identifier, project, owner(data))
        if action == "submit":
            return c.SubmitWork(identifier, project, owner(data))
        return c.ApproveWork(identifier, project, owner(data))
    if group == "config":
        if action == "show":
            return c.ReadConfiguration()
        if action == "initialize":
            return c.Initialize()
        if action == "register":
            return c.Register(
                Project(
                    ProjectId(string(data.get("project"), "project")),
                    Path(string(data.get("repository"), "repository"))
                    .expanduser()
                    .resolve(),
                    Path(string(data.get("invariants"), "invariants"))
                    .expanduser()
                    .resolve(),
                    CodexProjectId(string(data.get("native_id"), "native project")),
                )
            )
        limits = record(
            parse(string(data.get("project_limits_json"), "project limits"))
        )
        return c.SetCapacity(
            Capacity(
                integer(data.get("global_limit"), "global limit", minimum=1),
                tuple(
                    (ProjectId(k), integer(v, "project limit", minimum=1))
                    for k, v in limits.items()
                ),
            )
        )
    if action == "show":
        return c.Show(bead_id(data.get("bead")))
    project = ProjectId(string(data.get("project"), "project"))
    if action == "ready":
        return c.Ready(project)
    if action == "add":
        reason = data.get("defer_reason")
        state = (
            Queued()
            if reason is None
            else Deferred(
                (
                    PauseCondition(
                        PauseReason(string(reason, "pause reason")),
                        string(data.get("note"), "pause note"),
                    ),
                ),
                Unstarted(),
            )
        )
        if reason is None and data.get("note") is not None:
            raise HiveError(
                ErrorCode.INVALID_INPUT, "A pause note requires --defer-reason"
            )
        return c.Add(
            NewTask(
                project,
                string(data.get("title"), "title"),
                string(data.get("description"), "description"),
                string(data.get("acceptance"), "acceptance"),
                integer(data.get("priority"), "priority"),
                WorkKind(string(data.get("kind"), "work kind")),
                state,
            ),
            tuple(
                bead_id(item)
                for item in sequence(data.get("depends_on"), "dependencies")
            ),
        )
    if action in {"claim", "next"}:
        return c.Claim(
            project,
            owner(data),
            None if action == "next" else bead_id(data.get("bead")),
        )
    identifier = bead_id(data.get("bead"))
    if action == "dependency":
        return c.Dependency(
            identifier,
            project,
            bead_id(data.get("prerequisite")),
            data.get("remove") is True,
        )
    if action == "priority":
        return c.Prioritize(
            identifier, project, integer(data.get("priority"), "priority")
        )
    event: c.Transition
    if action == "advance":
        event = c.Advance(
            owner(data), decode_phase(parse(string(data.get("phase_json"), "phase")))
        )
    elif action == "defer":
        event = c.Defer(
            PauseReason(string(data.get("reason"), "pause reason")),
            string(data.get("note"), "pause note"),
            (
                None
                if data.get("owner") is None and data.get("turn") is None
                else owner(data)
            ),
        )
    elif action == "resume":
        event = c.Resume(
            (
                None
                if data.get("reason") is None
                else PauseReason(string(data.get("reason"), "pause reason"))
            ),
            data.get("user_authorized") is True,
        )
    elif action == "settle":
        event = c.Settle(owner(data))
    elif action == "complete":
        event = c.Complete(
            owner(data),
            string(data.get("summary"), "summary"),
            delivery(parse(string(data.get("delivery_json"), "delivery"))),
        )
    elif action == "enter-turn":
        current = owner(data)
        event = c.EnterTurn(
            Owner(
                current.task,
                CodexTurnId(string(data.get("previous_turn"), "previous turn")),
            ),
            current,
        )
    elif action == "cancel":
        event = c.Cancel(string(data.get("reason"), "cancellation reason"))
    else:
        raise HiveError(ErrorCode.INVALID_INPUT, "Unknown task operation")
    return c.Change(identifier, project, event)


def parse_request(arguments: list[str]) -> tuple[c.Request, bool]:
    structured = "--json" in arguments
    arguments = [argument for argument in arguments if argument != "--json"]
    try:
        return decode(record(vars(parser().parse_args(arguments)))), structured
    except HiveError as error:
        raise HiveError(ErrorCode.INVALID_INPUT, error.detail) from error
