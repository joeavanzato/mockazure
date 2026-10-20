"""Collect local lifecycle outcomes through original Graph/Azure clients."""

import asyncio
import re
import secrets
import ssl
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.invitation import Invitation
from msgraph.generated.models.password_profile import PasswordProfile
from msgraph.generated.models.user import User
from msgraph.generated.users.item.user_item_request_builder import UserItemRequestBuilder
from pydantic import SecretStr
from sqlalchemy import select

from integrations.python.graph_directory import graph_client
from mockazure.population.catalog import get_adapter
from mockazure.population.delegated_login import REDIRECT
from mockazure.population.delegated_login import acquire as acquire_delegated
from mockazure.population.identity import resolve_identity
from mockazure.population.transport import ClientCredential
from mockazure.population.types import Mutation
from mockazure.providers.arm.models import RoleAssignment
from tools.living_tenant_clients import acquire

CASES = {
    "joiner_mover_leaver",
    "guest_invite_accept_collect",
    "delete_restore_collect",
    "credential_rotation",
    "pim_api_access",
    "azure_rbac_removal",
}


def validate(report):
    try:
        assert report["phase"] == "identity" and report["passed"] is True
        assert report["failed_cases"] == report["unexpected_authorizations"] == []
        assert set(report["cases"]) == CASES
        assert all(c["state"] == "passed" for c in report["cases"].values())
        assert re.fullmatch("[0-9a-f]{64}", report["source_fingerprint"])
        assert set(report["subject_ids"]) <= set(report["audit_target_ids"])
        assert report["cases"]["joiner_mover_leaver"]["statuses"] == [200, 403]
        assert report["cases"]["azure_rbac_removal"]["statuses"] == [200, 403]
        assert report["cases"]["pim_api_access"]["steps"] == [
            "denied",
            "eligible-denied",
            "activated-allowed",
            "deactivated-denied",
        ]
        assert report["cases"]["credential_rotation"]["old_credential_status"] == 401
        assert report["cases"]["guest_invite_accept_collect"]["states"] == [
            "PendingAcceptance",
            "Accepted",
        ]
    except (AssertionError, KeyError, TypeError):
        raise ValueError("Incomplete living identity qualification") from None


def user_query(*fields):
    return RequestConfiguration(
        query_parameters=UserItemRequestBuilder.UserItemRequestBuilderGetQueryParameters(
            select=["id", *fields],
        )
    )


