"""Current grants, signed policies and lifecycle invalidation use persisted authority."""

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from xml.etree import ElementTree

import pytest
from azure.storage.blob import ContainerSasPermissions, UserDelegationKey, generate_container_sas
from sqlalchemy import delete, select
from starlette.requests import Request

from mockazure.identity.keys import read_wrapping_key
from mockazure.identity.models import ServicePrincipal
from mockazure.providers.arm.models import RoleAssignment
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_storage_authorization import (
    ARGS,
    VERSION,
    account_sas,
    provision,
    signed,
)


def signed_body(method, url, key, query, body=b"", extra=None):
    values = signed(method, url, key, query=query) | (extra or {})
    if body:
        values["Content-Length"] = str(len(body))
        values["Content-Type"] = "application/xml"
    fields = [
        method,
        "",
        "",
        str(len(body)) if body else "",
        "",
        values.get("Content-Type", ""),
        "",
        "",
        "",
        "",
        "",
        "",
    ]
    ms = "".join(
        k.lower() + ":" + v + "\n"
        for k, v in sorted(values.items(), key=lambda p: p[0].lower())
        if k.lower().startswith("x-ms-")
    )
    path = urlsplit(url).path
    text = (
        "\n".join(fields)
        + "\n"
        + ms
        + "/signedaccount"
        + path
        + "".join("\n" + k + ":" + v for k, v in sorted(query.items()))
    )
    values["Authorization"] = (
        "SharedKey signedaccount:"
        + base64.b64encode(
            hmac.new(base64.b64decode(key), text.encode(), hashlib.sha256).digest()
        ).decode()
    )
    return values


async def authorize(
    database,
    settings,
    url,
    *,
    headers=None,
    service="blob",
    action="read_blob",
    resource="evidence/item",
):
    assert __import__("pathlib").Path("src/mockazure/storage/sas.py").exists()
    from mockazure.storage.authorization import authorize_storage
    from mockazure.storage.lifecycle import lock_accounts
    from mockazure.storage.models import StorageAccount

    parsed = urlsplit(url)
    from types import SimpleNamespace

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": parsed.scheme,
            "path": parsed.path,
            "raw_path": parsed.path.encode(),
            "query_string": parsed.query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": ("127.0.0.1", 12345),
            "server": (parsed.hostname, parsed.port),
            "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
        }
    )
    async with database.sessions.begin() as session:
        session.info["storage_cipher"] = read_wrapping_key(settings.data_dir)
        tenant = await session.scalar(
            select(StorageAccount.tenant_id).where(StorageAccount.name == "signedaccount")
        )
        await lock_accounts(session, tenant)
        return await authorize_storage(
            session, request, service, action, resource, datetime.now(UTC)
        )


async def test_storage_oauth_current_data_grants_and_files_intent(database, settings):
    from mockazure.providers.storage.errors import StorageError
    from tests.integration.test_arm_authorization import assign

    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        oauth = await headers_for(
            database, settings, tenant, app, None, [], audience="https://storage.azure.com/"
        )
        response = await client.put(
            endpoints["blob"] + "evidence",
            params={"restype": "container"},
            headers=oauth | {"x-ms-version": VERSION},
        )
        assert response.status_code == 201, response.text
        with pytest.raises(StorageError) as denied:
            await authorize(database, settings, endpoints["blob"] + "evidence/item", headers=oauth)
        assert denied.value.status == 403
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(ServicePrincipal.client_id == app)
            )
        base = f"/_mock/tenants/{tenant}/subscriptions/{path.split('/')[2]}"
        await assign(client, base, sp, path, "Storage Blob Data Reader")
        access = await authorize(
            database, settings, endpoints["blob"] + "evidence/item", headers=oauth
        )
        assert access.mode == "oauth" and access.principal.client_id == app
        with pytest.raises(StorageError):
            await authorize(
                database,
                settings,
                endpoints["file"] + "evidence/item",
                headers=oauth,
                service="file",
                action="read_file",
            )
        await assign(client, base, sp, path, "Storage File Data Privileged Reader")
        with pytest.raises(StorageError):
            await authorize(
                database,
                settings,
                endpoints["file"] + "evidence/item",
                headers=oauth,
                service="file",
                action="read_file",
            )
        file_access = await authorize(
            database,
            settings,
            endpoints["file"] + "evidence/item",
            headers=oauth | {"x-ms-file-request-intent": "backup"},
            service="file",
            action="read_file",
        )
        assert file_access.principal.client_id == app
        async with database.sessions.begin() as session:
            await session.execute(
                delete(RoleAssignment).where(
                    RoleAssignment.tenant_id == tenant,
                    RoleAssignment.role_id == "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",
                )
            )
        with pytest.raises(StorageError):
            await authorize(database, settings, endpoints["blob"] + "evidence/item", headers=oauth)


