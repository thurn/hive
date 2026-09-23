"""Per-session stdio MCP transport; application policy runs in fresh CLI calls."""

import asyncio
import json
import os
import signal
import sys
from enum import Enum
from pathlib import Path

from hive.errors import HiveError
from hive.jsonvalue import parse, record, string
from hive.launch_context import LaunchContext
from hive.mcp_wait import WaitArguments, failure, wait_for_delivery

# This is an external protocol identifier, not a Hive state format.
PROTOCOL: str = "2025-06-18"
MAX_FRAME: int = 1024 * 1024
MAX_PENDING: int = 8

type RequestId = str | int


class ConnectionState(Enum):
    NEW = "new"
    NEGOTIATED = "negotiated"
    READY = "ready"


def request_id(value: object) -> RequestId:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Request ID must be a string or integer")
    return value


def send(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def respond(identifier: RequestId, result: object) -> None:
    send({"jsonrpc": "2.0", "id": identifier, "result": result})


def error(identifier: RequestId | None, code: int, message: str) -> None:
    send(
        {
            "jsonrpc": "2.0",
            "id": identifier,
            "error": {"code": code, "message": message},
        }
    )


def tools_result() -> dict[str, object]:
    return {
        "tools": [
            {
                "name": "wait_for_delivery",
                "description": "Block for one Tollgate candidate's delivery. Retains ownership; cancellation stops the wait client only. No model polling is needed.",
                "inputSchema": {
                    "type": "object",
                    "required": ["candidate", "project"],
                    "additionalProperties": False,
                    "properties": {
                        "candidate": {"type": "string", "minLength": 1},
                        "project": {"type": "string", "minLength": 1},
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 2**31 - 1,
                            "default": 3600,
                        },
                    },
                },
                "annotations": {"readOnlyHint": True, "openWorldHint": True},
            }
        ]
    }


class Session:
    launcher: Path
    state: ConnectionState
    pending: dict[RequestId, asyncio.Task[None]]

    def __init__(self, launcher: Path) -> None:
        self.launcher = launcher
        self.state = ConnectionState.NEW
        self.pending = {}

    async def deliver(self, identifier: RequestId, arguments: WaitArguments) -> None:
        try:
            result = await wait_for_delivery(self.launcher, arguments)
            respond(identifier, result)
        except asyncio.CancelledError:
            # MCP cancellation has no response; retained provider work continues.
            pass

    def finished(self, identifier: RequestId, task: asyncio.Task[None]) -> None:
        # A coroutine canceled before its first instruction has no finally run.
        if self.pending.get(identifier) is task:
            self.pending.pop(identifier)

    def receive(self, raw: object) -> None:
        identifier: RequestId | None = None
        notification = False
        try:
            message = record(raw, "JSON-RPC message")
            notification = "id" not in message and isinstance(
                message.get("method"), str
            )
            if "id" in message:
                identifier = request_id(message["id"])
            if message.get("jsonrpc") != "2.0":
                raise ValueError("Expected JSON-RPC 2.0")
            method = string(message.get("method"), "method")
            params = record(message.get("params", {}), "parameters")
            if identifier is None:
                self.notification(method, params)
                return
            if identifier in self.pending:
                error(identifier, -32600, "Request ID is already pending")
                return
            if method == "initialize":
                if self.state != ConnectionState.NEW:
                    raise ValueError("Session is already initialized")
                string(params.get("protocolVersion"), "protocol version")
                record(params.get("capabilities"), "client capabilities")
                client = record(params.get("clientInfo"), "client information")
                string(client.get("name"), "client name")
                string(client.get("version"), "client version")
                self.state = ConnectionState.NEGOTIATED
                respond(
                    identifier,
                    {
                        "protocolVersion": PROTOCOL,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "hive", "version": "0"},
                    },
                )
            elif method == "ping":
                respond(identifier, {})
            elif self.state != ConnectionState.READY:
                error(identifier, -32000, "Complete initialization first")
            elif method == "tools/list":
                respond(identifier, tools_result())
            elif method == "tools/call":
                if params.get("name") != "wait_for_delivery":
                    error(identifier, -32602, "Unknown tool")
                    return
                arguments = WaitArguments.read(params.get("arguments"))
                if len(self.pending) >= MAX_PENDING:
                    respond(
                        identifier,
                        failure(
                            "Busy", "This MCP session already has eight pending waits"
                        ),
                    )
                    return
                task = asyncio.create_task(self.deliver(identifier, arguments))
                self.pending[identifier] = task
                pending_id: RequestId = identifier
                task.add_done_callback(lambda done: self.finished(pending_id, done))
            else:
                error(identifier, -32601, "Method not found")
        except (HiveError, ValueError) as invalid:
            # Notifications must not receive replies, including malformed ones.
            if not notification:
                error(
                    identifier, -32600 if identifier is None else -32602, str(invalid)
                )

    def notification(self, method: str, params: dict[str, object]) -> None:
        if (
            method == "notifications/initialized"
            and self.state == ConnectionState.NEGOTIATED
        ):
            self.state = ConnectionState.READY
        elif method == "notifications/cancelled":
            task = self.pending.get(request_id(params.get("requestId")))
            if task is not None and not task.cancelling():
                task.cancel()

    async def close(self) -> None:
        tasks = list(self.pending.values())
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def serve(launcher: Path) -> None:
    loop = asyncio.get_running_loop()
    current: asyncio.Task[object] | None = asyncio.current_task()
    if current is None:
        raise RuntimeError("Missing MCP task")
    active: asyncio.Task[object] = current

    def interrupt() -> None:
        if not active.cancelling():
            active.cancel()

    for stop in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop, interrupt)
    reader = asyncio.StreamReader(limit=MAX_FRAME)
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )
    session = Session(launcher)
    try:
        while line := await reader.readline():
            try:
                session.receive(parse(line.decode()))
            except (HiveError, UnicodeDecodeError) as invalid:
                error(None, -32700, str(invalid))
    except ValueError:
        error(None, -32600, "MCP message exceeds the one MiB frame limit")
    except asyncio.CancelledError:
        pass
    finally:
        transport.close()
        await session.close()
        for stop in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(stop)


def main() -> int:
    context = LaunchContext.read()
    context.release()
    repository = Path(os.environ["HIVE_REPOSITORY_DIRECTORY"])
    asyncio.run(serve(repository / "scripts/hive.py"))
    return 0
