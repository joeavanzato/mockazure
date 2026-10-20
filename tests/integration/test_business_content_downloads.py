import hashlib

import pytest

from mockazure.population.catalog import get_adapter
from mockazure.population.corpus.render import render
from mockazure.population.types import AdapterContext, Mutation
from tests.integration.graph_support import graph_client
from tests.unit.test_business_corpus import FORMATS, specification


@pytest.mark.parametrize("format", FORMATS)
async def test_generated_business_document_download_matches_declared_bytes(
    database, settings, format
):
    async with graph_client(database, settings) as (client, tenant, *_):
        ctx = AdapterContext(database=database, settings=settings, tenant_id=tenant)
        spec = specification(format)
        rendered = render(spec)
        adapter = get_adapter("content.objects")
        mutation = Mutation(
            operation="render", logical_key=spec.logical_key, payload=spec.model_dump()
        )
        result = await adapter.apply(ctx, [mutation])
        assert (await adapter.apply(ctx, [mutation])).items[0].id == result.items[0].id
        response = await client.get(
            f"/_mock/tenants/{tenant}/content/objects/{result.items[0].id}/$value"
        )
        assert response.status_code == 200, response.text
        assert response.content == b"".join(rendered.chunks())
        assert hashlib.sha256(response.content).hexdigest() == rendered.sha256
        assert len(response.content) == rendered.length
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in response.headers["content-security-policy"]