async def test_stored_policy_service_sas_public_access_and_restore(database, settings):
    from mockazure.providers.storage.errors import StorageError

    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["blob"] + "evidence"
        q = {"restype": "container"}
        assert (
            await client.put(url, params=q, headers=signed("PUT", url, key, query=q))
        ).status_code == 201
        expiry = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        policy = (
            "<SignedIdentifiers><SignedIdentifier><Id>reader</Id><AccessPolicy>"
            f"<Expiry>{expiry}</Expiry><Permission>r</Permission>"
            "</AccessPolicy></SignedIdentifier></SignedIdentifiers>"
        ).encode()
        acl = q | {"comp": "acl"}
        result = await client.put(
            url, params=acl, content=policy, headers=signed_body("PUT", url, key, acl, policy)
        )
        assert result.status_code == 200, result.text
        sas = generate_container_sas(
            "signedaccount", "evidence", account_key=key, policy_id="reader"
        )
        access = await authorize(database, settings, url + "/item?" + sas)
        assert access.mode == "service_sas" and access.principal is None
        with pytest.raises(StorageError):
            await authorize(
                database,
                settings,
                endpoints["blob"] + "another/item?" + sas,
                resource="another/item",
            )
        with pytest.raises(StorageError):
            await authorize(database, settings, url + "/item?" + sas, action="write_blob")
        assert (
            await client.patch(
                path, params=ARGS, headers=arm, json={"properties": {"allowBlobPublicAccess": True}}
            )
        ).status_code == 200
        result = await client.put(
            url,
            params=acl,
            content=policy,
            headers=signed_body(
                "PUT", url, key, acl, policy, {"x-ms-blob-public-access": "container"}
            ),
        )
        assert result.status_code == 200, result.text
        public = await authorize(database, settings, url + "/item")
        assert public.mode == "anonymous" and public.principal is None
        assert (
            await client.patch(
                path,
                params=ARGS,
                headers=arm,
                json={"properties": {"allowBlobPublicAccess": False}},
            )
        ).status_code == 200
        with pytest.raises(StorageError):
            await authorize(database, settings, url + "/item")
        state_url = f"/_mock/tenants/{tenant}/cloud/storageState"
        exported = await client.get(state_url)
        state = exported.json()
        assert state["accounts"][0]["namespaces"][0]["name"] == "evidence"
        assert key not in exported.text and "sig=" not in exported.text
        assert (await client.put(state_url, json=state)).status_code == 200
        with pytest.raises(StorageError):
            await authorize(database, settings, url + "/item?" + sas)
        new_key = (await client.post(path + "/listKeys", params=ARGS, headers=arm)).json()["keys"][
            0
        ]["value"]
        current_sas = generate_container_sas(
            "signedaccount", "evidence", account_key=new_key, policy_id="reader"
        )
        assert (
            await authorize(database, settings, url + "/item?" + current_sas)
        ).mode == "service_sas"
        empty = b"<SignedIdentifiers />"
        result = await client.put(
            url, params=acl, content=empty, headers=signed_body("PUT", url, new_key, acl, empty)
        )
        assert result.status_code == 200, result.text
        with pytest.raises(StorageError):
            await authorize(database, settings, url + "/item?" + current_sas)