async def qualify_identity(config, settings, admin, ctx, refs):
    operator = await resolve_identity(ctx, refs["lifecycle-client"])
    token = await asyncio.to_thread(acquire, settings, ctx, operator, "https://graph.microsoft.com")
    cases = {}
    subjects = {refs["lifecycle-user"], refs["privileged-user"]}
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=str(settings.ca_file)),
        trust_env=False,
        follow_redirects=False,
        timeout=60,
    ) as http:
        graph = graph_client({"endpoints": {"graph": settings.endpoints.graph}}, token, http)
        stamp = uuid4().hex[:10]
        password = secrets.token_urlsafe(40)
        # Original generated SDK models/builders exercise a disposable joiner.
        user = await graph.users.post(
            User(
                display_name="Native lifecycle joiner " + stamp,
                account_enabled=True,
                user_principal_name=f"joiner-{stamp}@{refs['domain']}",
                mail_nickname="joiner-" + stamp,
                password_profile=PasswordProfile(
                    password=password,
                    force_change_password_next_sign_in=False,
                ),
            )
        )
        uid = user.id
        subjects.add(uid)
        base = f"/_mock/tenants/{ctx.tenant_id}/identity"
        client = await admin.request(
            "POST",
            base + "/applications",
            json={
                "display_name": "Disposable delegated probe",
                "client_mode": "confidential",
                "redirect_uris": [REDIRECT],
                "required_permissions": [
                    {"resource": "graph", "kind": "delegated", "permissions": ["User.Read"]},
                ],
            },
        )
        secret = await admin.request(
            "POST",
            base + "/applications/" + client["client_id"] + "/credentials",
            json={"kind": "secret"},
        )
        await admin.request(
            "PUT",
            base + "/grants/" + str(uuid4()),
            json={
                "client_id": client["client_id"],
                "user_id": uid,
                "resource": "graph",
                "kind": "delegated",
                "permissions": ["User.Read"],
            },
        )
        delegated = ClientCredential(
            UUID(client["client_id"]),
            SecretStr(secret["secret"]),
            username=user.user_principal_name,
            password=SecretStr(password),
            scopes=("User.Read",),
        )
        user_token = await acquire_delegated(
            http,
            settings.endpoints.login,
            ctx.tenant_id,
            delegated,
        )
        assert user_token.status_code == 200
        user_headers = {"Authorization": "Bearer " + user_token.json()["access_token"]}
        me = settings.endpoints.graph + "/v1.0/me"
        assert (await http.get(me, headers=user_headers)).status_code == 200
        builder = graph.users.by_user_id(uid)
        await builder.patch(User(department="Investigation"))
        assert (await builder.get(user_query("department"))).department == "Investigation"
        await builder.patch(User(account_enabled=False))
        denied = await http.get(me, headers=user_headers)
        assert denied.status_code == 403
        cases["joiner_mover_leaver"] = {
            "state": "passed",
            "client": "msgraph-sdk",
            "statuses": [200, denied.status_code],
            "user_id": uid,
            "local_pkce": True,
        }
        await builder.delete()
        deleted = await http.get(
            settings.endpoints.graph + "/v1.0/directory/deletedItems/" + uid,
            headers={"Authorization": "Bearer " + token},
        )
        assert deleted.status_code == 200 and deleted.json()["id"] == uid
        # Generated restore action in the original SDK.
        restored = await graph.directory.deleted_items.by_directory_object_id(uid).restore.post()
        assert restored.id == uid
        after = await builder.get(user_query("accountEnabled", "department"))
        assert after.id == uid and after.account_enabled is False
        assert (await http.get(me, headers=user_headers)).status_code == 403
        cases["delete_restore_collect"] = {
            "state": "passed",
            "user_id": uid,
            "restored_id": restored.id,
            "old_session_status": 403,
            "restored_enabled": False,
        }
        invitation = await graph.invitations.post(
            Invitation(
                invited_user_email_address="guest-" + stamp + "@external.test",
                invite_redirect_url=settings.endpoints.browser_login + "/_mock/ui/",
                send_invitation_message=True,
            )
        )
        gid = invitation.invited_user.id
        subjects.add(gid)
        guest = graph.users.by_user_id(gid)
        before = await guest.get(user_query("externalUserState"))
        assert before.external_user_state == "PendingAcceptance"
        # Redemption is deliberately an authenticated local control-plane action.
        await admin.request(
            "POST",
            f"/_mock/tenants/{ctx.tenant_id}/guests/{invitation.id}/redeem",
        )
        after = await guest.get(user_query("externalUserState"))
        assert after.external_user_state == "Accepted"
        cases["guest_invite_accept_collect"] = {
            "state": "passed",
            "user_id": gid,
            "states": [before.external_user_state, after.external_user_state],
            "delivery": "local-only",
        }
        cases["credential_rotation"] = await rotate_secret(
            http,
            settings,
            ctx,
            admin,
            token,
            stamp,
        )
        # The worker must project the actual source events; never fabricate an audit.
        audits = []
        for _ in range(60):
            page = await graph.audit_logs.directory_audits.get()
            audits = list(page.value or [])
            while page.odata_next_link:
                page = await graph.audit_logs.directory_audits.with_url(page.odata_next_link).get()
                audits.extend(page.value or [])
                assert len(audits) < 10000
            target_ids = {t.id for a in audits for t in a.target_resources or []}
            if subjects <= target_ids:
                break
            await asyncio.sleep(1)
        assert subjects <= target_ids
        labels = {
            a.activity_display_name
            for a in audits
            if any(t.id == uid for t in a.target_resources or [])
        }
        assert {"Add user", "Delete user", "Restore user"} <= labels
    cases["azure_rbac_removal"] = await azure_removal(settings, ctx, refs)
    from tools.verify_living_pim import qualify

    pim = await qualify(config, Path("work/living-identity-pim.json"))
    assert pim["state"] == "passed"
    cases["pim_api_access"] = pim
    return {
        "cases": cases,
        "subject_ids": sorted(subjects),
        "audit_target_ids": sorted(target_ids),
        "audit_ids": [a.id for a in audits],
        "failed_cases": [],
        "unexpected_authorizations": [],
        "clients": {
            "msgraph-sdk": version("msgraph-sdk"),
            "msal": version("msal"),
            "azure-storage-blob": version("azure-storage-blob"),
            "azure-mgmt-authorization": version("azure-mgmt-authorization"),
        },
    }


