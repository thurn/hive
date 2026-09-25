"""Bounded resident loopback HTTP transport; bodies remain opaque until sweep."""

import asyncio
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from hive.otlp_storage import listener_status, rejected, secret, spool_body

MAX_BODY = 4 * 1024 * 1024
MAX_HEADERS = 16 * 1024
MAX_CONNECTIONS = 8


@dataclass(frozen=True)
class Receiver:
    state: Path
    token: str
    writers: set[asyncio.StreamWriter] = field(default_factory=set)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    spool_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    rejections: asyncio.Queue[int] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1)
    )

    def reject_connection(self) -> None:
        # One bounded queue cell coalesces bursts without disk I/O on the timer
        # loop or an unbounded task per refused connection.
        count = 1
        try:
            count += self.rejections.get_nowait()
        except asyncio.QueueEmpty:
            pass
        self.rejections.put_nowait(count)

    async def record_rejections(self) -> None:
        while True:
            count = await self.rejections.get()
            try:
                await asyncio.to_thread(rejected, self.state, count)
            except OSError:
                pass

    def connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if len(self.writers) >= MAX_CONNECTIONS:
            # Refuse immediately without allocating a waiting connection task.
            writer.write(
                b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 2\r\n\r\n{}"
            )
            writer.close()
            self.reject_connection()
            return
        self.writers.add(writer)
        task = asyncio.create_task(self.handle(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        code = 400
        try:
            async with asyncio.timeout(5):
                head = await reader.readuntil(b"\r\n\r\n")
            if len(head) > MAX_HEADERS:
                code = 413
            else:
                code = await self.request(head, reader)
        except (
            asyncio.LimitOverrunError,
            asyncio.IncompleteReadError,
            UnicodeError,
            ValueError,
        ):
            code = 400
        except TimeoutError:
            code = 408
        except OSError:
            code = 503
            try:
                await asyncio.to_thread(rejected, self.state)
            except OSError:
                pass
        finally:
            try:
                writer.write(
                    f"HTTP/1.1 {code} Response\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{{}}".encode()
                )
                async with asyncio.timeout(1):
                    await writer.drain()
            except (OSError, TimeoutError):
                pass
            writer.close()
            self.writers.discard(writer)

    async def request(self, head: bytes, reader: asyncio.StreamReader) -> int:
        lines = head.decode("ascii").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or parts[2] not in {"HTTP/1.0", "HTTP/1.1"}:
            return 400
        method, path, _ = parts
        if path != "/v1/logs":
            return 404
        if method != "POST":
            return 405
        headers: dict[str, str] = {}
        for line in lines[1:-2]:
            key, colon, value = line.partition(":")
            key = key.lower()
            if not colon or not key or key in headers or key.strip() != key:
                return 400
            headers[key] = value.strip()
        host = headers.get("host", "").lower()
        if host.startswith("["):
            hostname, _, port = host.partition("]")
            valid_host = hostname == "[::1" and (
                not port or (port.startswith(":") and port[1:].isdigit())
            )
        else:
            hostname, colon, port = host.partition(":")
            valid_host = hostname in {"localhost", "127.0.0.1"} and (
                not colon or port.isdigit()
            )
        if not valid_host:
            return 400
        if not hmac.compare_digest(
            headers.get("authorization", ""), "Bearer " + self.token
        ):
            return 401
        if (
            headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            return 415
        length = headers.get("content-length", "")
        if (
            "transfer-encoding" in headers
            or not length.isascii()
            or not length.isdigit()
        ):
            return 400
        if length == "0":
            return 400
        if len(length) > 10 or int(length) > MAX_BODY:
            return 413
        if int(length) == 0:
            return 400
        async with asyncio.timeout(30):
            body = await reader.readexactly(int(length))
        async with self.spool_lock:
            stored = await asyncio.to_thread(spool_body, self.state, body)
        return 200 if stored else 503

    async def close(self) -> None:
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for writer in tuple(self.writers):
            writer.close()


@asynccontextmanager
async def listen(state: Path, port: int | None) -> AsyncIterator[None]:
    if port is None:
        listener_status(state, port=None, listening=False, error=None)
        yield
        return
    receiver: Receiver | None = None
    server: asyncio.Server | None = None
    failure: str | None = None
    logging: asyncio.Task[None] | None = None
    try:
        try:
            receiver = Receiver(state, secret(state))
            logging = asyncio.create_task(receiver.record_rejections())
            server = await asyncio.start_server(
                receiver.connected, "127.0.0.1", port, limit=MAX_HEADERS
            )
        except (OSError, ValueError) as error:
            failure = str(error)
        listener_status(state, port=port, listening=server is not None, error=failure)
        yield
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
        if receiver is not None:
            await receiver.close()
        if logging is not None:
            logging.cancel()
            await asyncio.gather(logging, return_exceptions=True)
        if receiver is not None and not receiver.rejections.empty():
            try:
                await asyncio.to_thread(
                    rejected, state, receiver.rejections.get_nowait()
                )
            except OSError:
                pass
        listener_status(state, port=port, listening=False, error=failure)