@pytest.mark.parametrize("fractional", [False, True])
async def test_user_delegation_key_sas_grant_and_restore_revocation(database, settings, fractional):
    from mockazure.providers.storage.errors import StorageError
    from tests.integration.test_arm_authorization import assign

    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        now = datetime.now(UTC)
        start = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if fractional:
            start = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
            end = (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        oauth = await headers_for(
            database, settings, tenant, app, None, [], audience="https://storage.azure.com/"
        )
        q = {"restype": "container"}
        url = endpoints["blob"] + "evidence"
        assert (
            await client.put(url, params=q, headers=signed("PUT", url, key, query=q))
        ).status_code == 201
        data = f"<KeyInfo><Start>{start}</Start><Expiry>{end}</Expiry></KeyInfo>".encode()
        result = await client.post(
            endpoints["blob"],
            params={"restype": "service", "comp": "userdelegationkey"},
            content=data,
            headers=oauth | {"x-ms-version": VERSION, "Content-Type": "application/xml"},
        )
        assert result.status_code == 200, result.text
        xml = ElementTree.fromstring(result.content)
        delegation = UserDelegationKey()
        for field, tag in [
            ("signed_oid", "SignedOid"),
            ("signed_tid", "SignedTid"),
            ("signed_start", "SignedStart"),
            ("signed_expiry", "SignedExpiry"),
            ("signed_service", "SignedService"),
            ("signed_version", "SignedVersion"),
            ("value", "Value"),
        ]:
            setattr(delegation, field, xml.findtext(tag))
        assert delegation.signed_oid and delegation.value
        sas = generate_container_sas(
            "signedaccount",
            "evidence",
            user_delegation_key=delegation,
            permission=ContainerSasPermissions(read=True),
            expiry=now + timedelta(minutes=15),
        )
        with pytest.raises(StorageError):
            await authorize(database, settings, url + "/item?" + sas)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(ServicePrincipal.client_id == app)
            )
        await assign(
            client,
            f"/_mock/tenants/{tenant}/subscriptions/{path.split('/')[2]}",
            sp,
            path,
            "Storage Blob Data Reader",
        )
        access = await authorize(database, settings, url + "/item?" + sas)
        assert access.mode == "user_delegation_sas" and access.principal is None
        assert (
            await client.patch(
                path, params=ARGS, headers=arm, json={"properties": {"allowSharedKeyAccess": False}}
            )
        ).status_code == 200
        assert (
            await authorize(database, settings, url + "/item?" + sas)
        ).mode == "user_delegation_sas"
        state_url = f"/_mock/tenants/{tenant}/cloud/storageState"
        exported = await client.get(state_url)
        assert delegation.value not in exported.text
        assert (await client.put(state_url, json=exported.json())).status_code == 200
        with pytest.raises(StorageError):
            await authorize(database, settings, url + "/item?" + sas)


