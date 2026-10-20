from mockazure.population.catalog import get_adapter
from mockazure.population.transport import LocalTransport
from mockazure.population.types import AdapterContext, Mutation
from tests.integration.test_graph_hunting import hunting_client


async def test_hunting_module_is_available_before_console_login(database, settings):
    from tests.integration.test_console_auth import browser

    async with browser(database, settings) as client:
        response = await client.get("/_mock/ui/hunting.js")
        assert response.status_code == 200, response.text
        assert "export async function hunting" in response.text


async def test_console_query_requires_named_authorized_identity(database, settings, monkeypatch):
    from mockazure.admin import hunting_routes

    async with hunting_client(database, settings) as (client, tid, app, user, headers):
        monkeypatch.setattr(
            hunting_routes,
            "LocalTransport",
            lambda ctx, identity: LocalTransport(ctx, identity, client=client),
        )
        ctx = AdapterContext(database, settings, tid)
        for name, permissions in (
            ("allowed", ["ThreatHunting.Read.All"]),
            ("denied", ["User.Read.All"]),
        ):
            await get_adapter("identity.clients").apply(
                ctx,
                [
                    Mutation(
                        operation="provision",
                        logical_key=name,
                        payload={
                            "id": name,
                            "display_name": name,
                            "permissions": [
                                {
                                    "resource": "graph",
                                    "kind": "application",
                                    "permissions": permissions,
                                }
                            ],
                        },
                    )
                ],
            )
        path = f"/_mock/tenants/{tid}/hunting/query"
        body = {"request": {"Query": "DeviceInfo | project DeviceId"}}
        assert (await client.post(path, json=body)).status_code == 422
        denied = await client.post(path, json=body | {"identity_ref": "denied"})
        assert denied.status_code == 403, denied.text
        success = await client.post(path, json=body | {"identity_ref": "allowed"})
        assert success.status_code == 200, success.text
        assert len(success.json()["graph"]["results"]) == 2
        assert success.json()["reference_time"].startswith("2026-10-07T12:00:00")
        assert success.json()["identity_ref"] == "allowed"
        assert "access_token" not in success.text
        examples = await client.get(f"/_mock/tenants/{tid}/hunting/cookbook")
        assert examples.status_code == 200 and len(examples.json()["cases"]) >= 24
        incident = await client.patch(
            "/v1.0/security/incidents/missing", headers=headers, json={"status": "resolved"}
        )
        assert incident.status_code in {404, 405, 501}
        from mockazure.admin.errors import AdminError

        class RejectedIdentity:
            async def request(self, *args, **kwargs):
                raise AdminError(401, "LocalIdentityRejected", "Local identity is disabled")

        monkeypatch.setattr(hunting_routes, "LocalTransport", lambda *args: RejectedIdentity())
        rejected = await client.post(path, json=body | {"identity_ref": "allowed"})
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "HuntingIdentityRejected"
