import asyncio
import json
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from sqlalchemy import func, select

from mockazure.directory.models import DirectoryRelation, EvidenceOutbox, ObjectVersion
from mockazure.identity.models import User
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.identity_support import identity_client
from tests.integration.test_identity_authorization import CALLBACK, PASSWORD, VERIFIER, sign_in

WRITE = {
    "User.ReadWrite.All",
    "User.Read.All",
    "User.EnableDisableAccount.All",
    "Group.ReadWrite.All",
    "Directory.Read.All",
}


def user_body(name="new"):
    return {
        "displayName": name,
        "userPrincipalName": name + "@contoso.test",
        "mailNickname": name,
        "accountEnabled": True,
        "passwordProfile": {"password": PASSWORD, "forceChangePasswordNextSignIn": False},
    }


def group_body(name="Group"):
    return {
        "displayName": name,
        "mailNickname": name.lower(),
        "mailEnabled": False,
        "securityEnabled": True,
        "groupTypes": [],
    }


async def counts(database):
    async with database.sessions() as session:
        return tuple(
            [
                await session.scalar(select(func.count()).select_from(model))
                for model in (User, ObjectVersion, EvidenceOutbox, DirectoryRelation)
            ]
        )


async def test_user_lifecycle_auth_effects(database, settings):
    async with identity_client(database, settings) as (client, tenant, app, *_):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        created = await client.post("/v1.0/users", headers=headers, json=user_body("reader"))
        assert created.status_code == 201, created.text
        uid = created.json()["id"]
        assert "password" not in created.text.lower()
        public_app = (
            await client.post(
                f"/_mock/tenants/{tenant}/identity/applications",
                json={
                    "display_name": "<script>Client</script>",
                    "client_mode": "public",
                    "redirect_uris": [CALLBACK],
                    "required_permissions": [
                        {"resource": "graph", "kind": "delegated", "permissions": ["User.Read"]}
                    ],
                },
            )
        ).json()
        callback = await sign_in(client, tenant, public_app, scope="User.Read offline_access")
        assert callback.status_code == 303, callback.text
        tokens = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "authorization_code",
                "client_id": public_app["client_id"],
                "redirect_uri": CALLBACK,
                "code_verifier": VERIFIER,
                "code": parse_qs(urlsplit(callback.headers["location"]).query)["code"][0],
            },
        )
        assert tokens.status_code == 200, tokens.text
        endpoint = f"/v1.0/users/{uid}"
        assert (
            await client.patch(
                endpoint, headers=headers, json={"displayName": "Changed", "department": "Research"}
            )
        ).status_code == 204
        got = await client.get(endpoint + "?$select=id,displayName,department", headers=headers)
        assert got.json()["displayName"] == "Changed" and got.json()["department"] == "Research"
        assert (
            await client.patch(endpoint, headers=headers, json={"accountEnabled": False})
        ).status_code == 204
        refresh = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "client_id": public_app["client_id"],
                "refresh_token": tokens.json()["refresh_token"],
            },
        )
        assert refresh.status_code == 400
        assert (
            await client.get(
                "/v1.0/me", headers={"Authorization": "Bearer " + tokens.json()["access_token"]}
            )
        ).status_code == 403
        # Re-enable then delete: deleted state must also survive an attempted admin re-enable.
        assert (
            await client.patch(endpoint, headers=headers, json={"accountEnabled": True})
        ).status_code == 204
        assert (await client.delete(endpoint, headers=headers)).status_code == 204
        assert (await client.get(endpoint, headers=headers)).status_code == 404
        assert (
            await client.patch(
                f"/_mock/tenants/{tenant}/identity/users/{uid}", json={"enabled": True}
            )
        ).status_code == 404
        async with database.sessions() as session:
            row = await session.get(User, UUID(uid))
            assert row.deleted_at and not row.enabled
            outbox = list(await session.scalars(select(EvidenceOutbox.facts)))
            assert PASSWORD not in json.dumps(outbox)
            assert "passwordProfile" not in json.dumps(outbox)
            assert any("Changed" in json.dumps(fact) for fact in outbox)


