import asyncio
import os
import runpy
import sys
from pathlib import Path

import pytest


def pytest_sessionstart(session):
    guard = Path(__file__).resolve().parent / "support/network_guard"
    os.environ["PYTHONPATH"] = str(guard)
    runpy.run_path(str(guard / "sitecustomize.py"))

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
def tenant_payload():
    return {
        "slug": "contoso",
        "display_name": "Contoso",
        "domain": "contoso.test",
        "licences": ["E5"],
    }
