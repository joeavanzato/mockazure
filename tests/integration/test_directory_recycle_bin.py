from datetime import UTC, datetime, timedelta

from mockazure.identity.models import User
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.identity_support import identity_client
from tests.integration.test_graph_mutations import WRITE, group_body, user_body
from tests.integration.test_graph_session_revocation import login, refresh, setup_actor
from tests.integration.test_security_grants import WRITE as APP_WRITE
from tests.integration.test_security_mutations import create_operational_app, token

RECYCLE = WRITE | APP_WRITE | {"User.DeleteRestore.All"}


async def delete_user(client, headers, user_id):
    result = await client.delete(f"/v1.0/users/{user_id}", headers=headers)
    assert result.status_code == 204, result.text
    return f"/v1.0/directory/deletedItems/{user_id}"


async def test_deleted_user_is_hidden_from_live_queries_and_auth(database, settings):
    async with identity_client(database, settings) as (client, tenant, operator, *_):
        user, app = await setup_actor(client, tenant)
        tokens = await login(client, tenant, app)
        headers = await headers_for(database, settings, tenant, operator, None, RECYCLE)
        path = await delete_user(client, headers, user["id"])
        assert (await client.get(f"/v1.0/users/{user['id']}", headers=headers)).status_code == 404
        assert (await refresh(client, tenant, app, tokens)).json()["error"] == "invalid_grant"
        deleted = await client.get(path, headers=headers)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["id"] == user["id"] and deleted.json()["deletedDateTime"]
        listed = await client.get(
            "/v1.0/directory/deletedItems/microsoft.graph.user", headers=headers
        )
        assert [x["id"] for x in listed.json()["value"]] == [user["id"]]
        assert (
            await client.get("/v1.0/directory/deletedItems", headers=headers)
        ).status_code == 400
        restored = await client.post(path + "/restore", headers=headers)
        assert restored.status_code == 200, restored.text
        assert restored.json()["id"] == user["id"]
        assert (await client.get(path, headers=headers)).status_code == 404
        assert (await refresh(client, tenant, app, tokens)).json()["error"] == "invalid_grant"
        assert (await login(client, tenant, app))["access_token"]