async def test_mutation_atomicity_and_references(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        initial = await counts(database)
        for body in [
            {**user_body(), "userPrincipalName": "new@foreign.test"},
            {
                **user_body(),
                "passwordProfile": {"password": "short", "forceChangePasswordNextSignIn": False},
            },
        ]:
            assert (await client.post("/v1.0/users", headers=headers, json=body)).status_code == 400
            assert await counts(database) == initial
        created = await client.post("/v1.0/groups", headers=headers, json=group_body())
        assert created.status_code == 201, created.text
        group = created.json()["id"]
        path = f"/v1.0/groups/{group}/members/$ref"
        initial = await counts(database)
        other = (
            await client.post(
                "/_mock/tenants",
                json={
                    "slug": "other",
                    "display_name": "Other",
                    "domain": "other.test",
                    "licences": ["P1"],
                },
            )
        ).json()
        foreign = await client.post(
            f"/_mock/tenants/{other['id']}/identity/users",
            json={"upn": "foreign@other.test", "display_name": "Foreign", "password": PASSWORD},
        )
        assert foreign.status_code == 201, foreign.text
        initial = await counts(database)
        # A nonexisting (including foreign) target cannot create an edge.
        for ref in [
            f"https://graph.microsoft.com/v1.0/directoryObjects/{user}",
            f"https://localhost:8443/v1.0/directoryObjects/{uuid4()}",
            f"https://localhost:8443/v1.0/directoryObjects/{foreign.json()['id']}",
            f"https://localhost:8443/v1.0/directoryObjects/{user}?x=1",
            "file:///C:/secret",
            f"/v1.0/users/{user}",
        ]:
            response = await client.post(path, headers=headers, json={"@odata.id": ref})
            assert response.status_code in (400, 404), response.text
            assert await counts(database) == initial
        assert other  # administrative fixture creation does not change directory counts
        ref = {"@odata.id": f"https://localhost:8443/v1.0/directoryObjects/{user}"}
        results = await asyncio.gather(
            *(client.post(path, headers=headers, json=ref) for _ in range(2))
        )
        assert sorted(r.status_code for r in results) == [204, 400], [r.text for r in results]
        after = await counts(database)
        assert after[-1] == initial[-1] + 1 and after[-2] == initial[-2] + 1
        assert (
            await client.delete(f"/v1.0/groups/{group}/members/{user}/$ref", headers=headers)
        ).status_code == 204
        assert (await client.get(f"/v1.0/users/{user}", headers=headers)).status_code == 200
        assert (await client.get(f"/v1.0/groups/{group}/members", headers=headers)).json()[
            "value"
        ] == []


async def test_bounded_write_profile_and_group_bindings(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        before = await counts(database)
        unsupported = [
            {
                **user_body(),
                "passwordProfile": {"password": PASSWORD, "forceChangePasswordNextSignIn": True},
            },
            {**user_body(), "unknown": "value"},
        ]
        for body in unsupported:
            assert (await client.post("/v1.0/users", headers=headers, json=body)).status_code == 400
        for extra in [
            {"groupTypes": ["Unified"]},
            {"isAssignableToRole": True},
            {"groupTypes": ["DynamicMembership"]},
            {"unknown": True},
            {"members@odata.bind": [f"https://localhost:8443/v1.0/users/{user}"] * 21},
        ]:
            assert (
                await client.post("/v1.0/groups", headers=headers, json={**group_body(), **extra})
            ).status_code == 400
        assert await counts(database) == before
        assigned = await client.put(
            f"/_mock/tenants/{tenant}/identity/users/{user}/roles/User Administrator"
        )
        assert assigned.status_code == 200, assigned.text
        privileged = await client.patch(
            f"/v1.0/users/{user}", headers=headers, json={"displayName": "Privileged"}
        )
        assert privileged.status_code == 400
        create_only = await headers_for(database, settings, tenant, app, None, {"Group.Create"})
        bound = {**group_body(), "owners@odata.bind": [f"https://localhost:8443/v1.0/users/{user}"]}
        assert (
            await client.post("/v1.0/groups", headers=create_only, json=bound)
        ).status_code == 403
        allowed = await headers_for(
            database, settings, tenant, app, None, {"Group.Create", "User.Read.All"}
        )
        created = await client.post("/v1.0/groups", headers=allowed, json=bound)
        assert created.status_code == 201, created.text
        owners = await client.get(f"/v1.0/groups/{created.json()['id']}/owners", headers=headers)
        assert [x["id"] for x in owners.json()["value"]] == [str(user)]


async def test_delegated_owner_and_write_permissions(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        owner = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"Group.ReadWrite.All", "User.Read.All"},
            "delegated",
        )
        created = await client.post("/v1.0/groups", headers=owner, json=group_body())
        assert created.status_code == 201, created.text
        group = created.json()["id"]
        owners = await client.get(f"/v1.0/groups/{group}/owners", headers=owner)
        assert [r["id"] for r in owners.json()["value"]] == [str(user)]
        assert (
            await client.patch(
                f"/v1.0/groups/{group}", headers=owner, json={"description": "Owned"}
            )
        ).status_code == 204
        denied = await headers_for(database, settings, tenant, app, None, {"User.ReadWrite.All"})
        assert (
            await client.patch(
                f"/v1.0/users/{user}", headers=denied, json={"accountEnabled": False}
            )
        ).status_code == 403
        assert (
            await client.patch(f"/v1.0/groups/{group}", headers=denied, json={})
        ).status_code in (400, 403)


async def test_graph_write_body_and_reference_boundaries(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        group = (await client.post("/v1.0/groups", headers=headers, json=group_body())).json()["id"]
        before = await counts(database)
        bad = await client.post(
            f"/v1.0/groups/{group}/members/$ref",
            headers=headers,
            json={"@odata.id": f"https://localhost:8443/v1.0/groups/{user}"},
        )
        assert bad.status_code == 400
        nul = await client.patch(
            f"/v1.0/users/{user}",
            headers=headers,
            json={"userPrincipalName": "nul\x00@contoso.test"},
        )
        assert nul.status_code == 400
        for body in [
            '{"displayName":"One","displayName":"Two"}',
            '{"displayName":NaN}',
            '{"displayName":' + "[" * 20 + "0" + "]" * 20 + "}",
        ]:
            bad = await client.patch(
                f"/v1.0/users/{user}",
                headers={**headers, "Content-Type": "application/json"},
                content=body,
            )
            assert bad.status_code == 400
        assert await counts(database) == before
        added = await client.post(
            f"/v1.0/groups/{group}/members/$ref",
            headers=headers,
            json={"@odata.id": f"https://localhost:8443/v1.0/users/{user}"},
        )
        assert added.status_code == 204
        deleted = await client.delete(f"/v1.0/users/{user}", headers=headers)
        assert deleted.status_code == 204
        async with database.sessions() as session:
            fact = await session.scalar(
                select(EvidenceOutbox.facts)
                .where(EvidenceOutbox.object_id == user)
                .order_by(EvidenceOutbox.revision.desc())
            )
            assert fact["relationships"] == [
                {"group_id": group, "object_id": str(user), "relation": "member", "removed": True}
            ]
            assert not list(await session.scalars(select(DirectoryRelation)))


async def test_patch_rejects_properties_outside_approved_profile(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        before = await counts(database)
        for fields in [
            {"userPrincipalName": "renamed@contoso.test"},
            {"mobilePhone": "12345"},
            {"businessPhones": ["12345"]},
        ]:
            response = await client.patch(f"/v1.0/users/{user}", headers=headers, json=fields)
            assert response.status_code == 400, response.text
            assert await counts(database) == before
