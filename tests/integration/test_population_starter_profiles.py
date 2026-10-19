import asyncio
from datetime import UTC, datetime
from uuid import UUID

from mockazure.population.actions import PublicActions
from mockazure.population.catalog import get_adapter
from mockazure.population.generators import generate
from mockazure.population.identity import resolve_identity
from mockazure.population.transport import LocalTransport
from mockazure.population.types import AdapterContext
from mockazure.profiles.builtin import builtin_bundle
from mockazure.profiles.resolver import resolve_template
from tests.integration.identity_support import identity_client


async def test_collection_profile_connects_actual_service_resources(
    database, settings, monkeypatch
):
    async with identity_client(database, settings) as (client, tenant, *_):
        ctx = AdapterContext(database, settings, UUID(tenant))

        async def transport(self, context, identity):
            return LocalTransport(context, await resolve_identity(context, identity), client=client)

        monkeypatch.setattr(PublicActions, "transport", transport)
        profile = resolve_template(
            builtin_bundle(),
            "collection-lab",
            {"shared.users": 2},
            datetime(2026, 10, 7, tzinfo=UTC),
        )
        refs = {"domain": "contoso.test"}
        outcomes = {}
        for name, step in profile.steps.items():
            parameters = {
                "tenant": tenant,
                "namespace": "starter",
                "users": 2,
                "referenceTime": profile.referenceTime.isoformat(),
                "seed": profile.seed,
                "days": 7,
                **step.parameters,
            }
            adapter = get_adapter(step.family)
            mutations = generate(parameters["generator"], parameters, refs, offset=0, limit=1000)
            results = []
            for mutation in mutations:
                try:
                    result = await asyncio.wait_for(adapter.apply(ctx, [mutation]), 20)
                except Exception as exc:
                    raise AssertionError(
                        f"Starter step {name} failed: {type(exc).__name__}: {exc}"
                    ) from exc
                results.extend(result.items)
                verification = await adapter.verify(ctx, result)
                assert verification.status in {"succeeded", "pending"}, name
            if step.target:
                refs[step.target] = results[0].id
            outcomes[name] = results
        assert UUID(refs["workspace"])
        assert outcomes["calendar"][0].properties["public_action"]
        assert outcomes["blob"][0].properties["content_size"] > 0
        assert (
            outcomes["file"][0].properties["content_sha256"]
            == outcomes["blob"][0].properties["content_sha256"]
        )
        for name in ("blob", "file"):
            step = profile.steps[name]
            parameters = {
                "tenant": tenant,
                "namespace": "starter",
                "referenceTime": profile.referenceTime.isoformat(),
                **step.parameters,
            }
            mutation = generate(parameters["generator"], parameters, refs, offset=0, limit=1)[0]
            replay = await get_adapter(step.family).apply(ctx, [mutation])
            assert replay.effects["unchanged"] == 1
