"""A real server/CLI journey with a subprocess provider protocol fixture."""

import json
import os
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path

from cli_fixture import ROOT, SCOPE, WORKER, cli_fixture
from server_fixture import private_server
from test_source_selection import commit
from tollgate_fixture import CANDIDATE, oid, provider_fixture, reply, status, sync_event

from hive.jsonvalue import parse, record, string
from hive.locking import Guards


class DeliveryCliTests(unittest.TestCase):
    def test_owned_delivery_releases_locks_and_survives_source_update(self) -> None:
        with (
            private_server() as (connection, _),
            cli_fixture(connection) as original,
            provider_fixture() as provider,
        ):
            cli = replace(
                original,
                environment={
                    **original.environment,
                    "PATH": str(provider.root) + os.pathsep + os.environ["PATH"],
                },
            )
            cli.call(
                "config",
                "register",
                *SCOPE,
                "--repository",
                str(provider.repository),
                "--invariants",
                str(provider.repository / "invariants.md"),
                "--native-id",
                "native-search",
            )
            first = string(
                record(
                    cli.call(
                        "task",
                        "add",
                        *SCOPE,
                        "--title",
                        "Code change",
                        "--description",
                        "Repair behavior",
                        "--acceptance",
                        "Behavior works",
                    )["task"]
                )["id"],
                "bead ID",
            )
            second = cli.add("Follow-on", "--depends-on", first)
            claimed = record(cli.call("task", "claim", first, *SCOPE, *WORKER)["task"])
            # Use the retained preparation branch, not a separately generated name.
            preparation = record(record(claimed["state"])["phase"])
            retained_branch = string(preparation["branch"], "branch")
            provider.configure(
                locks=str(cli.state / "locks"),
                worktree=reply(
                    {
                        "action": "created",
                        "path": str(provider.workspace),
                        "branch": retained_branch,
                        "new_oid": oid(provider.source),
                    }
                ),
            )
            cli.call(
                "workspace",
                "create",
                first,
                *SCOPE,
                "--owner",
                "stale",
                "--turn",
                "stale",
                expected="StaleOwner",
            )
            self.assertEqual(provider.calls(), [])
            cli.call(
                "workspace",
                "create",
                first,
                *SCOPE,
                *WORKER,
                expected="WorkspaceCreated",
            )
            phase: dict[str, object] = {
                "kind": "implementing",
                "workspace": str(provider.workspace),
            }
            cli.call(
                "task",
                "advance",
                first,
                *SCOPE,
                *WORKER,
                "--phase-json",
                json.dumps(phase),
            )
            phase = {**phase, "kind": "reviewing", "source": provider.source}
            cli.call(
                "task",
                "advance",
                first,
                *SCOPE,
                *WORKER,
                "--phase-json",
                json.dumps(phase),
            )
            provider.configure(
                locks=str(cli.state / "locks"),
                candidate=reply(
                    {"item_id": CANDIDATE, "source_oid": oid(provider.source)}
                ),
            )
            cli.call("delivery", "submit", first, *SCOPE, *WORKER, expected="Submitted")
            phase = {**phase, "kind": "waiting-for-delivery", "candidate": CANDIDATE}
            cli.call(
                "task",
                "advance",
                first,
                *SCOPE,
                *WORKER,
                "--phase-json",
                json.dumps(phase),
            )
            provider.configure(
                locks=str(cli.state / "locks"),
                status=reply(status(provider.source, "ready", authorized=False)),
                approve=reply(
                    {
                        "item_id": CANDIDATE,
                        "source_oid": oid(provider.source),
                        "already_authorized": False,
                        "authorized_item_ids": [CANDIDATE],
                    }
                ),
            )
            cli.call(
                "delivery", "approve", first, *SCOPE, *WORKER, expected="Authorized"
            )
            entered = provider.root / "waiting"
            provider.configure(
                locks=str(cli.state / "locks"),
                wait={
                    **reply(status(provider.source)),
                    "delay": 3,
                    "entered": str(entered),
                },
                history=reply([sync_event(1)]),
            )
            waiting = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "scripts/hive.py"),
                    "delivery",
                    "wait",
                    CANDIDATE,
                    *SCOPE,
                    "--json",
                ],
                env=cli.environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while (
                    not entered.exists()
                    and waiting.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
                self.assertTrue(entered.exists())
                self.assertEqual(cli.call("status")["global_owned"], 1)
                guards = Guards(cli.state / "locks")
                with guards.admission():
                    pass
                guards.stop_mutations("A waiting provider must not hold maintenance")
                with guards.maintenance():
                    pass
                settings = record(
                    parse(Path(cli.environment["HIVE_BOOTSTRAP_CONFIG"]).read_text())
                )
                source = Path(string(settings["repository"], "source repository"))
                (source / "new-source.txt").write_text(
                    "New source during pending delivery\n"
                )
                changed = commit(source, "docs: source update during delivery wait")
                selected = cli.call("source")
                self.assertIn(changed, json.dumps(selected))
                stdout, stderr = waiting.communicate(timeout=10)
                self.assertEqual(waiting.returncode, 0, stderr)
                self.assertEqual(record(parse(stdout))["code"], "Delivered")
            finally:
                if waiting.poll() is None:
                    waiting.kill()
                    waiting.communicate()
            cli.call(
                "task",
                "complete",
                first,
                *SCOPE,
                *WORKER,
                "--summary",
                "Delivered",
                "--delivery-json",
                json.dumps(
                    {"kind": "code", "source": provider.source, "candidate": CANDIDATE}
                ),
            )
            self.assertEqual(
                record(cli.call("task", "next", *SCOPE, *WORKER)["task"])["id"], second
            )
