"""Observation reads the configured server despite unrelated native routing."""

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from server_fixture import private_server
from test_source_selection import commit, fixture

ROOT: Path = Path(__file__).resolve().parents[1]
THREAD = "01a0d0bb-8916-7380-8a5e-24cacfaaeda1"
CLAUDE = "a521cf51-c055-4715-832b-fc19921ee482"


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
            settings["claude_projects"] = str(working_dir / "claude-projects")
            transcript = working_dir / "native.jsonl"
            transcript.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": THREAD}}) + "\n"
            )
            index = working_dir / "native.sqlite3"
            with sqlite3.connect(index) as db:
                db.execute(
                    "CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)"
                )
                db.execute(
                    "INSERT INTO threads VALUES (?,?)", (THREAD, str(transcript))
                )
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
                    json.dumps({"hive_origin_thread": CLAUDE}),
                    "--json",
                ],
                env=connection.environment(),
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )
            bead = json.loads(created.stdout)
            subprocess.run(
                [
                    "bd",
                    "-C",
                    str(connection.directory),
                    "--sandbox",
                    "--dolt-auto-commit",
                    "off",
                    "--actor",
                    THREAD,
                    "update",
                    bead["id"],
                    "--claim",
                ],
                env=connection.environment(),
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )
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

            listed = invoke(
                "telemetry", "links", "--native-index", str(index), "--json"
            )
            self.assertEqual(listed.returncode, 0, listed.stderr)
            links = json.loads(listed.stdout)["links"]
            self.assertEqual(
                sorted((link["task"], link["collected"]) for link in links),
                [(THREAD, True), (CLAUDE, False)],
            )
            self.assertEqual({link["bead"] for link in links}, {bead["id"]})

            for task, collected in ((THREAD, True), (CLAUDE, False)):
                priced = invoke(
                    "cost", "--task", task, "--native-index", str(index), "--json"
                )
                self.assertEqual(priced.returncode, 0, priced.stderr)
                cost = json.loads(priced.stdout)
                self.assertEqual(cost["usage_collectable"], collected)
                self.assertEqual("collection_gap" in cost, not collected, cost)
                self.assertEqual(len(cost["associated_beads"]), 1)

            server.terminate()
            server.wait(timeout=5)
            unavailable = invoke(
                "telemetry", "links", "--native-index", str(index), "--json"
            )
            self.assertNotEqual(unavailable.returncode, 0)
            self.assertEqual(
                json.loads(unavailable.stderr)["code"], "ProviderUnavailable"
            )
            self.assertFalse((working_dir / ".beads").exists())

            metadata = connection.directory / ".beads/metadata.json"
            metadata.write_text("{}")
            invalid = invoke(
                "telemetry", "links", "--native-index", str(index), "--json"
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertEqual(json.loads(invalid.stderr)["code"], "InvalidInput")
