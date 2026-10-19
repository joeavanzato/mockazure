from datetime import UTC, datetime, timedelta
from uuid import uuid4

from mockazure.jobs.worker import run_one
from mockazure.population.catalog import get_adapter
from mockazure.population.types import Mutation, ResourceQuery
from tests.integration.test_population_adapters import context


async def test_evidence_population_waits_for_publication_and_replays(database, settings):
    ctx = await context(database, settings)
    adapter = get_adapter("evidence.signIn")
    now = datetime.now(UTC).isoformat()
    identifier = str(uuid4())
    mutation = Mutation(
        operation="import",
        logical_key="event",
        payload={
            "id": identifier,
            "occurred_at": now,
            "available_at": now,
            "payload": {
                "id": identifier,
                "userId": str(uuid4()),
                "createdDateTime": now,
                "status": {"errorCode": 0},
                "userPrincipalName": "historical@lab.test",
                "isInteractive": True,
            },
        },
    )
    result = await adapter.apply(ctx, [mutation])
    assert result.effects["created"] == 1
    assert len(result.items) == 1
    assert (await adapter.verify(ctx, result)).status == "pending"
    for _ in range(10):
        await run_one(database, owner=uuid4(), now=datetime.now(UTC) + timedelta(seconds=60))
    assert (await adapter.verify(ctx, result)).status == "succeeded"
    assert (await adapter.apply(ctx, [mutation])).effects["unchanged"] == 1
    page = await adapter.browse(ctx, ResourceQuery(identifier=identifier))
    assert page.items[0].properties["id"] == identifier
