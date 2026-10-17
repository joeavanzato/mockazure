from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.integration.graph_support import graph_client


async def seed_arm(client, tenant):
    response = await client.post(
        f"/_mock/tenants/{tenant}/subscriptions", json={"display_name": "ARM evidence"}
    )
    assert response.status_code == 201, response.text
    subscription = response.json()["id"]
    base = f"/_mock/tenants/{tenant}/subscriptions/{subscription}"
    response = await client.put(base + "/resource-groups/lab", json={"location": "eastus"})
    assert response.status_code == 200, response.text
    scope = f"/subscriptions/{subscription}/resourceGroups/lab"
    resource = {
        "id": scope + "/providers/Microsoft.Compute/virtualMachines/vm",
        "name": "vm",
        "type": "Microsoft.Compute/virtualMachines",
        "location": "eastus",
        "tags": {"Purpose": "Evidence"},
        "properties": {"hardwareProfile": {"vmSize": "Standard_D2s_v5"}},
    }
    response = await client.post(base + "/resources", json={"resources": [resource]})
    assert response.status_code == 200, response.text
    return subscription, base, scope, resource


async def test_canonical_inventory_seed_and_ownership(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        subscription, base, scope, resource = await seed_arm(client, tenant)
        from mockazure.providers.arm.inventory import inventory_view, resolve_scope
        from mockazure.providers.arm.models import ArmResource
        from mockazure.providers.arm.scopes import parse_scope

        response = await client.post(base + "/resources", json={"resources": [resource]})
        assert response.status_code == 200
        async with database.sessions() as session:
            row = await resolve_scope(session, tenant, parse_scope(resource["id"].upper()))
            assert inventory_view(row)["id"] == resource["id"]
            assert inventory_view(row)["properties"] == resource["properties"]
            before = row.revision
        changed = resource | {"tags": {"Purpose": "Changed"}}
        assert (
            await client.post(base + "/resources", json={"resources": [changed]})
        ).status_code == 200
        async with database.sessions() as session:
            row = await resolve_scope(session, tenant, parse_scope(resource["id"]))
            assert row.revision == before + 1
            assert inventory_view(row)["tags"] == changed["tags"]
            assert (
                len(
                    list(
                        await session.scalars(
                            select(ArmResource).where(
                                ArmResource.tenant_id == tenant, ArmResource.kind == "resource"
                            )
                        )
                    )
                )
                == 1
            )
        from datetime import datetime

        historical = changed | {
            "createdTime": "2026-10-01T00:00:00Z",
            "changedTime": "2026-10-02T12:00:00Z",
        }
        assert (
            await client.post(base + "/resources", json={"resources": [historical]})
        ).status_code == 200
        async with database.sessions() as session:
            row = await resolve_scope(session, tenant, parse_scope(resource["id"]))
            assert row.created_at == datetime.fromisoformat("2026-10-01T00:00:00+00:00")
            assert row.changed_at == datetime.fromisoformat("2026-10-02T12:00:00+00:00")
            revision = row.revision
        assert (
            await client.post(base + "/resources", json={"resources": [historical]})
        ).status_code == 200
        async with database.sessions() as session:
            row = await resolve_scope(session, tenant, parse_scope(resource["id"]))
            assert row.revision == revision
        state_only = changed | {"provisioningState": "Updating"}
        assert (
            await client.post(base + "/resources", json={"resources": [state_only]})
        ).status_code == 200
        async with database.sessions() as session:
            row = await resolve_scope(session, tenant, parse_scope(resource["id"]))
            assert row.provisioning_state == "Updating"
        for bad in (
            resource | {"id": resource["id"].replace("/lab/", "/missing/")},
            resource | {"properties": {"clientSecret": "must-not-store"}},
            resource | {"name": "different"},
            resource | {"provisioningState": "x" * 33},
            resource
            | {"createdTime": "2026-10-02T00:00:00Z", "changedTime": "2026-10-01T00:00:00Z"},
            resource | {"type": "Microsoft.Storage/storageAccounts"},
        ):
            response = await client.post(base + "/resources", json={"resources": [bad]})
            assert response.status_code in {400, 404, 422}, response.text
        async with database.sessions.begin() as session:
            group = await resolve_scope(session, tenant, parse_scope(scope))
            group.provisioning_state = "Deleting"
        response = await client.post(base + "/resources", json={"resources": [changed]})
        assert response.status_code == 409, response.text
        # The database also rejects an inventory row claiming another subscription.
        other = (
            await client.post(
                f"/_mock/tenants/{tenant}/subscriptions", json={"display_name": "Other"}
            )
        ).json()["id"]
        with pytest.raises(IntegrityError):
            async with database.sessions.begin() as session:
                row = await resolve_scope(session, tenant, parse_scope(resource["id"]))
                row.subscription_id = UUID(other)
                await session.flush()
