from uuid import UUID

import pytest
from sqlalchemy import select

from mockazure.security.models import SecurityOwner
from tests.integration.graph_support import graph_client, headers_for


@pytest.mark.parametrize("owner_kind", ["user", "servicePrincipal", "application"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_deleted_owners_do_not_break_surviving_object_operations(
    database, settings, owner_kind, legacy
):
    async with graph_client(database, settings) as (client, tenant, actor, user):
        headers = await headers_for(
            database,
            settings,
            tenant,
            actor,
            None,
            {"Application.ReadWrite.All", "User.ReadWrite.All", "Directory.Read.All"},
        )
        target = (
            await client.post(
                "/v1.0/applications", headers=headers, json={"displayName": "Surviving application"}
            )
        ).json()
        target_path = "/v1.0/applications/" + target["id"]
        if owner_kind == "user":
            owner_id = str(user)
            delete_path = "/v1.0/users/" + owner_id
        else:
            app = (
                await client.post(
                    "/v1.0/applications", headers=headers, json={"displayName": "Owner application"}
                )
            ).json()
            sp = (
                await client.post(
                    "/v1.0/servicePrincipals", headers=headers, json={"appId": app["appId"]}
                )
            ).json()
            owner_id = sp["id"]
            delete_path = (
                "/v1.0/applications/" + app["id"]
                if owner_kind == "application"
                else "/v1.0/servicePrincipals/" + owner_id
            )
        result = await client.post(
            target_path + "/owners/$ref",
            headers=headers,
            json={"@odata.id": "https://localhost:8443/v1.0/directoryObjects/" + owner_id},
        )
        assert result.status_code == 204, result.text
        assert (await client.delete(delete_path, headers=headers)).status_code == 204
        if legacy:
            # Reinsert the orphan edge left by an older release, without reactivating its owner.
            async with database.sessions.begin() as session:
                key = (tenant, UUID(target["id"]), UUID(owner_id))
                if await session.get(SecurityOwner, key) is None:
                    session.add(SecurityOwner(tenant_id=tenant, object_id=key[1], owner_id=key[2]))
        else:
            async with database.sessions() as session:
                assert (
                    await session.scalar(
                        select(SecurityOwner.owner_id).where(
                            SecurityOwner.tenant_id == tenant,
                            SecurityOwner.owner_id == UUID(owner_id),
                        )
                    )
                    is None
                )
        result = await client.get(target_path + "/owners", headers=headers)
        assert result.status_code == 200, result.text
        assert owner_id not in {r["id"] for r in result.json()["value"]}
        expanded = await client.get(target_path + "?$expand=owners", headers=headers)
        assert expanded.status_code == 200, expanded.text
        if legacy:
            assert (
                await client.delete(target_path + "/owners/" + owner_id + "/$ref", headers=headers)
            ).status_code == 204
        replacement = (
            await client.post(
                f"/_mock/tenants/{tenant}/identity/users",
                json={
                    "upn": "replacement@contoso.test",
                    "display_name": "Replacement",
                    "password": "local-replacement-password",
                },
            )
        ).json()["id"]
        assert (
            await client.post(
                target_path + "/owners/$ref",
                headers=headers,
                json={"@odata.id": "https://localhost:8443/v1.0/directoryObjects/" + replacement},
            )
        ).status_code == 204
        assert (
            await client.delete(target_path + "/owners/" + replacement + "/$ref", headers=headers)
        ).status_code == 204
