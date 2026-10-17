import pytest

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_managed_identity import identity


@pytest.mark.parametrize("resource", ["applications", "servicePrincipals"])
@pytest.mark.parametrize("action", ["addPassword", "microsoft.graph.addPassword"])
async def test_managed_password_creation_is_rejected(database, settings, resource, action):
    async with graph_client(database, settings) as (client, tenant, actor, _):
        managed = await identity(client, tenant)
        headers = await headers_for(
            database, settings, tenant, actor, None, {"Application.ReadWrite.All"}
        )
        path = f"/v1.0/{resource}(appId='{managed['client_id']}')/{action}"
        result = await client.post(path, headers=headers, json={"passwordCredential": {}})
        assert result.status_code == 400
        assert "secretText" not in result.json()
