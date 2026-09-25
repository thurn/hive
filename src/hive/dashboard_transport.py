"""Resident loopback routing; application responses and builds select fresh source."""

import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path

from hive.collection_transport import environment
from hive.dashboard_routes import route
from hive.dashboard_static import asset
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import integer, parse, record, string

SECURITY = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def response(
    status: int,
    data: bytes,
    mime: str = "application/json",
    *,
    head: bool = False,
    immutable: bool = False,
    etag: str | None = None,
) -> bytes:
    headers = dict(SECURITY)
    headers.update(
        {
            "Connection": "close",
            "Content-Type": mime,
            "Content-Length": str(len(data)),
            "Cache-Control": (
                "public, max-age=31536000, immutable" if immutable else "no-store"
            ),
        }
    )
    if etag is not None:
        headers["ETag"] = etag
    return (
        f"HTTP/1.1 {status} Response\r\n"
        + "".join(f"{key}: {value}\r\n" for key, value in headers.items())
        + "\r\n"
    ).encode() + (b"" if head else data)


async def output(stream: asyncio.StreamReader | None) -> bytes:
    if stream is None:
        raise ValueError("Missing response pipe")
    data = bytearray()
    while chunk := await stream.read(65536):
        data.extend(chunk)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("Dashboard response exceeds limit")
    return bytes(data)


async def child(launcher: Path, args: tuple[str, ...], timeout: float) -> bytes:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-S",
        str(launcher),
        "dashboard",
        *args,
        "--json",
        env=environment(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        process_group=0,
    )
    read = asyncio.create_task(output(process.stdout))
    try:
        async with asyncio.timeout(timeout):
            data = await read
            await process.wait()
            return data
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        read.cancel()
        await asyncio.gather(read, return_exceptions=True)
        await process.wait()


@dataclass(frozen=True)
class Server:
    state: Path
    launcher: Path
    port: int
    writers: set[asyncio.StreamWriter] = field(default_factory=set)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    builds: set[asyncio.Task[None]] = field(default_factory=set)
    slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4))

    def connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if len(self.writers) >= 16:
            writer.write(response(503, b"{}"))
            writer.close()
            return
        self.writers.add(writer)
        task = asyncio.create_task(self.handle(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def build(self, commit: str) -> None:
        try:
            await child(self.launcher, ("build", commit), 600)
        except (OSError, ValueError, TimeoutError):
            pass

    async def application(self, args: tuple[str, ...]) -> bytes:
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=10)
        except TimeoutError:
            return json.dumps(dict(code="Busy", detail="Dashboard is busy")).encode()
        try:
            return await child(self.launcher, args, 10)
        finally:
            self.slots.release()

    async def request(self, raw: bytes) -> bytes:
        lines = raw.decode("ascii").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or parts[2] not in {"HTTP/1.0", "HTTP/1.1"}:
            return response(400, b"{}")
        method, target, _ = parts
        headers: dict[str, str] = {}
        for line in lines[1:-2]:
            key, colon, value = line.partition(":")
            key = key.lower()
            if not colon or not key or key in headers or key.strip() != key:
                return response(400, b"{}", head=method == "HEAD")
            headers[key] = value.strip()
        head = method == "HEAD"
        if headers.get("host", "").lower() not in {
            f"localhost:{self.port}",
            f"127.0.0.1:{self.port}",
        } or headers.get("sec-fetch-site", "none") not in {"none", "same-origin"}:
            return response(403, b"{}", head=head)
        if method not in {"GET", "HEAD"}:
            return response(405, b"{}")
        if headers.get("content-length", "0") != "0" or "transfer-encoding" in headers:
            return response(400, b"{}", head=head)
        selected = route(target)
        if selected is None:
            return response(404, b"{}", head=head)
        if selected.kind == "static":
            value = await asyncio.to_thread(
                asset, self.state, selected.tree, selected.asset
            )
            if value is None:
                return response(404, b"{}", head=head)
            return response(
                200,
                value[0],
                value[1],
                head=head,
                immutable=not selected.asset.endswith(".html"),
            )
        arguments: tuple[str, ...] = (
            ("page",) if selected.kind == "page" else ("api",) + selected.arguments
        )
        data = await self.application(arguments)
        value = record(parse(data.decode()))
        if selected.kind == "page" and value.get("code") == "DashboardPage":
            commit = value.get("build_commit")
            if isinstance(commit, str) and not self.builds:
                task = asyncio.create_task(self.build(commit))
                self.builds.add(task)
                task.add_done_callback(self.builds.discard)
            return response(
                integer(value["status"], "status"),
                string(value["html"], "html").encode(),
                "text/html; charset=utf-8",
                head=head,
            )
        status = {
            "Dashboard": 200,
            "excerpt_unavailable": 200,
            "InvalidInput": 400,
            "NotFound": 404,
            "Busy": 503,
            "RecoveryRequired": 503,
        }.get(str(value.get("code")), 500)
        revision = value.get("revision")
        etag = (
            '"' + str(value.get("ui_tree")) + "-" + revision + '"'
            if isinstance(revision, str)
            else None
        )
        if status == 200 and etag is not None and headers.get("if-none-match") == etag:
            return response(304, b"", head=True, etag=etag)
        return response(status, data, head=head, etag=etag)

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        data = response(400, b"{}")
        try:
            async with asyncio.timeout(5):
                raw = await reader.readuntil(b"\r\n\r\n")
            if len(raw) <= 16384:
                try:
                    data = await self.request(raw)
                except TimeoutError:
                    data = response(504, b"{}", head=raw.startswith(b"HEAD "))
        except TimeoutError:
            data = response(408, b"{}")
        except (
            asyncio.LimitOverrunError,
            asyncio.IncompleteReadError,
            UnicodeError,
            ValueError,
            HiveError,
        ):
            data = response(400, b"{}")
        except OSError:
            data = response(503, b"{}")
        finally:
            try:
                writer.write(data)
                async with asyncio.timeout(2):
                    await writer.drain()
            except (OSError, TimeoutError):
                pass
            writer.close()
            self.writers.discard(writer)

    async def close(self) -> None:
        tasks = tuple(self.tasks | self.builds)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for writer in tuple(self.writers):
            writer.close()


async def serve(state: Path, launcher: Path, port: int) -> None:
    receiver = Server(state, launcher, port)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    previous = {s: signal.getsignal(s) for s in signals}
    try:
        server = await asyncio.start_server(
            receiver.connected, "127.0.0.1", port, limit=16384
        )
    except OSError as error:
        raise HiveError(
            ErrorCode.INVALID_INPUT,
            f"Cannot bind dashboard to 127.0.0.1:{port}: {error}",
        ) from error
    try:
        for s in signals:
            loop.add_signal_handler(s, stopped.set)
        async with server:
            await stopped.wait()
    finally:
        await receiver.close()
        for s in signals:
            loop.remove_signal_handler(s)
            signal.signal(s, previous[s])


def run(state: Path, launcher: Path, port: int) -> dict[str, object]:
    if not 1 <= port <= 65535:
        raise HiveError(
            ErrorCode.INVALID_INPUT, "Dashboard port must be 1 through 65535"
        )
    asyncio.run(serve(state, launcher, port))
    return dict[str, object](code="DashboardStopped")
