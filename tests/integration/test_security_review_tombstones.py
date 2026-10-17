from datetime import UTC, datetime

import pytest

from mockazure.identity.models import Application, ServicePrincipal
from tests.integration.graph_support import graph_client, headers_for


@pytest.mark.parametrize("kind", ["application", "servicePrincipal"])
async def test_enabled_tombstones_cannot_use_previously_issued_tokens(database, settings, kind):
    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(
            database, settings, tenant, app, None, {"Organization.Read.All"}
        )
        assert (await client.get("/v1.0/organization", headers=headers)).status_code == 200
        async with database.sessions.begin() as session:
            if kind == "application":
                row = await session.get(Application, app)
            else:
                from sqlalchemy import select

                row = await session.scalar(
                    select(ServicePrincipal).where(
                        ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                    )
                )
            row.deleted_at = datetime.now(UTC)
            assert row.enabled
        assert (await client.get("/v1.0/organization", headers=headers)).status_code == 403
