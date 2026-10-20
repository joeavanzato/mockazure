from datetime import UTC, datetime
from uuid import UUID

from mockazure.population.actions import PublicActions
from mockazure.population.catalog import get_adapter
from mockazure.population.generators import generate
from mockazure.population.identity import resolve_identity
from mockazure.population.transport import LocalTransport
from mockazure.population.types import AdapterContext, ResourceQuery
from mockazure.profiles.builtin import builtin_bundle
from mockazure.profiles.resolver import resolve_template
from tests.integration.identity_support import identity_client


async def test_stacked_lifecycle_uses_disposable_subjects_and_wall_time(
    database, settings, monkeypatch
):
    async with identity_client(database, settings) as (client, tenant, *_):
        ctx = AdapterContext(database, settings, UUID(tenant))

        async def transport(self, context, identity):
            return LocalTransport(context, await resolve_identity(context, identity), client=client)

        monkeypatch.setattr(PublicActions, "transport", transport)
        profile = resolve_template(
            builtin_bundle(),
            "living-identity-lab",
            {"shared.users": 2},
            datetime(2026, 10, 7, tzinfo=UTC),
        )
        refs = {"domain": "contoso.test"}
        steps = {
            k: s
            for k, s in profile.steps.items()
            if k.startswith(("lifecycle-", "privileged-")) or k == "entra-license"
        }
        results = {}
        for name, step in steps.items():
            parameters = {
                "tenant": tenant,
                "namespace": "disposable",
                "referenceTime": profile.referenceTime.isoformat(),
                **step.parameters,
            }
            adapter = get_adapter(step.family)
            mutation = generate(parameters["generator"], parameters, refs, offset=0, limit=1)[0]
            result = await adapter.apply(ctx, [mutation])
            assert (await adapter.verify(ctx, result)).status == "succeeded", name
            results[name] = result
            if step.target:
                refs[step.target] = result.items[0].id
            if name == "lifecycle-delete":
                page = await get_adapter("identity.deletedObjects").browse(ctx, ResourceQuery())
                assert refs["lifecycle-user"] in {i.id for i in page.items}
        assert refs["lifecycle-user"] != refs["privileged-user"]
        assert results["lifecycle-restore"].items[0].id == refs["lifecycle-user"]
        eligible = await get_adapter("identity.pimEligibility").browse(ctx, ResourceQuery())
        active = await get_adapter("identity.pimAssignments").browse(ctx, ResourceQuery())
        assert len(eligible.items) == 1 and active.items == []
        assert eligible.items[0].properties["principalId"] == refs["privileged-user"]
        assert "password" not in str(results).lower()
