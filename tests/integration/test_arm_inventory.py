from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from sqlalchemy import select

from mockazure.identity.models import ServicePrincipal
from mockazure.providers.arm.contracts import validate_arm_payload
from tests.integration.graph_support import graph_client, headers_for, registered_app
from tests.integration.test_arm_authorization import assign
from tests.integration.test_arm_state import seed_arm


async def access(database, settings, tenant, app):
    return await headers_for(
        database, settings, tenant, app, None, set(), audience="https://management.azure.com/"
    )


async def test_arm_discovery_inventory_filters_and_current_pages(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        grant = await assign(client, base, sp, f"/subscriptions/{sub}", "Reader")
        for version in ("2020-01-01", "2021-01-01"):
            tenants = await client.get("/tenants", params={"api-version": version}, headers=headers)
            assert tenants.status_code == 200, tenants.text
            assert "nextLink" not in tenants.json()
            assert tenants.json()["value"][0]["tenantId"] == str(tenant)
            validate_arm_payload("TenantIdDescription", tenants.json()["value"][0])
            found = await client.get(
                "/subscriptions", params={"api-version": version}, headers=headers
            )
            assert found.status_code == 200, found.text
            assert "nextLink" not in found.json()
            assert found.json()["value"][0]["subscriptionId"] == sub
            single = await client.get(
                f"/subscriptions/{sub}", params={"api-version": version}, headers=headers
            )
            assert single.status_code == 200, single.text
            validate_arm_payload("Subscription", single.json())
        args = {"api-version": "2021-04-01"}
        response = await client.get(
            f"/subscriptions/{sub}/resourcegroups", params=args, headers=headers
        )
        assert response.status_code == 200, response.text
        validate_arm_payload("ResourceGroup", response.json()["value"][0])
        response = await client.get(group.upper(), params=args, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "lab"
        for number in range(3):
            item = resource | {"id": resource["id"] + str(number), "name": "vm" + str(number)}
            assert (
                await client.post(base + "/resources", json={"resources": [item]})
            ).status_code == 200
        path = f"/subscriptions/{sub}/resources"
        filtered = await client.get(
            path,
            params=args
            | {
                "$filter": (
                    "resourceType eq 'Microsoft.Compute/virtualMachines' and location ne 'westus'"
                ),
                "$expand": "createdTime,changedTime,provisioningState",
            },
            headers=headers,
        )
        assert filtered.status_code == 200, filtered.text
        assert len(filtered.json()["value"]) == 4
        assert all("createdTime" in row for row in filtered.json()["value"])
        tags = await client.get(
            path,
            params=args | {"$filter": "tagname eq 'purpose' and tagvalue eq 'Evidence'"},
            headers=headers,
        )
        assert tags.status_code == 200, tags.text
        assert len(tags.json()["value"]) == 4
        assert all("tags" not in row for row in tags.json()["value"])
        assert (
            await client.get(
                path,
                params=args | {"$filter": "tagName eq 'purpose' and tagValue eq 'evidence'"},
                headers=headers,
            )
        ).json()["value"] == []
        first = await client.get(path, params=args | {"$top": "1"}, headers=headers)
        assert first.status_code == 200, first.text
        link = first.json()["nextLink"]
        seen = first.json()["value"]
        assert len(parse_qs(urlsplit(link).query)["$skiptoken"][0]) == 43
        other_app = await registered_app(client, tenant)
        other_headers = await access(database, settings, tenant, other_app)
        assert (await client.get(link, headers=other_headers)).status_code in {400, 403}
        while link:
            page = await client.get(link, headers=headers)
            assert page.status_code == 200, page.text
            seen += page.json()["value"]
            link = page.json().get("nextLink")
        assert len(seen) == len({row["id"] for row in seen}) == 4
        for query in (
            {"$select": "name"},
            {"$top": "0"},
            {"$filter": "location gt 'a'"},
            {"$expand": "properties"},
            {"$filter": "tagValue eq 'Evidence'"},
        ):
            bad = await client.get(path, params=args | query, headers=headers)
            assert bad.status_code == 400, bad.text
            assert "x-ms-request-id" in bad.headers
        for malformed in (
            f"/subscriptions/{sub}/resourceGroups/lab%2fother",
            f"/subscriptions/{sub}/resourceGroups/%256cab",
        ):
            assert (await client.get(malformed, params=args, headers=headers)).status_code == 400
        assert (await client.delete(base + "/role-assignments/" + grant["name"])).status_code == 200
        assert (await client.get(first.json()["nextLink"], headers=headers)).status_code == 403
        assert (
            await client.get(
                "/subscriptions", params={"api-version": "2021-01-01"}, headers=headers
            )
        ).json()["value"] == []


async def test_arm_descendant_grant_filters_before_page(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, group, "Reader")
        assert (
            await client.put(base + "/resource-groups/labother", json={"location": "eastus"})
        ).status_code == 200
        hidden = resource | {"id": resource["id"].replace("/lab/", "/labother/")}
        assert (
            await client.post(base + "/resources", json={"resources": [hidden]})
        ).status_code == 200
        for suffix in ("resources", "resourcegroups"):
            result = await client.get(
                f"/subscriptions/{sub}/{suffix}",
                params={"api-version": "2021-04-01"},
                headers=headers,
            )
            assert result.status_code == 200, result.text
            assert len(result.json()["value"]) == 1
            assert "/labother" not in result.text
        assert (
            await client.get(
                f"/subscriptions/{uuid4()}/resources",
                params={"api-version": "2021-04-01"},
                headers=headers,
            )
        ).status_code == 404


async def test_arm_cursor_changes_expiry_and_unknown_route_errors(database, settings):
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from mockazure.directory.models import Continuation
    from mockazure.providers.arm.models import Subscription

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Reader")
        clone = resource | {"name": "vm2", "id": resource["id"] + "2"}
        await client.post(base + "/resources", json={"resources": [clone]})
        path = f"/subscriptions/{sub}/resources"
        args = {"api-version": "2021-04-01", "$top": "1"}
        first = await client.get(path, params=args, headers=headers)
        async with database.sessions.begin() as session:
            row = await session.get(Subscription, UUID(sub))
            row.revision += 1
        assert (await client.get(first.json()["nextLink"], headers=headers)).status_code == 400
        first = await client.get(path, params=args, headers=headers)
        async with database.sessions.begin() as session:
            for cursor in await session.scalars(
                select(Continuation).where(Continuation.tenant_id == tenant)
            ):
                cursor.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert (await client.get(first.json()["nextLink"], headers=headers)).status_code == 400
        unknown = await client.get(
            resource["id"].replace("Microsoft.Compute", "Microsoft.Unknown"),
            params={"api-version": "2021-04-01"},
            headers=headers,
        )
        assert unknown.status_code == 501
        assert set(unknown.json()["error"]) == {"code", "message"}
        assert "x-ms-request-id" in unknown.headers
        unsupported = await client.get(
            group.rsplit("/", 1)[0],
            params={"api-version": "2021-04-01", "$expand": "createdTime"},
            headers=headers,
        )
        assert unsupported.status_code == 400
