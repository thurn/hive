"""Retained capacity survives pending delivery and races during observation."""

import json
import os
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path

from cli_fixture import ROOT, SCOPE, WORKER, Cli, cli_fixture
from server_fixture import private_server
from test_source_selection import commit
from tollgate_fixture import CANDIDATE, ProviderFixture, provider_fixture, reply, status

from hive.jsonvalue import parse, record, string


def waiting_bead(cli: Cli, provider: ProviderFixture) -> str:
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
    bead = string(
        record(
            cli.call(
                "task",
                "add",
                *SCOPE,
                "--title",
                "Deliver",
                "--description",
                "Fix",
                "--acceptance",
                "Fixed",
            )["task"]
        )["id"],
        "bead",
    )
    cli.call("task", "claim", bead, *SCOPE, *WORKER)
    phase: dict[str, object] = {
        "kind": "implementing",
        "workspace": str(provider.workspace),
    }
    for fields in (
        {},
        {"kind": "reviewing", "source": provider.source},
        {"kind": "waiting-for-delivery", "candidate": CANDIDATE},
    ):
        phase.update(fields)
        cli.call(
            "task", "advance", bead, *SCOPE, *WORKER, "--phase-json", json.dumps(phase)
        )
    return bead


class DeliverySettlementTests(unittest.TestCase):
    def test_pending_delivery_and_draining_attempt_keep_capacity_and_pause(
        self,
    ) -> None:
        with (
            private_server() as (connection, _),
            cli_fixture(connection) as base,
            provider_fixture() as provider,
        ):
            cli = replace(
                base,
                environment={
                    **base.environment,
                    "PATH": str(provider.root) + os.pathsep + os.environ["PATH"],
                },
            )
            bead = waiting_bead(cli, provider)
            provider.configure(
                locks=str(cli.state / "locks"),
                status=reply(status(provider.source, "running")),
            )
            cli.call(
                "task",
                "advance",
                bead,
                *SCOPE,
                *WORKER,
                "--phase-json",
                json.dumps(
                    {"kind": "implementing", "workspace": str(provider.workspace)}
                ),
                expected="RecoveryRequired",
            )
            cli.call(
                "task",
                "complete",
                bead,
                *SCOPE,
                *WORKER,
                "--summary",
                "Premature",
                "--delivery-json",
                json.dumps(
                    {"kind": "code", "source": provider.source, "candidate": CANDIDATE}
                ),
                expected="RecoveryRequired",
            )
            # The same conversation reattaches without freeing or adding a slot.
            current = ("--owner", "task-1", "--turn", "turn-2")
            cli.call(
                "task",
                "enter-turn",
                bead,
                *SCOPE,
                *current,
                "--previous-turn",
                "turn-1",
            )
            cli.call(
                "task",
                "defer",
                bead,
                *SCOPE,
                *current,
                "--reason",
                "user-pause",
                "--note",
                "Stop",
            )
            cli.call("task", "settle", bead, *SCOPE, *WORKER, expected="StaleOwner")
            cli.call(
                "task", "settle", bead, *SCOPE, *current, expected="RecoveryRequired"
            )
            draining = status(provider.source, "canceled")
            draining["attempts"] = [{"state": "running"}]
            provider.configure(locks=str(cli.state / "locks"), status=reply(draining))
            cli.call(
                "task", "settle", bead, *SCOPE, *current, expected="RecoveryRequired"
            )
            self.assertEqual(cli.call("status")["global_owned"], 1)
            provider.configure(
                locks=str(cli.state / "locks"),
                status=reply(status(provider.source, "externally-integrated")),
            )
            settled = record(cli.call("task", "settle", bead, *SCOPE, *current)["task"])
            self.assertIn("user-pause", json.dumps(settled))
            self.assertIn(CANDIDATE, json.dumps(settled))
            self.assertEqual(cli.call("status")["global_owned"], 0)
            cli.call("task", "claim", bead, *SCOPE, *current, expected="Paused")
            cli.call("task", "resume", bead, *SCOPE, "--user-authorized")
            cli.call("task", "claim", bead, *SCOPE, *current)
            cli.call(
                "task",
                "advance",
                bead,
                *SCOPE,
                *current,
                "--phase-json",
                json.dumps(
                    {"kind": "implementing", "workspace": str(provider.workspace)}
                ),
                expected="RecoveryRequired",
            )
            cli.call(
                "task",
                "complete",
                bead,
                *SCOPE,
                *current,
                "--summary",
                "External integration",
                "--delivery-json",
                json.dumps(
                    {"kind": "code", "source": provider.source, "candidate": CANDIDATE}
                ),
                expected="RecoveryRequired",
            )

    def test_provider_observation_cannot_overwrite_a_pause_or_outlive_source(
        self,
    ) -> None:
        with (
            private_server() as (connection, _),
            cli_fixture(connection) as base,
            provider_fixture() as provider,
        ):
            cli = replace(
                base,
                environment={
                    **base.environment,
                    "PATH": str(provider.root) + os.pathsep + os.environ["PATH"],
                },
            )
            bead = waiting_bead(cli, provider)
            arguments = [
                "task",
                "advance",
                bead,
                *SCOPE,
                *WORKER,
                "--phase-json",
                json.dumps(
                    {"kind": "implementing", "workspace": str(provider.workspace)}
                ),
                "--json",
            ]
            for race in ("source", "pause"):
                entered = provider.root / f"{race}-entered"
                release = provider.root / f"{race}-release"
                provider.configure(
                    locks=str(cli.state / "locks"),
                    status={
                        **reply(status(provider.source, "failed")),
                        "entered": str(entered),
                        "release": str(release),
                    },
                )
                running = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-S",
                        str(ROOT / "scripts/hive.py"),
                        *arguments,
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
                        and running.poll() is None
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.02)
                    self.assertTrue(entered.exists())
                    if race == "pause":
                        cli.call(
                            "task",
                            "defer",
                            bead,
                            *SCOPE,
                            *WORKER,
                            "--reason",
                            "user-pause",
                            "--note",
                            "Stop during inspection",
                        )
                    else:
                        settings = record(
                            parse(
                                Path(
                                    cli.environment["HIVE_BOOTSTRAP_CONFIG"]
                                ).read_text()
                            )
                        )
                        source = Path(string(settings["repository"], "source"))
                        (source / "changed.txt").write_text("New source\n")
                        commit(source, "docs: change during provider inspection")
                    release.touch()
                    stdout, stderr = running.communicate(timeout=10)
                    self.assertEqual(running.returncode, 1, stdout + stderr)
                    self.assertEqual(
                        record(parse(stderr))["code"],
                        "StaleOwner" if race == "pause" else "RecoveryRequired",
                    )
                    self.assertEqual(cli.call("status")["global_owned"], 1)
                    self.assertIn(
                        "waiting-for-delivery",
                        json.dumps(cli.call("task", "show", bead)),
                    )
                finally:
                    release.touch()
                    if running.poll() is None:
                        running.kill()
                    running.communicate(timeout=5)
