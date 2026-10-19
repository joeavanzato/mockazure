"""Bounded request spooling keeps network waits outside Storage transactions."""

import asyncio
from contextlib import asynccontextmanager
from tempfile import SpooledTemporaryFile

import anyio

from mockazure.content.models import DEFAULT_MAX_SIZE
from mockazure.providers.storage.errors import StorageError

MEMORY_BYTES = 1048576
RECEIVE_SECONDS = 300


class BufferedRequest:
    def __init__(self, request, body):
        self.request = request
        self.buffer = body

    def __getattr__(self, name):
        return getattr(self.request, name)

    async def stream(self):
        await anyio.to_thread.run_sync(self.buffer.seek, 0)
        while chunk := await anyio.to_thread.run_sync(self.buffer.read, MEMORY_BYTES):
            yield chunk
        yield b""


@asynccontextmanager
async def buffered_request(request):
    # Disk-backed, per-request staging is temporary and has no publication authority.
    # Authentication/conditions are evaluated after reception under current locks.
    # Rollover before large writes avoids SpooledTemporaryFile's transient large buffer.
    length = request.headers.get("content-length")
    if length is not None and int(length) > DEFAULT_MAX_SIZE:
        raise StorageError(413, "RequestBodyTooLarge", "Storage body exceeds the local limit")
    with SpooledTemporaryFile(max_size=MEMORY_BYTES, mode="w+b") as body:
        total = 0
        try:
            async with asyncio.timeout(RECEIVE_SECONDS):
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > DEFAULT_MAX_SIZE:
                        raise StorageError(
                            413, "RequestBodyTooLarge", "Storage body exceeds the local limit"
                        )
                    if total > MEMORY_BYTES:
                        await anyio.to_thread.run_sync(body.rollover)
                    await anyio.to_thread.run_sync(body.write, chunk)
        except TimeoutError:
            raise StorageError(
                408, "RequestTimeout", "Storage request reception exceeded the local time limit"
            ) from None
        yield BufferedRequest(request, body)
