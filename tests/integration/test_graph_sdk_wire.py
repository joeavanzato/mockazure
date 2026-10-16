from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_mutations import WRITE, group_body, user_body


async def test_generated_sdk_types_and_delta_function_routes(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        for resource, kind, body in (
            ("users", "user", user_body("sdk-wire")),
            ("groups", "group", group_body()),
        ):
            body["@odata.type"] = "#microsoft.graph." + kind
            response = await client.post("/v1.0/" + resource, headers=headers, json=body)
            assert response.status_code == 201, response.text
            identifier = response.json()["id"]
            assert (
                await client.patch(
                    f"/v1.0/{resource}/{identifier}",
                    headers=headers,
                    json={"@odata.type": "#microsoft.graph." + kind, "displayName": "SDK update"},
                )
            ).status_code == 204
            assert (
                await client.patch(
                    f"/v1.0/{resource}/{identifier}",
                    headers=headers,
                    json={"@odata.type": "#microsoft.graph.device", "displayName": "wrong"},
                )
            ).status_code == 400
            for version in ("v1.0", "beta"):
                for alias in ("delta()", "microsoft.graph.delta()"):
                    page = await client.get(f"/{version}/{resource}/{alias}", headers=headers)
                    assert page.status_code == 200, page.text
                    assert any(row["id"] == identifier for row in page.json()["value"])