async def test_restore_conflict_is_atomic(database, settings):
    async with graph_client(database, settings) as (client, tenant, operator, user):
        headers = await headers_for(database, settings, tenant, operator, None, RECYCLE)
        path = await delete_user(client, headers, user)
        replacement = await client.post("/v1.0/users", headers=headers, json=user_body("reader"))
        assert replacement.status_code == 201, replacement.text
        conflict = await client.post(path + "/restore", headers=headers)
        assert conflict.status_code == 409, conflict.text
        assert (await client.get(path, headers=headers)).status_code == 200
        restored = await client.post(
            path + "/restore",
            headers=headers,
            json={
                "newUserPrincipalName": "restored@contoso.test",
                "autoReconcileProxyConflict": True,
            },
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["id"] == str(user)
        assert restored.json()["userPrincipalName"] == "restored@contoso.test"
        assert (await client.get("/v1.0/users/reader@contoso.test", headers=headers)).json()[
            "id"
        ] == replacement.json()["id"]


async def test_application_restore_does_not_restore_service_principal(database, settings):
    async with graph_client(database, settings) as (client, tenant, operator, _):
        headers = await headers_for(database, settings, tenant, operator, None, RECYCLE)
        app = await create_operational_app(client, tenant, headers)
        sp = await client.get(f"/v1.0/servicePrincipals(appId='{app['appId']}')", headers=headers)
        secret = await client.post(
            f"/v1.0/applications/{app['id']}/addPassword", headers=headers, json={}
        )
        password = secret.json()["secretText"]
        assert (await token(client, tenant, app, password)).status_code == 200
        assert (
            await client.delete(f"/v1.0/applications/{app['id']}", headers=headers)
        ).status_code == 204
        root = "/v1.0/directory/deletedItems/"
        early_sp = await client.post(root + sp.json()["id"] + "/restore", headers=headers)
        assert early_sp.status_code == 409
        assert (
            await client.post(root + app["id"] + "/restore", headers=headers)
        ).status_code == 200
        assert (
            await client.get(f"/v1.0/servicePrincipals/{sp.json()['id']}", headers=headers)
        ).status_code == 404
        assert (
            await client.post(root + sp.json()["id"] + "/restore", headers=headers)
        ).status_code == 200
        assert (await token(client, tenant, app, password)).json()["error"] == "invalid_client"
        renewed = await client.post(
            f"/v1.0/applications/{app['id']}/addPassword", headers=headers, json={}
        )
        issued = await token(client, tenant, app, renewed.json()["secretText"])
        assert issued.status_code == 200
        actor = {"Authorization": "Bearer " + issued.json()["access_token"]}
        assert (await client.get("/v1.0/organization", headers=actor)).status_code == 403


async def test_restore_does_not_revive_revoked_session_or_credential(database, settings):
    async with identity_client(database, settings) as (client, tenant, operator, *_):
        user, app = await setup_actor(client, tenant)
        tokens = await login(client, tenant, app)
        own = {"Authorization": "Bearer " + tokens["access_token"]}
        assert (await client.post("/v1.0/me/revokeSignInSessions", headers=own)).status_code == 200
        admin = await headers_for(database, settings, tenant, operator, None, RECYCLE)
        path = await delete_user(client, admin, user["id"])
        assert (await client.post(path + "/restore", headers=admin)).status_code == 200
        assert (await refresh(client, tenant, app, tokens)).json()["error"] == "invalid_grant"


async def test_retention_expires_without_worker_permission_leak(database, settings):
    from mockazure.identity.deleted_models import DeletedObject

    async with graph_client(database, settings) as (client, tenant, operator, user):
        headers = await headers_for(database, settings, tenant, operator, None, RECYCLE)
        path = await delete_user(client, headers, user)
        async with database.sessions.begin() as session:
            row = await session.get(DeletedObject, (tenant, user))
            row.deleted_at = datetime.now(UTC) - timedelta(days=31)
        assert (await client.get(path, headers=headers)).status_code == 404
        assert (await client.post(path + "/restore", headers=headers)).status_code == 404
        assert (await client.get(f"/v1.0/users/{user}", headers=headers)).status_code == 404
        listed = await client.get(
            "/v1.0/directory/deletedItems/microsoft.graph.user", headers=headers
        )
        assert listed.json()["value"] == []


async def test_unsupported_group_type_is_not_restorable(database, settings):
    async with graph_client(database, settings) as (client, tenant, operator, _):
        headers = await headers_for(database, settings, tenant, operator, None, RECYCLE)
        group = await client.post("/v1.0/groups", headers=headers, json=group_body())
        gid = group.json()["id"]
        assert (await client.delete(f"/v1.0/groups/{gid}", headers=headers)).status_code == 204
        assert (
            await client.post(f"/v1.0/directory/deletedItems/{gid}/restore", headers=headers)
        ).status_code == 404


async def test_purge_and_bound_paging_do_not_resurrect_or_leak(database, settings):
    async with graph_client(database, settings) as (client, tenant, operator, user):
        headers = await headers_for(database, settings, tenant, operator, None, RECYCLE)
        path = await delete_user(client, headers, user)
        for name in ("second", "third"):
            created = await client.post("/v1.0/users", headers=headers, json=user_body(name))
            await delete_user(client, headers, created.json()["id"])
        endpoint = "/v1.0/directory/deletedItems/microsoft.graph.user"
        listed = await client.get(endpoint, headers=headers, params={"$top": 1, "$select": "id"})
        assert listed.status_code == 200, listed.text
        changed_actor = await headers_for(
            database, settings, tenant, operator, None, {"User.Read.All"}
        )
        assert (
            await client.get(listed.json()["@odata.nextLink"], headers=changed_actor)
        ).status_code == 400
        seen = [listed.json()["value"][0]["id"]]
        while "@odata.nextLink" in listed.json():
            listed = await client.get(listed.json()["@odata.nextLink"], headers=headers)
            assert listed.status_code == 200, listed.text
            seen += [x["id"] for x in listed.json()["value"]]
        assert len(seen) == len(set(seen)) == 3
        weak = await headers_for(database, settings, tenant, operator, None, {"User.Read.All"})
        assert (await client.delete(path, headers=weak)).status_code == 403
        assert (await client.delete(path, headers=headers)).status_code == 204
        assert (await client.post(path + "/restore", headers=headers)).status_code == 404
        async with database.sessions() as session:
            retained = await session.get(User, user)
            assert retained is not None and retained.deleted_at is not None
            assert not retained.enabled
        assert (
            await client.get(endpoint + "?$filter=id eq 'x'", headers=headers)
        ).status_code == 400


async def test_restore_saved_group_owner_and_membership_once_per_revision(database, settings):
    async with graph_client(database, settings) as (client, tenant, operator, user):
        headers = await headers_for(database, settings, tenant, operator, None, RECYCLE)
        reference = settings.endpoints.graph + f"/v1.0/users/{user}"
        group = await client.post(
            "/v1.0/groups",
            headers=headers,
            json={
                **group_body(),
                "owners@odata.bind": [reference],
                "members@odata.bind": [reference],
            },
        )
        assert group.status_code == 201, group.text
        path = await delete_user(client, headers, user)
        restored = await client.post(path + "/restore", headers=headers)
        assert restored.status_code == 200, restored.text
        for relation in ("owners", "members"):
            response = await client.get(
                f"/v1.0/groups/{group.json()['id']}/{relation}", headers=headers
            )
            assert [x["id"] for x in response.json()["value"]] == [str(user)]


async def test_application_restore_cannot_redeem_predeletion_authorization(database, settings):
    from urllib.parse import parse_qs, urlsplit
    from uuid import uuid4

    from tests.integration.test_identity_authorization import CALLBACK, VERIFIER, sign_in
    from tests.integration.test_security_grants import sp_ids

    async with identity_client(database, settings) as (client, tenant, operator, *_):
        user, app = await setup_actor(client, tenant)
        pending = await sign_in(
            client, tenant, app, scope="User.Read User.RevokeSessions.All offline_access"
        )
        code = parse_qs(urlsplit(pending.headers["location"]).query)["code"][0]
        headers = await headers_for(database, settings, tenant, operator, None, RECYCLE)
        registration = await client.get(
            f"/v1.0/applications(appId='{app['client_id']}')", headers=headers
        )
        _, sp = await sp_ids(client, headers, app["client_id"])
        assert (
            await client.delete(f"/v1.0/applications/{registration.json()['id']}", headers=headers)
        ).status_code == 204
        for identifier in (registration.json()["id"], sp):
            restored = await client.post(
                f"/v1.0/directory/deletedItems/{identifier}/restore", headers=headers
            )
            assert restored.status_code == 200, restored.text
        granted = await client.put(
            f"/_mock/tenants/{tenant}/identity/grants/{uuid4()}",
            json={
                "client_id": app["client_id"],
                "resource": "graph",
                "kind": "delegated",
                "permissions": ["User.Read", "User.RevokeSessions.All"],
            },
        )
        assert granted.status_code == 200
        response = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "authorization_code",
                "client_id": app["client_id"],
                "redirect_uri": CALLBACK,
                "code": code,
                "code_verifier": VERIFIER,
            },
        )
        assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


