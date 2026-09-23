"""Enrolled discovery and a real resident across source changes and outages."""

import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path

from cli_fixture import ROOT, SCOPE, Cli, cli_fixture
from mcp_fixture import assert_stopped, await_file
from server_fixture import private_server
from test_source_selection import commit
from test_usage import TASK, counters, header, response

from hive.jsonvalue import parse, record, string
from hive.locking import file_lock


def enroll(cli: Cli, task: str) -> None:
    cli.call(
        "session",
        "enter",
        *SCOPE,
        "--task",
        task,
        "--role",
        "executor",
        "--subject",
        "Collect usage",
    )


def native_index(path: Path, task: str, transcript: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO threads VALUES (?, ?)", (task, str(transcript))
        )


def next_event(path: Path, offset: int = 0) -> tuple[dict[str, object], int]:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        with path.open("rb") as stream:
            stream.seek(offset)
            value = stream.readline()
            if value.endswith(b"\n"):
                return record(parse(value.decode())), stream.tell()
        time.sleep(0.02)
    raise AssertionError("Collector produced no event")


class CollectionTests(unittest.TestCase):
    def test_round_robin_archive_discovery_and_cached_outage_collection(self) -> None:
        with private_server() as (connection, _), cli_fixture(connection) as cli:
            root = connection.directory
            index = root / "native.sqlite3"
            path = root / "transcript.jsonl"
            path.write_bytes(header() + response("first", counters()))
            native_index(index, TASK, path)
            native_index(index, "not-enrolled", path)
            for task in ("a-missing", TASK):
                enroll(cli, task)
            sweep = (
                "telemetry",
                "sweep",
                "--native-index",
                str(index),
                "--batch-size",
                "1",
            )
            status = ("telemetry", "status")
            first = cli.call(*sweep)
            self.assertEqual(first["attempted"], 1)
            self.assertEqual(cli.call(*status)["never_attempted"], 1)
            self.assertIn("a-missing", str(cli.call(*status)["recent_failures"]))
            cli.call(*sweep)
            self.assertEqual(cli.call(*status)["enrolled"], 2)
            self.assertEqual(cli.call(*status)["never_attempted"], 0)
            usage = ("telemetry", "usage", "--task", TASK)
            self.assertEqual(cli.call(*usage)["observed_responses"], 1)
            archive = root / "archived.jsonl"
            path.rename(archive)
            with archive.open("ab") as stream:
                stream.write(response("second", counters()))
            native_index(index, TASK, archive)
            cli.call(*sweep)
            cli.call(*sweep)
            self.assertEqual(cli.call(*usage)["observed_responses"], 2)
            # Both discovery providers fail. Cached enrollment and last known
            # paths still permit observation; neither failure becomes silence.
            metadata = root / ".beads/metadata.json"
            saved = metadata.read_text()
            metadata.write_text("{}")
            index.unlink()
            with archive.open("ab") as stream:
                stream.write(response("third", counters()))
            cli.call(*sweep)
            failed = cli.call(*sweep)
            self.assertIsNotNone(failed["registry_error"])
            self.assertIsNotNone(failed["native_index_error"])
            self.assertIsNotNone(cli.call(*status)["registry_error"])
            self.assertEqual(cli.call(*usage)["observed_responses"], 3)
            self.assertFalse(
                index.exists(), "Read-only lookup created a native database"
            )
            # Restore providers. A wrong native identity is rejected, not used
            # as evidence of zero usage or activity for this task.
            metadata.write_text(saved)
            impostor = root / "wrong.jsonl"
            impostor.write_bytes(header("someone-else"))
            native_index(index, TASK, impostor)
            cli.call(*sweep)
            cli.call(*sweep)
            self.assertIsNotNone(cli.call(*usage)["source_error"])
            self.assertIsNone(cli.call(*status)["registry_error"])
            self.assertEqual(cli.call(*usage)["observed_responses"], 3)
            index.unlink()
            with archive.open("ab") as stream:
                stream.write(response("after-rejected-path", counters()))
            cli.call(*sweep)
            cli.call(*sweep)
            self.assertEqual(cli.call(*usage)["observed_responses"], 4)
            self.assertIsNone(cli.call(*usage)["source_error"])
            native_index(index, TASK, impostor)
            # This observer never claimed a bead or rewrote the native index.
            self.assertEqual(cli.call("status")["global_owned"], 0)
            with sqlite3.connect(index) as db:
                self.assertEqual(
                    db.execute(
                        "SELECT rollout_path FROM threads WHERE id=?", (TASK,)
                    ).fetchone(),
                    (str(impostor),),
                )
            # A failed observer database does not disable task work.
            (cli.state / "telemetry.sqlite3").write_bytes(b"broken")
            cli.call(*sweep, expected="ProviderUnavailable")
            self.assertEqual(
                cli.call("task", "show", cli.add("Still usable"))["code"], "Task"
            )

    def test_resident_selects_new_master_and_drains_its_child_on_stop(self) -> None:
        with private_server() as (connection, _), cli_fixture(connection) as cli:
            root = connection.directory
            index = root / "native.sqlite3"
            path = root / "transcript.jsonl"
            path.write_bytes(header() + response("first", counters()))
            native_index(index, TASK, path)
            enroll(cli, TASK)
            configuration = record(
                parse(Path(cli.environment["HIVE_BOOTSTRAP_CONFIG"]).read_text())
            )
            repository = Path(string(configuration["repository"], "source"))
            module = repository / "src/hive/collection.py"
            original = module.read_text()
            args = [
                sys.executable,
                "-I",
                "-S",
                str(ROOT / "scripts/hive.py"),
                "telemetry",
                "watch",
                "--native-index",
                str(index),
                "--interval-seconds",
                "1",
                "--json",
            ]
            log = root / "collector.jsonl"
            with log.open("wb") as output:
                process = subprocess.Popen(
                    args, env=cli.environment, stdout=output, stderr=output
                )
                try:
                    first, offset = next_event(log)
                    self.assertEqual(first["exit_code"], 0, first)
                    first_batch = record(parse(string(first["output"], "batch")))
                    # Resident ownership excludes a second timer; it does not
                    # retain maintenance or admission while waiting.
                    cli.call(*args[4:-1], expected="Busy")
                    with file_lock(cli.state / "locks/maintenance.lock", timeout=0):
                        with file_lock(cli.state / "locks/admission.lock", timeout=0):
                            self.assertIsNone(process.poll())
                    module.write_text(
                        original.replace(
                            '"code": "CollectionBatch"',
                            '"code": "UpdatedCollectionBatch"',
                        )
                    )
                    changed = commit(repository, "feat: new parser policy")
                    current = first_batch
                    for _ in range(5):
                        event, offset = next_event(log, offset)
                        self.assertEqual(event["exit_code"], 0, event)
                        current = record(parse(string(event["output"], "batch")))
                        if current["source"] == changed:
                            break
                    else:
                        raise AssertionError(
                            "Resident kept old policy after master changed"
                        )
                    self.assertEqual(current["code"], "UpdatedCollectionBatch")
                    self.assertNotEqual(current["source"], first_batch["source"])
                    # A broken next source fails visibly without killing the timer
                    # or silently falling back to its previous application policy.
                    module.write_text("this is invalid Python!\n")
                    commit(repository, "test: broken observer")
                    for _ in range(5):
                        event, offset = next_event(log, offset)
                        if event["exit_code"] != 0:
                            break
                    else:
                        self.fail("Broken source was silently ignored")
                    self.assertIsNone(process.poll())
                    marker = root / "active-child"
                    blocking = original.replace(
                        "    usage = UsageStore(",
                        f'    Path({str(marker)!r}).write_text(str(__import__("os").getpid()))\n'
                        "    time.sleep(60)\n    usage = UsageStore(",
                        1,
                    )
                    module.write_text(blocking)
                    commit(repository, "test: blocked observer")
                    await_file(marker)
                    child = int(marker.read_text())
                    process.terminate()
                    self.assertEqual(process.wait(timeout=5), 0)
                    assert_stopped(child)
                    with file_lock(cli.state / "collection-watch.lock", timeout=0):
                        pass
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
            # A full supervisor pipe must not prevent signal handling. The real
            # CLI must also avoid a blocking final print after transport stops.
            marker.unlink()
            module.write_text(
                "from pathlib import Path\n"
                "def sweep(*arguments):\n"
                f"    Path({str(marker)!r}).write_text('emitted')\n"
                "    return {'code': 'CollectionBatch', 'large': 'x' * 200000}\n"
            )
            commit(repository, "test: full supervisor output")
            unread = subprocess.Popen(
                args,
                env=cli.environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                await_file(marker)
                time.sleep(0.3)
                unread.terminate()
                self.assertEqual(unread.wait(timeout=3), 0)
            finally:
                if unread.poll() is None:
                    unread.kill()
                    unread.wait(timeout=3)
                for stream in (unread.stdout, unread.stderr):
                    if stream is not None:
                        stream.close()
            self.assertEqual(cli.call("status")["global_owned"], 0)