async def rotate_secret(http, settings, ctx, admin, token, stamp):
    from msgraph.generated.applications.item.add_password.add_password_post_request_body import (
        AddPasswordPostRequestBody,
    )
    from msgraph.generated.applications.item.remove_password import (
        remove_password_post_request_body,
    )
    from msgraph.generated.models.password_credential import PasswordCredential

    base = f"/_mock/tenants/{ctx.tenant_id}/identity"
    application = await admin.request(
        "POST",
        base + "/applications",
        json={
            "display_name": "Disposable rotation " + stamp,
            "client_mode": "confidential",
        },
    )
    graph = graph_client({"endpoints": {"graph": settings.endpoints.graph}}, token, http)
    metadata = await http.get(
        settings.endpoints.graph + f"/v1.0/applications(appId='{application['client_id']}')",
        headers={"Authorization": "Bearer " + token},
    )
    assert metadata.status_code == 200
    object_id = metadata.json()["id"]
    builder = graph.applications.by_application_id(object_id)
    old = await builder.add_password.post(
        AddPasswordPostRequestBody(
            password_credential=PasswordCredential(display_name="before"),
        )
    )
    new = await builder.add_password.post(
        AddPasswordPostRequestBody(
            password_credential=PasswordCredential(display_name="after"),
        )
    )

    async def login(secret):
        return await http.post(
            f"{settings.endpoints.login}/{ctx.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": application["client_id"],
                "client_secret": secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )

    assert (await login(old.secret_text)).status_code == 200
    assert (await login(new.secret_text)).status_code == 200
    await builder.remove_password.post(
        remove_password_post_request_body.RemovePasswordPostRequestBody(key_id=old.key_id)
    )
    rejected = await login(old.secret_text)
    assert rejected.status_code == 401 and rejected.json()["error"] == "invalid_client"
    assert (await login(new.secret_text)).status_code == 200
    return {
        "state": "passed",
        "application_id": object_id,
        "old_credential_status": rejected.status_code,
        "new_credential_status": 200,
        "client": "msgraph-sdk",
        "credentials_in_report": False,
    }


async def azure_removal(settings, ctx, refs):
    # Explicit setup grants management authority; the tested removal uses the original SDK.
    identity = await resolve_identity(ctx, refs["client"])
    await get_adapter("arm.roles").apply(
        ctx,
        [
            Mutation(
                operation="assign",
                logical_key="qualification-rbac-owner",
                payload={
                    "id": str(uuid4()),
                    "scope": "/subscriptions/" + refs["subscription"],
                    "role": "Owner",
                    "identity_ref": refs["client"],
                },
            )
        ],
    )
    from mockazure.providers.arm.contracts import builtin_roles

    async with ctx.database.sessions() as session:
        assignment = await session.scalar(
            select(RoleAssignment).where(
                RoleAssignment.tenant_id == ctx.tenant_id,
                RoleAssignment.client_id == identity.client_id,
                RoleAssignment.role_id == builtin_roles()["Storage Blob Data Contributor"]["id"],
            )
        )
        assert assignment is not None
        assignment_id, scope = str(assignment.id), assignment.scope_key
    arm_token = await asyncio.to_thread(
        acquire, settings, ctx, identity, "https://management.azure.com"
    )
    blob_token = await asyncio.to_thread(
        acquire, settings, ctx, identity, "https://storage.azure.com"
    )
    return await asyncio.to_thread(
        azure_clients,
        settings,
        refs,
        arm_token,
        blob_token,
        assignment_id,
        scope,
    )


def azure_clients(settings, refs, arm_token, blob_token, assignment_id, scope):
    from azure.core.exceptions import HttpResponseError
    from azure.core.pipeline.transport import RequestsTransport
    from azure.mgmt.authorization import AuthorizationManagementClient
    from azure.storage.blob import BlobServiceClient

    from integrations.python.arm import Credential, LocalSession
    from mockazure.endpoints import local_origin

    arm_origin, blob_origin = (
        local_origin(settings.endpoints.arm),
        local_origin(settings.endpoints.storage),
    )
    with (
        LocalSession(arm_origin, str(settings.ca_file)) as management,
        LocalSession(
            blob_origin,
            str(settings.ca_file),
        ) as storage,
    ):
        roles = AuthorizationManagementClient(
            Credential(arm_token),
            refs["subscription"],
            base_url=arm_origin,
            api_version="2022-04-01",
            credential_scopes=["https://management.azure.com/.default"],
            transport=RequestsTransport(
                session=management, connection_verify=str(settings.ca_file)
            ),
        )
        blobs = BlobServiceClient(
            blob_origin + "/" + refs["account"].rsplit("/", 1)[-1],
            credential=Credential(blob_token),
            api_version="2025-11-05",
            retry_total=0,
            transport=RequestsTransport(session=storage, connection_verify=str(settings.ca_file)),
        )
        try:
            blob = blobs.get_blob_client("evidence", "project-review.docx")
            assert blob.download_blob().readall().startswith(b"PK")
            roles.role_assignments.delete(scope, assignment_id)
            try:
                blob.download_blob().readall()
            except HttpResponseError as error:
                assert error.status_code == 403
            else:
                raise AssertionError("Deleted Azure data role still grants access")
            return {
                "state": "passed",
                "statuses": [200, 403],
                "assignment_id": assignment_id,
                "same_token": True,
                "clients": ["azure-mgmt-authorization", "azure-storage-blob"],
            }
        finally:
            roles.close()
            blobs.close()