async def test_restore_proxy_conflict_requires_explicit_reconciliation(database, settings):
    from uuid import UUID

    async with graph_client(database, settings) as (client, tenant, operator, user):
        headers = await headers_for(database, settings, tenant, operator, None, RECYCLE)
        other = await client.post("/v1.0/users", headers=headers, json=user_body("other"))
        async with database.sessions.begin() as session:
            for uid in (user, UUID(other.json()["id"])):
                row = await session.get(User, uid)
                row.profile = {**row.profile, "proxyAddresses": ["smtp:shared@contoso.test"]}
        path = await delete_user(client, headers, user)
        assert (await client.post(path + "/restore", headers=headers)).status_code == 409
        restored = await client.post(
            path + "/restore", headers=headers, json={"autoReconcileProxyConflict": True}
        )
        assert restored.status_code == 200, restored.text
        async with database.sessions() as session:
            assert (await session.get(User, user)).profile["proxyAddresses"] == []


async def test_ownedby_recovery_uses_saved_owner_and_rechecks_current_actor(database, settings):
    from tests.integration.graph_support import registered_app

    async with graph_client(database, settings) as (client, tenant, operator, _):
        headers = await headers_for(database, settings, tenant, operator, None, RECYCLE)
        app = await create_operational_app(client, tenant, headers)
        assert (
            await client.delete(f"/v1.0/applications/{app['id']}", headers=headers)
        ).status_code == 204
        path = f"/v1.0/directory/deletedItems/{app['id']}/restore"
        stranger = await registered_app(client, tenant)
        other = await headers_for(
            database, settings, tenant, stranger, None, {"Application.ReadWrite.OwnedBy"}
        )
        assert (await client.post(path, headers=other)).status_code == 403
        owner = await headers_for(
            database, settings, tenant, operator, None, {"Application.ReadWrite.OwnedBy"}
        )
        restored = await client.post(path, headers=owner)
        assert restored.status_code == 200, restored.text
