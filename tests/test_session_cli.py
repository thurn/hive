"""Enrollment, title drift, and inline filing through real Beads/CLI boundaries."""

import unittest
from concurrent.futures import ThreadPoolExecutor

from cli_fixture import SCOPE, WORKER, cli_fixture
from server_fixture import private_server

from hive.jsonvalue import record, sequence, string
from hive.session import Role


class SessionCliTests(unittest.TestCase):
    def test_named_roles_use_the_required_ui_symbols(self) -> None:
        self.assertEqual(
            {role.value: role.emoji for role in Role},
            {
                "executor": "⚒️",
                "warden": "🛡️",
                "weaver": "🧵",
                "sage": "📖",
                "justiciar": "🔥",
                "vizier": "🔮",
                "archivist": "📁",
                "bead": "📿",
            },
        )

    def test_parallel_enrollment_and_nonblocking_title_drift(self) -> None:
        with private_server() as (connection, _), cli_fixture(connection) as cli:
            cli.call("config", "capacity", "--global-limit", "1")
            bead = cli.add("Search indexing")
            entry = (
                "session",
                "enter",
                "--task",
                "task-1",
                *SCOPE,
                "--role",
                "executor",
                "--bead",
                bead,
                "--subject",
                "Search indexing",
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                entered = list(pool.map(lambda _: cli.call(*entry), range(2)))
            first = record(entered[0]["session"])
            self.assertEqual(first["id"], record(entered[1]["session"])["id"])
            self.assertEqual(
                len(sequence(cli.call("session", "list")["sessions"], "sessions")), 1
            )
            status = cli.call("status")
            self.assertEqual(status["global_owned"], 0)
            self.assertEqual(len(sequence(status["tasks"], "tasks")), 1)
            title = string(first["title"], "title")
            self.assertEqual(title, f"⚒️ [{bead}] Search indexing")
            named = ("session", "named", "--task", "task-1")
            cli.call(*named, "--title", title, "--error", "Native naming unavailable")
            self.assertIn("Native naming unavailable", cli.run("status").stdout)
            self.assertTrue(record(cli.call(*entry)["session"])["rename_required"])
            # A failed title cannot block admission or keep ownership after work.
            cli.call("task", "claim", bead, *SCOPE, *WORKER)
            self.assertEqual(cli.call("status")["global_owned"], 1)
            review = cli.call(
                "session",
                "enter",
                "--task",
                "task-1",
                *SCOPE,
                "--role",
                "warden",
                "--bead",
                bead,
                "--subject",
                "Review search architecture",
            )
            review_title = string(record(review["session"])["title"], "title")
            cli.call(*named, "--title", title, "--applied", expected="Busy")
            cli.call(*named, "--title", review_title, "--applied")
            # An older RPC can finish after the newer title was acknowledged.
            cli.call(*named, "--title", title, "--applied", expected="Busy")
            drift = sequence(cli.call("status")["sessions"], "sessions")
            self.assertTrue(record(drift[0])["rename_required"])
            cli.call(*named, "--title", review_title, "--applied")
            inline = cli.call(
                "session",
                "enter",
                "--task",
                "task-1",
                *SCOPE,
                "--role",
                "bead",
                "--subject",
                "File a follow-up",
                "--inline-bead",
            )
            retained = record(inline["session"])
            self.assertEqual(retained["title"], review_title)
            self.assertFalse(retained["rename_required"])
            after = cli.call(*entry, "--stage", "waiting for CI")
            self.assertTrue(record(after["session"])["rename_required"])
            self.assertIn(
                "waiting for CI", string(record(after["session"])["title"], "title")
            )
            cli.call(
                "task",
                "defer",
                bead,
                *SCOPE,
                "--reason",
                "user-pause",
                "--note",
                "Stop",
            )
            cli.call("task", "settle", bead, *SCOPE, *WORKER)
            completed = cli.call(
                "session",
                "enter",
                "--task",
                "task-1",
                *SCOPE,
                "--role",
                "executor",
                "--subject",
                "Search work paused",
            )
            self.assertNotIn(
                bead, string(record(completed["session"])["title"], "title")
            )
            self.assertEqual(cli.call("status")["global_owned"], 0)
            fresh = cli.call(
                "session",
                "enter",
                "--task",
                "new-task",
                *SCOPE,
                "--role",
                "bead",
                "--subject",
                "Record failure",
                "--inline-bead",
            )
            self.assertTrue(
                string(record(fresh["session"])["title"], "title").startswith("📿")
            )
            cli.call(*entry, "--inline-bead", expected="InvalidInput")
            cli.call(
                "session",
                "named",
                "--task",
                "not-enrolled",
                "--title",
                title,
                "--applied",
                expected="NotFound",
            )
