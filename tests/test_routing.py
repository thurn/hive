"""Observation reads the configured server despite unrelated native routing."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from server_fixture import private_server
from test_source_selection import commit, fixture

ROOT: Path = Path(__file__).resolve().parents[1]
THREAD = "01a0d0bb-8916-7380-8a5e-24cacfaaeda1"


class RoutingTests(unittest.TestCase):
    def test_observer_routes_reads_and_reports_unavailable_server(self) -> None:
        with (
            private_server() as (connection, server),
            tempfile.TemporaryDirectory() as temporary,
        ):
            working_dir: Path = Path(temporary)
            environment: dict[str, str] = {}
            repository, environment = fixture(working_dir)
            shutil.copytree(
                ROOT / "src/hive", repository / "src/hive", dirs_exist_ok=True
            )
            commit(repository, "feat: observation routing")
            config = Path(environment["HIVE_BOOTSTRAP_CONFIG"])
            settings = json.loads(config.read_text())
            settings["beads"] = str(connection.directory)
            config.write_text(json.dumps(settings))
            created = subprocess.run(
                [
                    "bd",
                    "-C",
                    str(connection.directory),
                    "--sandbox",
                    "--dolt-auto-commit",
                    "off",
                    "create",
                    "Observed",
                    "--metadata",
                    json.dumps({"hive_origin_thread": THREAD}),
                    "--json",
                ],
                env=connection.environment(),
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )
            bead = json.loads(created.stdout)
            environment.update(
                BEADS_DIR=str(working_dir / ".beads"),
                BEADS_DOLT_SERVER_DATABASE="wrong",
                BEADS_DOLT_SERVER_PORT="1",
                BEADS_DOLT_AUTO_START="1",
                GIT_DIR="/missing/unrelated",
            )

            def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sys.executable, str(ROOT / "scripts/hive.py"), *arguments],
                    env=environment,
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )

            listed = invoke("telemetry", "links", "--json")
            self.assertEqual(listed.returncode, 0, listed.stderr)
            links = json.loads(listed.stdout)["links"]
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]["bead"], bead["id"])
            self.assertEqual(links[0]["task"], THREAD)

            server.terminate()
            server.wait(timeout=5)
            unavailable = invoke("telemetry", "links", "--json")
            self.assertNotEqual(unavailable.returncode, 0)
            self.assertEqual(
                json.loads(unavailable.stderr)["code"], "ProviderUnavailable"
            )
            self.assertFalse((working_dir / ".beads").exists())

            metadata = connection.directory / ".beads/metadata.json"
            metadata.write_text("{}")
            invalid = invoke("telemetry", "links", "--json")
            self.assertNotEqual(invalid.returncode, 0)
            self.assertEqual(json.loads(invalid.stderr)["code"], "InvalidInput")
