import hashlib

import pytest

from mockazure.population.catalog import get_adapter
from mockazure.population.types import Mutation, ResourceQuery
from tests.integration.test_population_adapters import context


async def test_generated_content_uses_chunk_store_and_reconciles(database, settings):
    ctx = await context(database, settings)
    adapter = get_adapter("content.objects")
    value = Mutation(
        operation="generate",
        logical_key="content",
        payload={"text": "synthetic\n", "repeat": 200000},
    )
    result = await adapter.apply(ctx, [value])
    assert result.effects["created"] == 1
    assert (await adapter.apply(ctx, [value])).items[0].id == result.items[0].id
    detail = await adapter.browse(ctx, ResourceQuery(identifier=result.items[0].id))
    assert (
        detail.items[0].properties["sha256"] == hashlib.sha256(b"synthetic\n" * 200000).hexdigest()
    )
    assert (await adapter.verify(ctx, result)).status == "succeeded"


def test_content_upload_is_not_server_path_read():
    adapter = get_adapter("content.objects")
    for payload in (
        {"path": "C:/private/file"},
        {"url": "https://example.com/file"},
        {"text": "x", "repeat": 100000000},
    ):
        with pytest.raises(ValueError):
            adapter.validate(Mutation(operation="generate", logical_key="bad", payload=payload))
