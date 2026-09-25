"""Real loopback requests exercise the resident listener's transport boundary."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_contention import hive
from test_source_selection import ROOT, commit, fixture

from hive.jsonvalue import record
from hive.otlp_listener import listen
from hive.otlp_storage import MAX_SPOOL, status


def port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def post(
    number: int,
    token: str,
    *,
    body: bytes = b'{"resourceLogs":[]}',
    method: str = "POST",
    path: str = "/v1/logs",
    host: str = "127.0.0.1",
    content_type: str = "application/json",
    size: int | None = None,
) -> int:
    reader, writer = await asyncio.open_connection("127.0.0.1", number)
    try:
        header = f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nAuthorization: Bearer {token}\r\nContent-Type: {content_type}\r\nContent-Length: {len(body) if size is None else size}\r\n\r\n"
        writer.write(header.encode() + body)
        await writer.drain()
        result = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)
        return int(result.split(b" ", 2)[1])
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionResetError:
            pass


class OtlpTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_auth_limits_atomic_spool_and_private_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            number = port()
            target = root / "operator-secret"
            configured = hive(
                root,
                "telemetry",
                "otlp-config",
                "--port",
                str(number),
                "--secret-file",
                str(target),
            )
            self.assertEqual(configured.returncode, 0, configured.stderr)
            token = target.read_text()
            self.assertEqual(len(token), 64)
            self.assertNotIn(token, configured.stdout + configured.stderr)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("OTEL_METRICS_EXPORTER", configured.stdout)
            state = root / "state"
            async with listen(state, number):
                self.assertTrue(record(status(state)["otlp_listener"])["listening"])
                payload = b'{ "opaque": "body" }\n'
                self.assertEqual(await post(number, token, body=payload), 200)
                spool = state / "otlp-spool"
                files = list(spool.glob("*.json"))
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0].read_bytes(), payload)
                self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
                self.assertEqual(spool.stat().st_mode & 0o777, 0o700)
                self.assertFalse(list(spool.glob("*.tmp")))
                outcomes = (
                    (401, await post(number, "wrong")),
                    (400, await post(number, token, body=b"")),
                    (404, await post(number, token, path="/v1/traces")),
                    (405, await post(number, token, method="OPTIONS")),
                    (
                        415,
                        await post(
                            number, token, content_type="application/x-protobuf"
                        ),
                    ),
                    (400, await post(number, token, host="example.com")),
                    (413, await post(number, token, size=4 * 1024 * 1024 + 1)),
                )
                for expected, actual in outcomes:
                    self.assertEqual(actual, expected)
                with (spool / "capacity.json").open("wb") as stream:
                    stream.truncate(MAX_SPOOL)
                self.assertEqual(await post(number, token, body=b""), 400)
                self.assertEqual(await post(number, token, body=b"x"), 503)
                rejection = record(json.loads((spool / "rejections.log").read_text()))
                self.assertEqual(rejection["count"], 1)
                self.assertEqual(
                    (spool / "rejections.log").stat().st_mode & 0o777, 0o600
                )
            self.assertFalse(record(status(state)["otlp_listener"])["listening"])

    async def test_slow_headers_and_connection_limit_do_not_block_other_requests(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            number = port()
            async with listen(state, number):
                token = (state / "otlp-secret").read_text()
                reader, writer = await asyncio.open_connection("127.0.0.1", number)
                writer.write(b"POST ")
                await writer.drain()
                self.assertEqual(await post(number, token), 200)
                timed_out = await asyncio.wait_for(reader.read(), 7)
                self.assertIn(b" 408 ", timed_out)
                writer.close()
                await writer.wait_closed()
                waiting = [
                    await asyncio.open_connection("127.0.0.1", number) for _ in range(8)
                ]
                try:
                    self.assertEqual(await post(number, token), 503)
                finally:
                    for _, pending in waiting:
                        pending.close()
                    for _, pending in waiting:
                        await pending.wait_closed()

    async def test_rejection_persistence_cannot_stall_event_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            spool = state / "otlp-spool"
            spool.mkdir()
            fifo = spool / "rejections.log"
            os.mkfifo(fifo, 0o600)
            number = port()
            async with listen(state, number):
                token = (state / "otlp-secret").read_text()
                waiting = [
                    await asyncio.open_connection("127.0.0.1", number) for _ in range(8)
                ]
                try:
                    # The logger is blocked opening the FIFO until a reader
                    # appears; HTTP/timer work must still make progress.
                    self.assertEqual(await post(number, token), 503)
                    waiting[0][1].close()
                    await waiting[0][1].wait_closed()
                    await asyncio.sleep(0.05)
                    self.assertEqual(await post(number, token), 200)
                finally:
                    for _, pending in waiting:
                        pending.close()
                    for _, pending in waiting:
                        await pending.wait_closed()
                    descriptor = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
                    try:
                        deadline = time.monotonic() + 3
                        data = b""
                        while not data and time.monotonic() < deadline:
                            try:
                                data = os.read(descriptor, 4096)
                            except BlockingIOError:
                                pass
                            await asyncio.sleep(0.01)
                        self.assertEqual(record(json.loads(data))["count"], 1)
                    finally:
                        os.close(descriptor)

    async def test_bind_failure_is_reported_without_failing_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            state = Path(temporary)
            async with listen(state, occupied.getsockname()[1]):
                value = record(status(state)["otlp_listener"])
                self.assertFalse(value["listening"])
                self.assertTrue(value["error"])


class WatcherOtlpTests(unittest.TestCase):
    def test_cli_watcher_keeps_listener_and_reloads_sweep_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository, environment = fixture(root)
            for package in ("hive", "hive_bootstrap"):
                shutil.copytree(
                    ROOT / "src" / package,
                    repository / "src" / package,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
            shutil.copyfile(ROOT / "scripts/hive.py", repository / "scripts/hive.py")
            initial = commit(repository, "test: actual observer")
            number = port()
            state = root / "local-state"
            output: Path = root / "watch.jsonl"
            errors = root / "watch-errors.txt"
            with output.open("w") as stdout, errors.open("w") as stderr:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(ROOT / "scripts/hive.py"),
                        "telemetry",
                        "watch",
                        "--native-index",
                        str(root / "missing-index"),
                        "--interval-seconds",
                        "1",
                        "--otlp-port",
                        str(number),
                        "--json",
                    ],
                    env=environment,
                    stdout=stdout,
                    stderr=stderr,
                )
                try:

                    def seen(commit_id: str) -> bool:
                        return commit_id in output.read_text()

                    deadline = time.monotonic() + 15
                    while (
                        not seen(initial)
                        and time.monotonic() < deadline
                        and process.poll() is None
                    ):
                        time.sleep(0.05)
                    self.assertIsNone(process.poll(), errors.read_text())
                    self.assertTrue(
                        seen(initial), output.read_text() + errors.read_text()
                    )
                    token = (state / "otlp-secret").read_text()
                    self.assertEqual(asyncio.run(post(number, token)), 200)
                    with (repository / "src/hive/collection.py").open("a") as stream:
                        stream.write("\n# Source refresh witness.\n")
                    updated = commit(repository, "test: update sweep source")
                    deadline = time.monotonic() + 15
                    while (
                        not seen(updated)
                        and time.monotonic() < deadline
                        and process.poll() is None
                    ):
                        time.sleep(0.05)
                    self.assertTrue(
                        seen(updated), output.read_text() + errors.read_text()
                    )
                    self.assertIsNone(process.poll(), errors.read_text())
                    self.assertEqual(asyncio.run(post(number, token)), 200)
                    self.assertNotIn(token, output.read_text() + errors.read_text())
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            self.assertEqual(process.returncode, 0, errors.read_text())
            self.assertFalse(record(status(state)["otlp_listener"])["listening"])
            with socket.socket() as sock:
                self.assertNotEqual(sock.connect_ex(("127.0.0.1", number)), 0)
