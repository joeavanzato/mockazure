"""Calendar empty action bodies remain bounded without buffering arbitrary requests."""

import pytest
from starlette.requests import Request

from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError


async def test_optional_json_stops_before_unbounded_body():
    consumed = 0

    async def receive():
        nonlocal consumed
        consumed += 1
        assert consumed <= 2, "read past configured body bound"
        return {"type": "http.request", "body": b"x" * 600, "more_body": True}

    request = Request(
        {"type": "http", "headers": [(b"content-type", b"application/json")]}, receive
    )
    with pytest.raises(GraphError) as result:
        await read_json(request, limit=1000, allow_empty=True)
    assert result.value.status == 413
    assert consumed == 2


async def test_optional_json_accepts_empty_delete_without_content_type():
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request({"type": "http", "headers": []}, receive)
    assert await read_json(request, allow_empty=True) == {}
