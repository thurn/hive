"""Source-selected diagnostics and derived observation."""

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from hive.beads_connection import BeadsConnection
from hive.beads_process import BeadsProcess
from hive.collection import sweep
from hive.collection_registry import CollectionRegistry
from hive.cost_report import report
from hive.errors import ErrorCode, HiveError
from hive.identity import CodexTaskId, PricingTier
from hive.launch_context import LaunchContext
from hive.thread_links import codex_thread, read
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
        cost = groups.add_parser("cost")
        cost.add_argument("--task", required=True)
        cost.add_argument(
            "--tier", choices=[tier.value for tier in PricingTier], default="standard"
        )
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
        actions.add_parser("links")
        for name in ("sweep", "watch"):
            operation = actions.add_parser(name)
            operation.add_argument("--native-index", required=True)
            operation.add_argument(
                "--batch-size", type=int, choices=range(1, 65), default=32
            )
            if name == "watch":
                operation.add_argument("--interval-seconds", type=int, default=5)
        structured = "--json" in arguments
        parsed = parser.parse_args([arg for arg in arguments if arg != "--json"])
        context.release()
        if parsed.group == "source":
            result: dict[str, object] = {
                "code": "SourceSelected",
                "commit": context.commit,
                "directory": str(context.source),
            }
        elif parsed.group == "cost":
            store = UsageStore(context.state / "telemetry.sqlite3")
            result = report(store, CodexTaskId(parsed.task), PricingTier(parsed.tier))
            registry = CollectionRegistry(store)
            try:
                links, gaps = read(
                    BeadsProcess(BeadsConnection.read(context.beads), timeout=2)
                )
                registry.refresh(links, gaps, None)
                result["association_stale"] = False
            except (HiveError, OSError) as error:
                registry.refresh(None, None, str(error))
                result["association_stale"] = True
            result["associated_beads"] = registry.associations(CodexTaskId(parsed.task))
            result["association_gaps"] = registry.gaps()
            result["usage_collectable"] = codex_thread(parsed.task)
            if not result["usage_collectable"]:
                result["collection_gap"] = (
                    "Not a Codex thread ID; usage and cost are not collected"
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
            links, gaps = read(
                BeadsProcess(BeadsConnection.read(context.beads), timeout=2)
            )
            result = {
                "code": "BeadThreads",
                "links": [link.__dict__ for link in links],
                "gaps": gaps,
            }
        elif parsed.action == "sweep":
            result = sweep(context, Path(parsed.native_index), parsed.batch_size)
        else:
            from hive.collection_transport import run

            result = run(
                context.state,
                Path(parsed.native_index),
                parsed.batch_size,
                parsed.interval_seconds,
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
