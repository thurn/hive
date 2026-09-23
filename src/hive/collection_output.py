"""Bounded event delivery that remains cancellable under supervisor backpressure."""

import asyncio
import json
import os


def writable(ready: asyncio.Future[None]) -> None:
    if not ready.done():
        ready.set_result(None)


async def write(descriptor: int, value: dict[str, object]) -> None:
    data = (json.dumps(value) + "\n").encode()
    loop = asyncio.get_running_loop()
    offset = 0
    while offset < len(data):
        try:
            offset += os.write(descriptor, data[offset:])
        except BlockingIOError:
            ready: asyncio.Future[None] = loop.create_future()

            loop.add_writer(descriptor, writable, ready)
            try:
                await ready
            finally:
                loop.remove_writer(descriptor)


async def send(
    descriptor: int, value: dict[str, object], stop: asyncio.Task[bool]
) -> None:
    sending = asyncio.create_task(write(descriptor, value))
    try:
        await asyncio.wait((sending, stop), return_when=asyncio.FIRST_COMPLETED)
        if sending.done():
            sending.result()
    finally:
        if not sending.done():
            sending.cancel()
        try:
            await sending
        except asyncio.CancelledError:
            pass
