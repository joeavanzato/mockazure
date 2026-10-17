"""Regression boundaries identified by the M3C final review."""

import copy
from uuid import UUID

import pytest

from mockazure.security.models import SecurityObject
from tests.integration.graph_support import headers_for
from tests.integration.test_policy_enforcement import (
    add_policy,
    authenticate,
    policy_client,
    redeem,
)
from tests.integration.test_security_policies import BASE
from tests.policy_support import location_body, policy_body


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("category", ["Users", "Groups", "Applications", "Locations"])
@pytest.mark.parametrize("exclude", [False, True])
async def test_uuid_selector_spelling_does_not_change_oauth_decision(
    database, settings, category, exclude, legacy
):
    async with policy_client(database, settings) as (client, tenant, control, user, app, writer):
        identifier = user["id"]
        if category == "Groups":
            directory = await headers_for(
                database, settings, tenant, control, None, {"Group.ReadWrite.All"}
            )
            response = await client.post(
                "/v1.0/groups",
                headers=directory,
                json={
                    "displayName": "Policy target",
                    "mailEnabled": False,
                    "mailNickname": "target",
                    "securityEnabled": True,
                },
            )
            assert response.status_code == 201
            identifier = response.json()["id"]
            assert (
                await client.post(
                    f"/v1.0/groups/{identifier}/members/$ref",
                    headers=directory,
                    json={
                        "@odata.id": f"https://localhost:8443/v1.0/directoryObjects/{user['id']}"
                    },
                )
            ).status_code == 204
        elif category == "Applications":
            identifier = "00000003-0000-0000-c000-000000000000"
        elif category == "Locations":
            response = await client.post(
                BASE + "/namedLocations", headers=writer, json=location_body()
            )
            assert response.status_code == 201
            identifier = response.json()["id"]
        group = {
            "Users": "users",
            "Groups": "users",
            "Applications": "applications",
            "Locations": "locations",
        }[category]
        field = ("exclude" if exclude else "include") + category
        body = policy_body(control="block")
        if category == "Locations":
            body["conditions"]["locations"] = {"includeLocations": ["All"], "excludeLocations": []}
        if category == "Groups" and not exclude:
            body["conditions"]["users"]["includeUsers"] = []
        body["conditions"][group][field] = [identifier.upper()]
        path = await add_policy(client, writer, body)
        if legacy:
            # An already accepted policy must remain effective on upgrade.
            async with database.sessions.begin() as session:
                row = await session.get(
                    SecurityObject, (UUID(tenant), UUID(path.rsplit("/", 1)[1]))
                )
                value = copy.deepcopy(row.properties)
                value["conditions"][group][field] = [identifier.upper()]
                row.properties = value
        response = await authenticate(client, tenant, app)
        if exclude:
            assert (await redeem(client, tenant, app, response)).status_code == 200
        else:
            assert response.status_code == 400, response.text
            assert response.json()["error"] == "access_denied"


async def test_existing_uppercase_location_reference_prevents_deletion(database, settings):
    async with policy_client(database, settings) as (client, tenant, _, user, app, writer):
        response = await client.post(BASE + "/namedLocations", headers=writer, json=location_body())
        location = response.json()["id"]
        body = policy_body()
        body["conditions"]["locations"] = {"includeLocations": [location]}
        path = await add_policy(client, writer, body)
        async with database.sessions.begin() as session:
            row = await session.get(SecurityObject, (UUID(tenant), UUID(path.rsplit("/", 1)[1])))
            value = copy.deepcopy(row.properties)
            value["conditions"]["locations"]["includeLocations"] = [location.upper()]
            row.properties = value
        assert (
            await client.delete(BASE + "/namedLocations/" + location, headers=writer)
        ).status_code == 409
