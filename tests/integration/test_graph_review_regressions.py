from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_datasets import dataset
from tests.integration.test_graph_mutations import WRITE, counts


@pytest.mark.parametrize("surface", ["expand", "delta"])
async def test_deleted_hidden_group_keeps_snapshot_members_protected(database, settings, surface):
    async with graph_client(database, settings) as (client, tenant, app, _):
        data = dataset()
        hidden = data["groups"][0]
        hidden["id"] = str(UUID(int=2**128 - 2))
        hidden["displayName"] = "ZZZ Hidden"
        hidden["visibility"] = "HiddenMembership"
        data["relationships"][0]["group_id"] = hidden["id"]
        public_count = 1 if surface == "expand" else 100
        data["groups"] = [
            {
                **hidden,
                "id": str(UUID(int=index + 10)),
                "displayName": f"Public {index:03}",
                "mailNickname": f"public{index}",
                "visibility": "Public",
            }
            for index in range(public_count)
        ] + [hidden]
        imported = await client.post(f"/_mock/tenants/{tenant}/directory/import", json=data)
        assert imported.status_code == 201, imported.text
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        privileged = await headers_for(
            database, settings, tenant, app, None, WRITE | {"Member.Read.Hidden"}
        )
        path = (
            "/v1.0/groups?$orderby=displayName&$top=1&$expand=members"
            if surface == "expand"
            else "/v1.0/groups/delta"
        )
        first = await client.get(path, headers=headers)
        allowed_first = await client.get(path, headers=privileged)
        assert first.status_code == allowed_first.status_code == 200
        assert hidden["id"] not in {row["id"] for row in first.json()["value"]}
        assert (
            await client.delete("/v1.0/groups/" + hidden["id"], headers=headers)
        ).status_code == 204
        denied = await client.get(first.json()["@odata.nextLink"], headers=headers)
        assert denied.status_code == 403, denied.text
        allowed = await client.get(allowed_first.json()["@odata.nextLink"], headers=privileged)
        assert allowed.status_code == 200, allowed.text
        row = next(row for row in allowed.json()["value"] if row["id"] == hidden["id"])
        assert (
            row["members" if surface == "expand" else "members@delta"][0]["id"]
            == (data["users"][0]["id"])
        )


async def test_imported_guest_rejects_provider_writes_atomically(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        data = dataset()
        guest = data["users"][0]
        guest["userType"] = "Guest"
        assert (
            await client.post(f"/_mock/tenants/{tenant}/directory/import", json=data)
        ).status_code == 201
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        before = await counts(database)
        path = "/v1.0/users/" + guest["id"]
        for values in ({"displayName": "Outside profile"}, {"accountEnabled": False}):
            response = await client.patch(path, headers=headers, json=values)
            assert response.status_code == 400, response.text
        assert (await client.delete(path, headers=headers)).status_code == 400
        assert await counts(database) == before
        current = (
            await client.get(
                path, headers=headers, params={"$select": "id,displayName,accountEnabled,userType"}
            )
        ).json()
        assert current["displayName"] == guest["displayName"] and current["accountEnabled"]
        # Trusted administrative controls retain their separate operational authority.
        assert (
            await client.patch(
                f"/_mock/tenants/{tenant}/identity/users/{guest['id']}", json={"enabled": False}
            )
        ).status_code == 200


@pytest.mark.parametrize(
    "expression",
    ["displayName eq 'a\x00b'", "accountEnabled eq " + "9" * 5000],
    ids=["nul-text", "oversized-number"],
)
@pytest.mark.parametrize("surface", ["users", "users/$count", "$batch"])
async def test_malformed_literals_are_graph_400(database, settings, expression, surface):
    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        headers["ConsistencyLevel"] = "eventual"
        query = urlencode({"$filter": expression})
        if surface == "$batch":
            response = await client.post(
                "/v1.0/$batch",
                headers=headers,
                json={
                    "requests": [
                        {
                            "id": "bad",
                            "method": "GET",
                            "url": "/users?" + query,
                            "headers": {"ConsistencyLevel": "eventual"},
                        }
                    ]
                },
            )
            assert response.status_code == 200
            item = response.json()["responses"][0]
            status, body = item["status"], item["body"]
        else:
            response = await client.get("/v1.0/" + surface + "?" + query, headers=headers)
            status, body = response.status_code, response.json()
        assert status == 400, body
        assert body["error"]["code"] == "Request_UnsupportedQuery"
        assert "innerError" in body["error"]
        assert "Traceback" not in str(body)


async def test_nullable_in_matches_equality_and_mixed_values(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        data = dataset()
        other = {
            **data["users"][0],
            "id": str(uuid4()),
            "userPrincipalName": "mail@contoso.test",
            "mailNickname": "mail",
            "mail": "analyst@contoso.test",
        }
        data["users"].append(other)
        assert (
            await client.post(f"/_mock/tenants/{tenant}/directory/import", json=data)
        ).status_code == 201
        headers = await headers_for(database, settings, tenant, app, None, WRITE)

        async def ids(expression):
            response = await client.get(
                "/v1.0/users",
                headers=headers,
                params={
                    "$filter": expression,
                    "$select": "id",
                },
            )
            assert response.status_code == 200, response.text
            return {row["id"] for row in response.json()["value"]}

        empty = await ids("mail eq null")
        assert empty
        assert await ids("mail in (null)") == empty
        assert await ids("mail in (null,'analyst@contoso.test')") == empty | {other["id"]}
        assert await ids("mail in ('analyst@contoso.test')") == {other["id"]}
