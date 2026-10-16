import asyncio
import sys


def run(coroutine):
    """psycopg async connections require a selector loop on native Windows."""
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(coroutine)
