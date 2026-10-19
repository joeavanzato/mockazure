from uuid import UUID, uuid4

import pytest

from mockazure.admin.errors import AdminError
from mockazure.population.catalog import get_adapter
from mockazure.population.identity import resolve_identity
from mockazure.population.transport import LocalTransport
from mockazure.population.types import AdapterContext, Mutation, ResourceQuery
from tests.integration.identity_support import identity_client


async def test_named_identity_is_encrypted_replayable_and_permission_checked(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        ctx = AdapterContext(database, settings, UUID(tenant))
        adapter = get_adapter("identity.clients")
        mutation = Mutation(
            operation="provision",
            logical_key="collector",
            payload={
                "id": "collector",
                "display_name": "Lab collector",
                "permissions": [
                    {
                        "resource": "graph",
                        "kind": "application",
                        "permissions": ["Organization.Read.All"],
                    }
                ],
            },
        )
        first = await adapter.apply(ctx, [mutation])
        assert first.effects["created"] == 1
        assert (await adapter.apply(ctx, [mutation])).effects["unchanged"] == 1
        credentials = await resolve_identity(ctx, "collector")
        page = await adapter.browse(ctx, ResourceQuery(identifier="collector"))
        serialized = page.model_dump_json()
        assert credentials.secret.get_secret_value() not in serialized
        assert "encrypted_secret" not in serialized
        public = LocalTransport(ctx, credentials, client=client)
        assert (await public.request("graph", "GET", "/v1.0/organization")).status_code == 200
        assert (await public.request("graph", "GET", "/v1.0/users")).status_code == 403
        with pytest.raises(AdminError):
            await resolve_identity(AdapterContext(database, settings, uuid4()), "collector")
