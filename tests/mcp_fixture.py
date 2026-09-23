"""A real stdio client; frame reads have deadlines and preserve buffered messages."""

import json
import os
import select
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cli_fixture import ROOT

from hive.jsonvalue import parse, record


class McpClient:
    process: subprocess.Popen[bytes]
    buffer: bytes

    def __init__(self, environment: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts/hive.py"), "mcp"],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.buffer = b""

    def send(
        self, method: str, identifier: int | str | None = None, **params: object
    ) -> None:
        value: dict[str, object] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if identifier is not None:
            value["id"] = identifier
        self.write(json.dumps(value).encode() + b"\n")

    def write(self, data: bytes) -> None:
        stream = self.process.stdin
        if stream is None:
            raise AssertionError("Missing MCP input")
        stream.write(data)
        stream.flush()

    def receive(self, timeout: float = 10) -> dict[str, object]:
        stream = self.process.stdout
        if stream is None:
            raise AssertionError("Missing MCP output")
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
                raise AssertionError("MCP response deadline exceeded")
            chunk = os.read(stream.fileno(), 65536)
            if not chunk:
                errors = self.process.stderr
                raise AssertionError(
                    "MCP closed: " + (errors.read().decode() if errors else "")
                )
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return record(parse(line.decode()))

    def initialize(self) -> None:
        self.send(
            "initialize",
            1,
            protocolVersion="2025-06-18",
            capabilities={},
            clientInfo={"name": "hive-test", "version": "0"},
        )
        response = self.receive()
        if response.get("id") != 1 or "result" not in response:
            raise AssertionError(response)
        self.send("notifications/initialized")

    def wait(self, identifier: int, candidate: str, **arguments: object) -> None:
        self.send(
            "tools/call",
            identifier,
            name="wait_for_delivery",
            arguments={"candidate": candidate, "project": "search", **arguments},
        )

    def close(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
            raise AssertionError("MCP did not stop after EOF") from None
        finally:
            for stream in (self.process.stdout, self.process.stderr):
                if stream is not None:
                    stream.close()


@contextmanager
def mcp_client(environment: dict[str, str]) -> Iterator[McpClient]:
    client = McpClient(environment)
    try:
        yield client
    finally:
        client.close()


def await_file(path: Path) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"Provider did not enter wait: {path}")
        time.sleep(0.02)


def assert_stopped(pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    raise AssertionError(f"Wait client {pid} survived cancellation")
