"""The source-selected native launcher cannot choose another store."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from server_fixture import private_server
from test_source_selection import commit, fixture

from hive.beads_connection import BeadsConnection

ROOT: Path = Path(__file__).resolve().parents[1]


class RoutingTests(unittest.TestCase):
    def test_launcher_rejects_routing_override_but_not_description_text(self) -> None:
        with (
            private_server() as (connection, _),
            tempfile.TemporaryDirectory() as temporary,
        ):
            working_dir: Path = Path(temporary)
            environment: dict[str, str] = {}
            repository, environment = fixture(working_dir)
            shutil.copytree(
                ROOT / "src/hive", repository / "src/hive", dirs_exist_ok=True
            )
            commit(repository, "feat: native routing")
            config = Path(environment["HIVE_BOOTSTRAP_CONFIG"])
            settings = json.loads(config.read_text())
            settings["beads"] = str(connection.directory)
            config.write_text(json.dumps(settings))
            environment.update(
                BEADS_DIR="/missing/unrelated", BEADS_DOLT_AUTO_START="1"
            )

            def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "scripts/hive.py"), "bd", *arguments],
                    env=environment,
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )

            created = invoke(
                "create",
                "Routed",
                "--description",
                "--db",
                "--json",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            bead = json.loads(created.stdout)
            self.assertTrue(bead["id"].startswith("hv-"))
            for override in (
                ("--global",),
                ("--db=elsewhere",),
                ("-C", "/tmp"),
                ("--directory=/tmp",),
                ("--repo", "other"),
                ("--sandbox=false",),
                ("--dolt-auto-commit", "on"),
            ):
                result = invoke(*override, "list", "--json")
                self.assertNotEqual(result.returncode, 0, override)
                self.assertIn("Native routing override refused", result.stderr)
            listed = invoke("list", "--all", "--json")
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(
                bead["id"], [item["id"] for item in json.loads(listed.stdout)]
            )
            metadata = connection.directory / ".beads/metadata.json"
            metadata.write_text("{}")
            unavailable = invoke("list", "--json")
            self.assertNotEqual(unavailable.returncode, 0)
            self.assertEqual(json.loads(unavailable.stderr)["code"], "InvalidInput")

    def test_environment_sanitizes_conflicting_native_routing(self) -> None:
        with private_server() as (connection, _):
            original = os.environ.get("BEADS_DIR")
            try:
                os.environ["BEADS_DIR"] = "/tmp/wrong-beads"
                routing = BeadsConnection.read(connection.directory).environment()
                self.assertEqual(
                    routing["BEADS_DIR"], str(connection.directory / ".beads")
                )
                self.assertEqual(routing["BEADS_DOLT_AUTO_START"], "0")
            finally:
                if original is None:
                    del os.environ["BEADS_DIR"]
                else:
                    os.environ["BEADS_DIR"] = original
