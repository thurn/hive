"""Source-selected diagnostics and derived observation."""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import NoReturn

from hive.bead_history import refresh as refresh_history
from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.collection import sweep
from hive.collection_registry import CollectionRegistry
from hive.cost_report import report
from hive.errors import ErrorCode, HiveError
from hive.identity import CodexTaskId, PricingTier
from hive.launch_context import LaunchContext
from hive.transcript_discovery import probe
from hive.usage_store import UsageStore


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise HiveError(ErrorCode.INVALID_INPUT, message)


def main() -> int:
    try:
        context = LaunchContext.read()
        arguments = sys.argv[1:]
        parser = Parser(prog="hive")
        parser.add_argument("--json", action="store_true")
        groups = parser.add_subparsers(dest="group", required=True)
        groups.add_parser("source")
        from hive.dashboard_api import add_parser, dispatch

        add_parser(groups)
        executor = groups.add_parser("executor")
        executor_actions = executor.add_subparsers(dest="action", required=True)
        executor_start = executor_actions.add_parser("start")
        executor_start.add_argument("--project", required=True)
        executor_stop = executor_actions.add_parser("stop")
        from hive.executor_state import StopKind

        executor_stop.add_argument("--reason", required=True)
        executor_stop.add_argument(
            "--kind", choices=[kind.value for kind in StopKind], required=True
        )
        executor_stop.add_argument("--recovery")
        executor_actions.add_parser("hook")
        executor_actions.add_parser("hook-config")
        executor_diagnostics = executor_actions.add_parser("diagnostics")
        executor_diagnostics.add_argument("--session")
        cost = groups.add_parser("cost")
        cost_target = cost.add_mutually_exclusive_group(required=True)
        cost_target.add_argument("--task")
        cost_target.add_argument("--bead")
        cost_target.add_argument("--reconcile", action="store_true")
        cost.add_argument(
            "--native-index", default=str(Path.home() / ".codex/state_5.sqlite")
        )
        cost.add_argument(
            "--tier", choices=[tier.value for tier in PricingTier], default=None
        )
        cost.add_argument("--requests", action="store_true")
        cost.add_argument("--cursor")
        telemetry = groups.add_parser("telemetry")
        actions = telemetry.add_subparsers(dest="action", required=True)
        collect = actions.add_parser("collect")
        collect.add_argument("--task", required=True)
        collect.add_argument("--transcript", required=True)
        collect.add_argument("--max-bytes", type=int, default=1_048_576)
        collect.add_argument("--from-start", action="store_true")
        usage = actions.add_parser("usage")
        usage.add_argument("--task", required=True)
        actions.add_parser("status")
        actions.add_parser("reset-bead-events")
        otlp_config = actions.add_parser("otlp-config")
        otlp_config.add_argument("--port", type=int, default=4319)
        otlp_config.add_argument("--secret-file")
        links_parser = actions.add_parser("links")
        links_parser.add_argument(
            "--native-index", default=str(Path.home() / ".codex/state_5.sqlite")
        )
        for name in ("sweep", "watch"):
            operation = actions.add_parser(name)
            operation.add_argument("--native-index", required=True)
            operation.add_argument(
                "--batch-size", type=int, choices=range(1, 65), default=32
            )
            if name == "watch":
                operation.add_argument("--interval-seconds", type=int, default=5)
                operation.add_argument("--otlp-port", type=int, nargs="?", const=4319)
        structured = "--json" in arguments
        parsed = parser.parse_args([arg for arg in arguments if arg != "--json"])
        context.release()
        if parsed.group == "source":
            result: dict[str, object] = {
                "code": "SourceSelected",
                "commit": context.commit,
                "directory": str(context.source),
            }
        elif parsed.group == "executor":
            from hive.executor_hook import configuration, handle, start, stop

            if parsed.action == "start":
                result = start(context, parsed.project)
            elif parsed.action == "stop":
                result = stop(
                    context, parsed.reason, StopKind(parsed.kind), parsed.recovery
                )
            elif parsed.action == "hook-config":
                result = configuration(context)
            elif parsed.action == "diagnostics":
                from hive.executor_diagnostics import identity, read

                if parsed.session is not None and identity(parsed.session) is None:
                    raise ValueError("Diagnostics session must be a native UUID")
                result = read(context, parsed.session)
            else:
                result = handle(context, sys.stdin.read(1_048_577))
        elif parsed.group == "dashboard":
            result = dispatch(context, parsed)
        elif parsed.group == "cost":
            if parsed.task is None:
                if parsed.reconcile and (parsed.requests or parsed.cursor is not None):
                    raise HiveError(
                        ErrorCode.INVALID_INPUT,
                        "Request paging requires --task or --bead",
                    )
                from hive.bead_cost import report as bead_report

                store = UsageStore(context.state / "telemetry.sqlite3")
                registry = CollectionRegistry(store)
                history_error: str | None = None
                try:
                    links, gaps = refresh_history(
                        store,
                        BeadsProcess(BeadsConnection.read(context.beads)),
                        time.monotonic() + 2,
                    )
                    registry.refresh(links, gaps, None)
                except (HiveError, OSError) as error:
                    history_error = str(error)
                result = bead_report(
                    store,
                    parsed.bead,
                    None if parsed.tier is None else PricingTier(parsed.tier),
                    history_error=history_error,
                )
                if parsed.cursor is not None and not parsed.requests:
                    raise HiveError(
                        ErrorCode.INVALID_INPUT, "--cursor requires --requests"
                    )
                if parsed.requests:
                    if parsed.tier not in (None, "standard"):
                        raise HiveError(
                            ErrorCode.INVALID_INPUT,
                            "Bead request pages use the standard Codex tier",
                        )
                    from hive.dashboard_detail import page

                    with store.connect(write=False) as connection:
                        result.update(
                            page(connection, "bead:" + parsed.bead, parsed.cursor)
                        )
            else:
                store = UsageStore(context.state / "telemetry.sqlite3")
                registry = CollectionRegistry(store)
                discoveries, _ = probe(
                    (CodexTaskId(parsed.task),),
                    Path(parsed.native_index),
                    context.claude_projects,
                    registry,
                )
                found = discoveries[CodexTaskId(parsed.task)]
                if parsed.cursor is not None and not parsed.requests:
                    raise HiveError(
                        ErrorCode.INVALID_INPUT, "--cursor requires --requests"
                    )
                if parsed.requests:
                    from hive.request_detail import report as request_report

                    result = request_report(
                        store,
                        CodexTaskId(parsed.task),
                        None if parsed.tier is None else PricingTier(parsed.tier),
                        cursor=parsed.cursor,
                        host_hint=found.host,
                    )
                else:
                    result = report(
                        store,
                        CodexTaskId(parsed.task),
                        None if parsed.tier is None else PricingTier(parsed.tier),
                        host_hint=found.host,
                    )
                links, gaps, failure = None, None, None
                try:
                    links, gaps = refresh_history(
                        store,
                        BeadsProcess(BeadsConnection.read(context.beads), timeout=2),
                        time.monotonic() + 2,
                    )
                except (HiveError, OSError) as error:
                    failure = str(error)
                # Refreshing the link cache is incidental to a report; contention
                # leaves the cached associations in place, reported as stale.
                try:
                    registry.refresh(links, gaps, failure)
                except HiveError as error:
                    if error.code != ErrorCode.BUSY:
                        raise
                    failure = failure or error.detail
                result["association_stale"] = (
                    failure is not None
                    or not registry.status()["bead_events_caught_up"]
                )
                result["associated_beads"] = registry.associations(
                    CodexTaskId(parsed.task)
                )
                result["association_gaps"] = registry.gaps()
                result["usage_collectable"] = (
                    found.host is not None and found.path is not None
                )
                if found.error:
                    result["collection_gap"] = found.error
        elif parsed.action == "reset-bead-events":
            from hive.bead_intervals import reset

            result = reset(UsageStore(context.state / "telemetry.sqlite3"))
        elif parsed.action == "otlp-config":
            from hive.otlp_storage import config

            result = config(
                context.state,
                parsed.port,
                None if parsed.secret_file is None else Path(parsed.secret_file),
            )
        elif parsed.action == "collect":
            result = UsageStore(context.state / "telemetry.sqlite3").collect(
                CodexTaskId(parsed.task),
                Path(parsed.transcript),
                budget=parsed.max_bytes,
                from_start=parsed.from_start,
            )
        elif parsed.action == "usage":
            result = UsageStore(context.state / "telemetry.sqlite3").report(
                CodexTaskId(parsed.task)
            )
        elif parsed.action == "status":
            result = CollectionRegistry(
                UsageStore(context.state / "telemetry.sqlite3")
            ).status()
        elif parsed.action == "links":
            store = UsageStore(context.state / "telemetry.sqlite3")
            links, gaps = refresh_history(
                store,
                BeadsProcess(BeadsConnection.read(context.beads), timeout=2),
                time.monotonic() + 2,
            )
            registry = CollectionRegistry(store)
            registry.refresh(links, gaps, None)
            tasks = tuple(sorted({link.task for link in links}))
            linked: list[dict[str, object]] = []
            for start in range(0, len(tasks), 64):
                discoveries, _ = probe(
                    tasks[start : start + 64],
                    Path(parsed.native_index),
                    context.claude_projects,
                    registry,
                )
                for link in links:
                    if link.task in discoveries:
                        found = discoveries[link.task]
                        linked.append(
                            {
                                **link.__dict__,
                                "collected": found.host is not None
                                and found.path is not None,
                                "host": found.host,
                                "collection_gap": found.error,
                            }
                        )
            result = {"code": "BeadThreads", "links": linked, "gaps": gaps}
        elif parsed.action == "sweep":
            result = sweep(context, Path(parsed.native_index), parsed.batch_size)
        else:
            from hive.collection_transport import run

            result = run(
                context.state,
                Path(parsed.native_index),
                parsed.batch_size,
                parsed.interval_seconds,
                otlp_port=parsed.otlp_port,
            )
        if parsed.group != "telemetry" or parsed.action != "watch":
            print(
                json.dumps(result, ensure_ascii=False)
                if structured
                else json.dumps(result, ensure_ascii=False, indent=2)
            )
        return 0
    except (HiveError, OSError, ValueError) as error:
        code = (
            error.code
            if isinstance(error, HiveError)
            else ErrorCode.PROVIDER_UNAVAILABLE
        )
        result = {"code": code, "detail": str(error), "uncertain": False}
        print(
            json.dumps(result) if "--json" in sys.argv[1:] else f"{code}: {error}",
            file=sys.stderr,
        )
        return 1
