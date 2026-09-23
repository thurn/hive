"""Worker-facing CLI journeys use a real server and independent command processes."""

import json
import unittest

from cli_fixture import SCOPE, WORKER, cli_fixture
from server_fixture import private_server
from test_bead_json import native

from hive.beads_process import BeadsProcess
from hive.beads_store import BeadsStore
from hive.configuration_store import ConfigurationStore
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import record, sequence
from hive.locking import Guards
from hive.model import Capacity, Queued
from hive.task_status import decode_status


class TaskCliTests(unittest.TestCase):
    def test_corrupt_boundary_records_do_not_hide_healthy_work(self) -> None:
        healthy = native(Queued())
        corrupt = {**healthy, "id": "hv-bad", "metadata": []}
        status = decode_status((healthy, corrupt), None)
        self.assertEqual([bead.id for bead in status.tasks], ["hv-j3h"])
        self.assertEqual(
            {problem.identifier for problem in status.problems},
            {"configuration", "hv-bad"},
        )
        self.assertIsNone(status.configuration)
        self.assertIsNone(status.owned_count)

    def test_artifact_delivery_unblocks_next_claim_and_preserves_user_pauses(
        self,
    ) -> None:
        with private_server() as (connection, _), cli_fixture(connection) as cli:
            cli.call("config", "capacity", "--global-limit", "1")
            first = cli.add("Research")
            second = cli.add("Apply findings", "--depends-on", first)
            cli.call(
                "task", "claim", second, *SCOPE, *WORKER, expected="DependencyBlocked"
            )
            ready = cli.call("task", "ready", *SCOPE)
            self.assertEqual(
                [record(t)["id"] for t in sequence(ready["tasks"], "tasks")], [first]
            )
            claimed = cli.call("task", "next", *SCOPE, *WORKER, expected="Claimed")
            self.assertEqual(record(claimed["task"])["id"], first)
            cli.add("Allowed backlog filing")
            cli.call(
                "task",
                "next",
                *SCOPE,
                "--owner",
                "other",
                "--turn",
                "other-turn",
                expected="CapacityFull",
            )
            cli.call(
                "task",
                "defer",
                first,
                *SCOPE,
                *WORKER,
                "--reason",
                "user-pause",
                "--note",
                "Explicit stop",
            )
            self.assertEqual(cli.call("status")["global_owned"], 1)
            cli.call("task", "settle", first, *SCOPE, *WORKER)
            self.assertEqual(cli.call("status")["global_owned"], 0)
            cli.call("task", "resume", first, *SCOPE, expected="Paused")
            cli.call("task", "resume", first, *SCOPE, "--user-authorized")
            cli.call("task", "claim", first, *SCOPE, *WORKER)
            cli.call(
                "task",
                "enter-turn",
                first,
                *SCOPE,
                "--owner",
                "task-1",
                "--previous-turn",
                "turn-1",
                "--turn",
                "turn-2",
            )
            review = json.dumps(
                {"kind": "reviewing-artifact", "location": "/tmp/research.md"}
            )
            cli.call(
                "task",
                "advance",
                first,
                *SCOPE,
                *WORKER,
                "--phase-json",
                review,
                expected="StaleOwner",
            )
            current = ("--owner", "task-1", "--turn", "turn-2")
            before_delayed_stop = cli.call("task", "show", first)
            for stale in (WORKER, (), ("--owner", "another-task", "--turn", "turn-2")):
                cli.call(
                    "task",
                    "defer",
                    first,
                    *SCOPE,
                    *stale,
                    "--reason",
                    "user-pause",
                    "--note",
                    "Delayed stop",
                    expected="StaleOwner",
                )
            for partial in (("--owner", "task-1"), ("--turn", "turn-2")):
                cli.call(
                    "task",
                    "defer",
                    first,
                    *SCOPE,
                    *partial,
                    "--reason",
                    "user-pause",
                    "--note",
                    "Incomplete identity",
                    expected="InvalidInput",
                )
            self.assertEqual(cli.call("task", "show", first), before_delayed_stop)
            cli.call("task", "advance", first, *SCOPE, *current, "--phase-json", review)
            cli.call(
                "task",
                "complete",
                first,
                *SCOPE,
                *current,
                "--summary",
                "Research delivered",
                "--delivery-json",
                json.dumps({"kind": "artifact", "location": "/tmp/research.md"}),
            )
            completed = record(cli.call("task", "show", first)["task"])
            self.assertEqual(record(completed["state"])["status"], "closed")
            cli.call("task", "priority", second, *SCOPE, "--priority", "0")
            next_task = cli.call("task", "next", *SCOPE, *current)
            self.assertEqual(record(next_task["task"])["id"], second)
            output = cli.run("status", *SCOPE)
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertIn(second, output.stdout)
            self.assertIn("In flight: 1 / 1", output.stdout)

    def test_maintenance_corruption_and_provider_failure_remain_visible(self) -> None:
        with private_server() as (connection, server), cli_fixture(connection) as cli:
            cli.call(
                "config",
                "register",
                "--project",
                "alpha",
                "--repository",
                str(cli.state / "alpha"),
                "--invariants",
                str(cli.state / "alpha/invariants.md"),
                "--native-id",
                "alpha-native",
            )
            cli.call(
                "config",
                "capacity",
                "--global-limit",
                "8",
                "--project-limits-json",
                '{"search":2,"alpha":1}',
                expected="Configured",
            )
            paused = cli.add(
                "Await design",
                "--defer-reason",
                "design-approval",
                "--note",
                "Needs approval",
            )
            cli.call("task", "claim", paused, *SCOPE, *WORKER, expected="Paused")
            cli.call("task", "resume", paused, *SCOPE, expected="Paused")
            cli.call(
                "task",
                "defer",
                paused,
                *SCOPE,
                "--reason",
                "user-pause",
                "--note",
                "User stopped",
            )
            visible = cli.run("task", "show", paused)
            self.assertEqual(visible.returncode, 0, visible.stderr)
            self.assertIn("design-approval", visible.stdout)
            self.assertIn("user-pause", visible.stdout)
            cli.call(
                "task", "resume", paused, *SCOPE, "--user-authorized", expected="Paused"
            )
            cli.call(
                "task",
                "resume",
                paused,
                *SCOPE,
                "--reason",
                "user-pause",
                expected="Paused",
            )
            cli.call(
                "task",
                "resume",
                paused,
                *SCOPE,
                "--reason",
                "user-pause",
                "--user-authorized",
            )
            cli.call("task", "claim", paused, *SCOPE, *WORKER, expected="Paused")
            cli.call("task", "resume", paused, *SCOPE, expected="Paused")
            cli.call(
                "task",
                "resume",
                paused,
                *SCOPE,
                "--reason",
                "design-approval",
                "--user-authorized",
            )
            dependent = cli.add("Dependent")
            cli.call("task", "dependency", dependent, *SCOPE, "--prerequisite", paused)
            cli.call("task", "cancel", paused, *SCOPE, "--reason", "Dropped")
            cli.call(
                "task",
                "claim",
                dependent,
                *SCOPE,
                *WORKER,
                expected="DependencyBlocked",
            )
            cli.call(
                "task",
                "dependency",
                dependent,
                *SCOPE,
                "--prerequisite",
                paused,
                "--remove",
            )
            cli.call(
                "task",
                "advance",
                dependent,
                *SCOPE,
                *WORKER,
                "--phase-json",
                '{"kind":"impossible"}',
                expected="InvalidInput",
            )
            guards = Guards(cli.state / "locks")
            configuration_before = cli.call("config", "show", expected="Configured")
            guards.stop_mutations("Testing maintenance")
            cli.call("status", expected="Status")
            configuration_during = cli.call("config", "show", expected="Configured")
            self.assertEqual(configuration_during, configuration_before)
            settings = record(configuration_during["hive_config"])
            self.assertEqual(settings["global_limit"], 8)
            self.assertEqual(settings["project_limits"], {"search": 2, "alpha": 1})
            search = next(
                record(project)
                for project in sequence(settings["projects"], "projects")
                if record(project)["id"] == "search"
            )
            self.assertEqual(
                search,
                {
                    "id": "search",
                    "repository": str(cli.state.parent / "project"),
                    "invariants": str(cli.state.parent / "project/invariants.md"),
                    "native_id": "native-search",
                },
            )
            shown = cli.run("config", "show")
            self.assertEqual(shown.returncode, 0, shown.stderr)
            for field in ("repository", "invariants", "native_id"):
                self.assertIn(str(search[field]), shown.stdout)
            cli.call("task", "claim", dependent, *SCOPE, *WORKER, expected="Paused")
            with guards.maintenance():
                pass
            store = BeadsStore(BeadsProcess(connection, "fixture"))
            config = ConfigurationStore(store, guards)
            old = config.read()
            config.replace(old.projects, Capacity(2))
            with self.assertRaises(HiveError) as caught:
                config.replace(old.projects, Capacity(3), observed=old)
            self.assertEqual(caught.exception.code, ErrorCode.BUSY)
            self.assertEqual(config.read().capacity.global_limit, 2)
            store.process.run(
                ["update", dependent, "--metadata", '{"hive_config":{}}'], mutation=True
            )
            mixed = cli.call("status", expected="Status")
            self.assertIn(
                dependent,
                [record(task)["id"] for task in sequence(mixed["tasks"], "tasks")],
            )
            self.assertIn(
                dependent,
                [
                    record(problem)["id"]
                    for problem in sequence(mixed["problems"], "problems")
                ],
            )
            self.assertIsNone(mixed["global_owned"])
            store.process.run(
                ["update", dependent, "--status", "closed", "--assignee", "ghost"],
                mutation=True,
            )
            status = cli.call("status", expected="Status")
            self.assertIsNone(status["global_owned"])
            self.assertEqual(
                record(sequence(status["problems"], "problems")[0])["id"], dependent
            )
            cli.call("task", "next", *SCOPE, *WORKER, expected="InvalidRecord")
            server.terminate()
            server.wait(timeout=5)
            cli.call("status", expected="ProviderUnavailable")
