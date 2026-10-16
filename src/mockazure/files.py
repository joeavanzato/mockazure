"""Small cross-process initialization primitives; fail closed after interrupted writes."""

import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(directory: Path, name: str):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / name
    deadline = time.monotonic() + 30
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise ValueError(
                    "Initialization lock is busy; inspect an interrupted initializer"
                ) from None
            time.sleep(0.05)
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def write_private(path: Path, contents: bytes):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())