async def test_unsupported_storage_creation_headers_do_not_succeed(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        for service, kind, extra in (
            ("file", "share", {"x-ms-enabled-protocols": "NFS"}),
            ("file", "share", {"x-ms-root-squash": "RootSquash"}),
            ("blob", "container", {"x-ms-default-encryption-scope": "unmodeled"}),
            ("blob", "container", {"x-ms-meta-invalid-name": "value"}),
        ):
            url = endpoints[service] + "rejected"
            q = {"restype": kind}
            response = await client.put(
                url, params=q, headers=signed_body("PUT", url, key, q, extra=extra)
            )
            assert response.status_code == 400, response.text
            assert (
                await client.get(url, params=q, headers=signed("GET", url, key, query=q))
            ).status_code == 404


async def test_container_acl_etag_atomicity_and_bad_xml(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["blob"] + "evidence"
        q = {"restype": "container"}
        result = await client.put(url, params=q, headers=signed("PUT", url, key, query=q))
        assert result.status_code == 201
        etag = result.headers["ETag"]
        acl = q | {"comp": "acl"}
        for content in (
            b'<!DOCTYPE SignedIdentifiers [<!ENTITY ext SYSTEM "file:///does-not-exist">]><SignedIdentifiers>&ext;</SignedIdentifiers>',
            b"<SignedIdentifiers><SignedIdentifier><Id>bad</Id><AccessPolicy><Permission>wr</Permission></AccessPolicy></SignedIdentifier></SignedIdentifiers>",
        ):
            bad = await client.put(
                url, params=acl, content=content, headers=signed_body("PUT", url, key, acl, content)
            )
            assert bad.status_code == 400, bad.text
        current = await client.get(url, params=q, headers=signed("GET", url, key, query=q))
        assert current.headers["ETag"] == etag
        # An account-SAS cannot rewrite stored access policies.
        denied = await client.put(
            url,
            params=acl | account_sas(key),
            headers={"Authorization": ""},
            content=b"<SignedIdentifiers />",
        )
        assert denied.status_code == 403


async def test_official_python_share_redundant_metadata_header(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["file"] + "metadata"
        query = {"restype": "share"}
        extra = {"x-ms-meta-writer": "python", "x-ms-meta": "{'writer': 'python'}"}
        response = await client.put(
            url, params=query, headers=signed_body("PUT", url, key, query, extra=extra)
        )
        assert response.status_code == 201, response.text
        got = await client.get(url, params=query, headers=signed("GET", url, key, query=query))
        assert got.headers["x-ms-meta-writer"] == "python"
        invalid = extra | {"x-ms-meta": "{'writer': 'different'}"}
        mismatch = await client.put(
            endpoints["file"] + "conflict",
            params=query,
            headers=signed_body("PUT", endpoints["file"] + "conflict", key, query, extra=invalid),
        )
        assert mismatch.status_code == 400


async def test_native_client_correlation_headers(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["blob"] + "correlation"
        query = {"restype": "container"}
        extra = {
            "x-ms-client-request-id": "local-client-correlation",
            "x-ms-return-client-request-id": "true",
        }
        response = await client.put(
            url, params=query, headers=signed_body("PUT", url, key, query, extra=extra)
        )
        assert response.status_code == 201, response.text
        assert response.headers["x-ms-client-request-id"] == "local-client-correlation"


async def test_public_container_without_optional_version_and_no_account_listing(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        assert (
            await client.patch(
                path, params=ARGS, headers=arm, json={"properties": {"allowBlobPublicAccess": True}}
            )
        ).status_code == 200
        url = endpoints["blob"] + "public"
        query = {"restype": "container"}
        extra = {"x-ms-blob-public-access": "container"}
        created = await client.put(
            url, params=query, headers=signed_body("PUT", url, key, query, extra=extra)
        )
        assert created.status_code == 201, created.text
        read = await client.get(url, params=query, headers={"Authorization": ""})
        assert read.status_code == 200, read.text
        listing = await client.get(
            endpoints["blob"], params={"comp": "list"}, headers={"Authorization": ""}
        )
        assert listing.status_code == 403
        assert (
            await client.patch(
                path,
                params=ARGS,
                headers=arm,
                json={"properties": {"allowBlobPublicAccess": False}},
            )
        ).status_code == 200
        assert (
            await client.get(url, params=query, headers={"Authorization": ""})
        ).status_code == 403


async def test_powershell_metadata_include_casing(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        for service, kind in (("blob", "container"), ("file", "share")):
            url = endpoints[service] + "listed"
            query = {"restype": kind}
            assert (
                await client.put(
                    url,
                    params=query,
                    headers=signed_body("PUT", url, key, query, extra={"x-ms-meta-owner": "test"}),
                )
            ).status_code == 201
            query = {
                "comp": "list",
                "include": "Metadata,snapshots" if service == "file" else "Metadata",
            }
            result = await client.get(
                endpoints[service],
                params=query,
                headers=signed("GET", endpoints[service], key, query=query),
            )
            assert result.status_code == 200, result.text
            assert "<owner>test</owner>" in result.text
