"""One source-selected invocation, one truly read-only SQLite snapshot."""

from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from hive.bead_queries import BEAD
from hive.dashboard_values import Filters
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import integer
from hive.launch_context import LaunchContext
from hive.telemetry_schema import VERSION
from hive.usage_store import row


def add_parser[ParserType: argparse.ArgumentParser](
    groups: argparse._SubParsersAction[ParserType],
) -> None:
    dashboard = groups.add_parser("dashboard")
    operations = dashboard.add_subparsers(dest="action", required=True)
    server = operations.add_parser("serve")
    server.add_argument("--port", type=int, default=4320)
    build = operations.add_parser("build")
    build.add_argument("commit")
    build.add_argument("--retry", action="store_true")
    operations.add_parser("page")
    api = operations.add_parser("api")
    routes = api.add_subparsers(dest="route", required=True)
    feed = routes.add_parser("feed")
    feed.add_argument("--window", choices=("today", "7d", "30d"), default="7d")
    for name in ("project", "role", "state", "q", "cursor"):
        feed.add_argument("--" + name)
    feed.add_argument("--active", action="store_true")
    feed.add_argument("--older-completed", action="store_true")
    bead = routes.add_parser("bead")
    bead.add_argument("id")
    bead.add_argument("--requests", action="store_true")
    bead.add_argument("--cursor")
    bead.add_argument("--sort", choices=("time", "share"), default="time")
    session = routes.add_parser("session")
    session.add_argument("id")
    session.add_argument("--tail", action="store_true")
    ledger = routes.add_parser("ledger")
    ledger.add_argument("kind", choices=("small_tails", "unattributable"))
    ledger.add_argument("project")
    excerpt = routes.add_parser("excerpt")
    excerpt.add_argument("--thread", required=True)
    excerpt.add_argument("--agent")
    choice = excerpt.add_mutually_exclusive_group(required=True)
    choice.add_argument("--call")
    choice.add_argument("--event")
    log = routes.add_parser("ci-log")
    log.add_argument("--candidate", required=True)
    log.add_argument("--step", required=True)
    routes.add_parser("status")


def tree(context: LaunchContext) -> str | None:
    result = subprocess.run(
        ("git", "rev-parse", f"{context.commit}:dashboard"),
        cwd=context.repository,
        capture_output=True,
        timeout=2,
    )
    text = result.stdout.decode().strip()
    return (
        text
        if result.returncode == 0
        and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", text)
        else None
    )


def read(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        version = integer(
            row(connection.execute("PRAGMA user_version").fetchone(), 1)[0], "schema"
        )
        if version != VERSION:
            connection.close()
            raise HiveError(
                ErrorCode.RECOVERY_REQUIRED,
                "Observation store not yet migrated; run the collector",
            )
        return connection
    except sqlite3.Error as error:
        raise HiveError(
            ErrorCode.RECOVERY_REQUIRED,
            "Observation store not yet migrated; run the collector",
        ) from error


def dispatch(context: LaunchContext, args: argparse.Namespace) -> dict[str, object]:
    if args.action == "serve":
        from hive.dashboard_transport import run

        return run(context.state, context.repository / "scripts/hive.py", args.port)
    if args.action in {"build", "page"}:
        from hive.dashboard_build import build
        from hive.dashboard_build import page as document

        return (
            build(context, args.commit, retry=args.retry)
            if args.action == "build"
            else document(context)
        )
    from hive.dashboard_detail import detail, page
    from hive.dashboard_feed import feed, freshness
    from hive.dashboard_panel import panel

    envelope: dict[str, object] = dict[str, object](
        schema=1,
        source_commit=context.commit,
        ui_tree=tree(context),
        generated_at=datetime.now(UTC).isoformat(),
    )
    try:
        connection = read(context.state / "telemetry.sqlite3")
        try:
            if args.route == "feed":
                value = feed(
                    connection,
                    Filters(
                        args.window,
                        args.project,
                        args.role,
                        args.state,
                        args.q or "",
                        args.active,
                        args.older_completed,
                        args.cursor,
                    ),
                )
            elif args.route == "bead":
                if BEAD.fullmatch(args.id) is None:
                    raise HiveError(ErrorCode.INVALID_INPUT, "Invalid bead ID")
                value = (
                    page(connection, "bead:" + args.id, args.cursor, sort=args.sort)
                    if args.requests
                    else detail(connection, "bead:" + args.id)
                )
                if not args.requests:
                    value["beads"] = panel(connection, context, args.id)
            elif args.route == "session":
                value = detail(
                    connection, "session:" + args.id, whole_session=not args.tail
                )
                if args.tail and value["card"]:
                    from hive.jsonvalue import record

                    if record(value["card"])["kind"] != "tail":
                        raise HiveError(
                            ErrorCode.NOT_FOUND, "Session has no visible tail"
                        )
            elif args.route == "ledger":
                value = detail(connection, f"ledger:{args.kind}:{args.project}")
            elif args.route == "excerpt":
                from hive.dashboard_excerpts import excerpt

                value = excerpt(
                    connection, args.thread, args.call, args.event, args.agent
                )
            elif args.route == "ci-log":
                from hive.dashboard_excerpts import ci_log

                value = ci_log(connection, context, args.candidate, args.step)
            else:
                value = freshness(connection)
        finally:
            connection.close()
        return {**envelope, "code": "Dashboard", **value}
    except (HiveError, sqlite3.Error, OSError, ValueError) as error:
        return {
            **envelope,
            "code": (
                error.code
                if isinstance(error, HiveError)
                else ErrorCode.PROVIDER_UNAVAILABLE
            ),
            "detail": str(error),
        }
