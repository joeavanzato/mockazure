import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from mockazure.admin.app import create_app
from tests.integration.graph_support import graph_client, headers_for, registered_app
from tests.integration.test_graph_mutations import WRITE, user_body


async def test_batch_contract(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        items = [
            {"id": str(i), "method": "GET", "url": f"/users/{user}?$select=id"} for i in range(20)
        ]
        response = await client.post("/v1.0/$batch", headers=headers, json={"requests": items})
        assert response.status_code == 200, response.text
        responses = response.json()["responses"]
        assert len(responses) == 20
        assert {r["id"] for r in responses} == {str(i) for i in range(20)}
        assert all(r["status"] == 200 and r["body"]["id"] == str(user) for r in responses)
        mixed = await client.post(
            "/v1.0/$batch",
            headers=headers,
            json={
                "requests": [
                    {"id": "allowed", "method": "GET", "url": "/users?$top=1"},
                    {"id": "denied", "method": "GET", "url": "/groups"},
                    {"id": "missing", "method": "GET", "url": f"/users/{uuid4()}"},
                ]
            },
        )
        assert {r["id"]: r["status"] for r in mixed.json()["responses"]} == {
            "allowed": 200,
            "denied": 403,
            "missing": 404,
        }
        for bad in [
            items + [{"id": "21", "method": "GET", "url": "/users"}],
            [items[0], items[0]],
            [{"id": "x", "method": "POST", "url": "/users", "body": user_body()}],
            [{"id": "x", "method": "GET", "url": "https://graph.microsoft.com/v1.0/users"}],
            [{"id": "x", "method": "GET", "url": "/users", "dependsOn": []}],
            [{"id": "x", "method": "GET", "url": "/_mock/state"}],
            [{"id": "x", "method": "GET", "url": "/users", "headers": {"Authorization": "forged"}}],
        ]:
            rejected = await client.post("/v1.0/$batch", headers=headers, json={"requests": bad})
            assert rejected.status_code == 400, rejected.text
        oversized = await client.post(
            "/v1.0/$batch",
            headers={**headers, "Content-Type": "application/json"},
            content=b"x" * (1024 * 1024 + 1),
        )
        assert oversized.status_code == 413


async def test_shared_limit_and_batch_retry(database, settings):
    settings = settings.model_copy(
        update={"graph_directory_request_limit": 2, "graph_directory_window_seconds": 60}
    )
    now = datetime.now(UTC)
    async with graph_client(database, settings) as (first, tenant, app, _):
        second_app = create_app(settings, database)
        first._transport.app.state.clock = lambda: now
        second_app.state.clock = lambda: now
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=second_app), base_url="https://localhost:8443"
        ) as second:
            assert (await first.get("/v1.0/users", headers=headers)).status_code == 200
            assert (await second.get("/v1.0/users", headers=headers)).status_code == 200
            denied = await first.get("/v1.0/users", headers=headers)
            assert denied.status_code == 429, denied.text
            assert int(denied.headers["Retry-After"]) > 0
            assert (await second.get("/v1.0/groups", headers=headers)).status_code == 200
            other = (
                await first.post(
                    "/_mock/tenants",
                    json={
                        "slug": "other",
                        "display_name": "Other",
                        "domain": "other.test",
                        "licences": [],
                    },
                )
            ).json()["id"]
            other_headers = await headers_for(
                database, settings, other, await registered_app(first, other), None, WRITE
            )
            assert (await second.get("/v1.0/users", headers=other_headers)).status_code == 200
            now += timedelta(seconds=61)
            assert (await second.get("/v1.0/users", headers=headers)).status_code == 200
            mixed = await first.post(
                "/v1.0/$batch",
                headers=headers,
                json={
                    "requests": [
                        {"id": "one", "method": "GET", "url": "/users"},
                        {"id": "two", "method": "GET", "url": "/users"},
                    ]
                },
            )
            assert mixed.status_code == 200
            assert [r["status"] for r in mixed.json()["responses"]] == [200, 429]


async def test_fault_scope_and_no_partial_write(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        # There are no implicit faults.
        assert (await client.get("/v1.0/users", headers=headers)).status_code == 200
        path = f"/_mock/tenants/{tenant}/graph/faults"
        payload = {
            "client_id": str(app),
            "resource": "users",
            "kind": "next_read_429",
            "retry_after": 1,
        }
        assert (await client.post(path, headers=headers, json=payload)).status_code == 401
        installed = await client.post(path, json=payload)
        assert installed.status_code == 201, installed.text
        assert (await client.post(path, json=payload)).status_code == 409
        assert (await client.get("/v1.0/groups", headers=headers)).status_code == 200
        # Writes do not consume an explicitly read-only fault.
        created = await client.post("/v1.0/users", headers=headers, json=user_body("faultsafe"))
        assert created.status_code == 201, created.text
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url="https://localhost:8443",
        ) as restarted:
            results = await asyncio.gather(
                *(target.get("/v1.0/users", headers=headers) for target in (client, restarted))
            )
            assert sorted(r.status_code for r in results) == [200, 429]
            denied = next(r for r in results if r.status_code == 429)
            assert denied.headers["Retry-After"] == "1"
            response = await restarted.get(f"/v1.0/users/{created.json()['id']}", headers=headers)
            assert response.status_code == 200 and response.json()["displayName"] == "faultsafe"
