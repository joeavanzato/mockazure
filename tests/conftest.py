import asyncio
import sys

import pytest


def pytest_sessionstart(session):
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
