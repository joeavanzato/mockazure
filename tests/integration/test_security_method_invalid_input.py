import pytest

from tests.integration.graph_support import graph_client


@pytest.mark.parametrize("annotation", [[], {}, 7, None])
async def test_invalid_method_type_is_a_client_error(database, settings, annotation):
    async with graph_client(database, settings) as (client, tenant, app, user):
        response = await client.post(
            f"/_mock/tenants/{tenant}/security/users/{user}/authenticationMethods",
            json={"@odata.type": annotation},
        )
        assert response.status_code == 400, response.text
