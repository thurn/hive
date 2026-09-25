"""Private local handoff lets a new dashboard stop the previous port owner."""

import asyncio
import os
import stat
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from hive.errors import ErrorCode, HiveError
from hive.locking import file_lock


def directory() -> Path:
    # A short, user-private path also works across different Hive state directories.
    path = Path("/tmp") / f"hive-dashboard-{os.getuid()}"
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise HiveError(
            ErrorCode.INVALID_INPUT,
            "Dashboard control directory must be private and owned by this user",
        )
    return path


async def replace(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode):
        raise HiveError(
            ErrorCode.INVALID_INPUT, "Dashboard control path is not a socket"
        )
    try:
        async with asyncio.timeout(2):
            reader, writer = await asyncio.open_unix_connection(path, limit=64)
    except (FileNotFoundError, ConnectionRefusedError):
        path.unlink(missing_ok=True)
        return
    try:
        async with asyncio.timeout(2):
            writer.write(b"stop\n")
            await writer.drain()
            if await reader.readline() != b"HiveDashboardStopping\n":
                raise HiveError(
                    ErrorCode.INVALID_INPUT, "Unrecognized dashboard control response"
                )
    finally:
        writer.close()
        await writer.wait_closed()
    # Wait for all old request/build children to drain, not just the HTTP socket.
    async with asyncio.timeout(8):
        while path.exists():
            await asyncio.sleep(0.025)


@asynccontextmanager
async def listener(
    port: int,
    connected: Callable[[asyncio.StreamReader, asyncio.StreamWriter], None],
    stopped: asyncio.Event,
) -> AsyncIterator[asyncio.Server]:
    root = directory()
    path = root / f"{port}.sock"

    async def control(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            async with asyncio.timeout(1):
                if await reader.readline() == b"stop\n":
                    writer.write(b"HiveDashboardStopping\n")
                    await writer.drain()
                    stopped.set()
        except (OSError, TimeoutError, ValueError):
            pass
        finally:
            writer.close()

    server: asyncio.Server | None = None
    endpoint: asyncio.Server | None = None
    identity: tuple[int, int] | None = None
    try:
        # Only startup is serialized. No serving process holds this admission lock.
        with file_lock(root / f"{port}.lock", timeout=12):
            await replace(path)
            server = await asyncio.start_server(
                connected, "127.0.0.1", port, limit=16384
            )
            endpoint = await asyncio.start_unix_server(control, path, limit=64)
            info = path.lstat()
            identity = (info.st_dev, info.st_ino)
        yield server
    except TimeoutError as error:
        raise HiveError(
            ErrorCode.BUSY, "Existing dashboard did not stop in time"
        ) from error
    except OSError as error:
        raise HiveError(
            ErrorCode.INVALID_INPUT,
            f"Cannot bind dashboard to 127.0.0.1:{port}: {error}",
        ) from error
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
        if endpoint is not None:
            endpoint.close()
            await endpoint.wait_closed()
        if identity is not None:
            try:
                info = path.lstat()
                if (info.st_dev, info.st_ino) == identity:
                    path.unlink()
            except FileNotFoundError:
                pass
