import asyncio
import json
import os
import socket
import ssl
import subprocess
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

import httpx
import jwt
import pytest
from sqlalchemy import select

from mockazure.bootstrap import initialize_instance
from mockazure.config import load_settings
from mockazure.evidence.models import EvidenceObservation
from tests.integration.identity_support import identity_client
from tests.integration.test_evidence_directory import drain


async def identity(client, tenant, kind="user", name="workload"):
    resource = f"/subscriptions/{uuid4()}/resourceGroups/mockazure/providers/"
    resource += (
        "Microsoft.Web/sites/"
        if kind == "system"
        else "Microsoft.ManagedIdentity/userAssignedIdentities/"
    ) + name
    response = await client.post(
        f"/_mock/tenants/{tenant}/managed-identities",
        json={"display_name": name, "kind": kind, "resource_id": resource},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def host(client, tenant, system=None, users=(), name="host"):
    response = await client.post(
        f"/_mock/tenants/{tenant}/managed-identity-hosts",
        json={"name": name, "system_identity_id": system, "user_identity_ids": list(users)},
    )
    assert response.status_code == 201
    return response.json()


def arguments(**extra):
    return {"api-version": "2019-08-01", "resource": "https://graph.microsoft.com", **extra}


async def grant(client, tenant, client_id, permissions):
    response = await client.put(
        f"/_mock/tenants/{tenant}/identity/grants/{uuid5(UUID(client_id), 'managed-grant')}",
        json={
            "client_id": client_id,
            "resource": "graph",
            "kind": "application",
            "permissions": permissions,
        },
    )
    assert response.status_code == 200, response.text


async def test_host_protocol_matrix(database, settings):
    async with identity_client(database, settings) as (admin, tenant, *_):
        system = await identity(admin, tenant, "system", "system")
        user = await identity(admin, tenant)
        unattached = await identity(admin, tenant, name="unattached")
        foreign_tenant = (
            await admin.post(
                "/_mock/tenants",
                json={"slug": "foreign", "display_name": "Foreign", "domain": "foreign.test"},
            )
        ).json()["id"]
        foreign = await identity(admin, foreign_tenant)
        denied = await admin.post(
            f"/_mock/tenants/{tenant}/managed-identity-hosts",
            json={"name": "foreign", "user_identity_ids": [foreign["id"]]},
        )
        assert denied.status_code == 404
        binding = await host(admin, tenant, system["id"], [user["id"]])
        from mockazure.managed_identity.app import create_managed_identity_app

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_managed_identity_app(settings, database)),
            base_url="http://127.0.0.1:49446",
            headers={"X-IDENTITY-HEADER": binding["header"]},
        ) as client:
            for selector, expected in [
                ({}, system),
                ({"client_id": user["client_id"]}, user),
                ({"principal_id": user["principal_id"]}, user),
                ({"object_id": user["principal_id"]}, user),
                ({"mi_res_id": user["resource_id"]}, user),
            ]:
                response = await client.get("/MSI/token", params=arguments(**selector))
                assert response.status_code == 200, response.text
                body = response.json()
                assert body["client_id"] == expected["client_id"] and body["expires_on"].isdigit()
                claims = jwt.decode(body["access_token"], options={"verify_signature": False})
                assert claims["xms_mirid"] == expected["resource_id"]
            for params, status in [
                (arguments(**{"api-version": "old"}), 400),
                (arguments(client_id=user["client_id"], object_id=user["principal_id"]), 400),
                (arguments(client_id=unattached["client_id"]), 400),
                (arguments(client_id=foreign["client_id"]), 400),
                (arguments(client_id=system["client_id"]), 400),
                (arguments(client_id=str(uuid4())), 400),
                (arguments(extra="no"), 400),
                (arguments(resource="https://never-fetch.invalid/path"), 400),
                ([*arguments().items(), ("resource", "https://graph.microsoft.com")], 400),
            ]:
                response = await client.get("/MSI/token", params=params)
                assert response.status_code == status
                assert response.json()["statusCode"] == status and response.json()["message"]
            assert (
                await client.get(
                    "/MSI/token", params=arguments(), headers={"X-IDENTITY-HEADER": "wrong"}
                )
            ).status_code == 403
            response = await client.get(
                "/MSI/token",
                params=arguments(),
                headers=[
                    ("X-IDENTITY-HEADER", binding["header"]),
                    ("X-IDENTITY-HEADER", binding["header"]),
                ],
            )
            assert response.status_code == 403
            assert (
                await admin.patch(
                    f"/_mock/tenants/{tenant}/managed-identities/{user['id']}",
                    json={"enabled": False},
                )
            ).status_code == 200
            assert (
                await client.get("/MSI/token", params=arguments(client_id=user["client_id"]))
            ).status_code == 403
        response = await admin.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": user["client_id"],
                "client_secret": "invalid",
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        assert response.status_code in (400, 401)
        assert (
            await admin.post(
                f"/_mock/tenants/{tenant}/identity/applications/{user['client_id']}/credentials",
                json={"kind": "secret"},
            )
        ).status_code == 422


async def test_header_rotation_and_detach_semantics(database, settings):
    async with identity_client(database, settings) as (admin, tenant, *_):
        system = await identity(admin, tenant, "system", "system")
        user = await identity(admin, tenant)
        for item in (system, user):
            await grant(
                admin, tenant, item["client_id"], ["Organization.Read.All", "User.Read.All"]
            )
        first = await host(admin, tenant, system["id"], [user["id"]], name="first")
        second = await host(admin, tenant, users=[user["id"]], name="second")
        from mockazure.managed_identity.app import create_managed_identity_app

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_managed_identity_app(settings, database)),
            base_url="http://127.0.0.1:49446",
        ) as client:

            async def token(header, **selector):
                return await client.get(
                    "/MSI/token",
                    params=arguments(**selector),
                    headers={"X-IDENTITY-HEADER": header},
                )

            original = (await token(first["header"])).json()["access_token"]
            rotated = (
                await admin.post(
                    f"/_mock/tenants/{tenant}/managed-identity-hosts/{first['host_id']}/rotate-header"
                )
            ).json()
            assert (await token(first["header"])).status_code == 403
            assert (await token(rotated["header"])).status_code == 200
            access = {"Authorization": "Bearer " + original}
            assert (await admin.get("/v1.0/organization", headers=access)).status_code == 200
            assert (
                await admin.patch(
                    f"/_mock/tenants/{tenant}/managed-identity-hosts/{first['host_id']}",
                    json={"system_identity_id": None, "user_identity_ids": []},
                )
            ).status_code == 200
            assert (await token(rotated["header"])).status_code == 400
            assert (await token(rotated["header"], client_id=user["client_id"])).status_code == 400
            assert (await token(second["header"], client_id=user["client_id"])).status_code == 200
            assert (await admin.get("/v1.0/organization", headers=access)).status_code in (401, 403)
            assert (await admin.get("/v1.0/users", headers=access)).status_code in (401, 403)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_managed_identity_sdk_end_to_end(database, settings, tmp_path, monkeypatch):
    from azure.core.exceptions import ClientAuthenticationError
    from azure.identity import ManagedIdentityCredential

    public_port, managed_port = free_port(), free_port()
    files = initialize_instance(
        tmp_path / "live",
        database_url=settings.database_url.get_secret_value(),
        advertised_host="localhost",
        port=public_port,
    )
    config = json.loads(files.config_file.read_text())
    config["managed_identity_listen_port"] = managed_port
    files.config_file.write_text(json.dumps(config))
    local = load_settings(files.config_file)
    async with identity_client(database, local) as (admin, tenant, *_):
        system = await identity(admin, tenant, "system", "system")
        user = await identity(admin, tenant)
        for item in (system, user):
            await grant(admin, tenant, item["client_id"], ["Organization.Read.All"])
        binding = await host(admin, tenant, system["id"], [user["id"]])
        for key in (
            "MSI_ENDPOINT",
            "MSI_SECRET",
            "IDENTITY_SERVER_THUMBPRINT",
            "IMDS_ENDPOINT",
            "AZURE_CLIENT_ID",
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_SECRET",
            "AZURE_FEDERATED_TOKEN_FILE",
        ):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("IDENTITY_ENDPOINT", f"http://127.0.0.1:{managed_port}/MSI/token")
        monkeypatch.setenv("IDENTITY_HEADER", binding["header"])
        monkeypatch.setenv("NO_PROXY", "*")
        env = {k: v for k, v in os.environ.items() if not k.startswith("MOCKAZURE_")}
        processes = []
        streams = []
        try:
            for name, command in [
                ("provider", ["serve"]),
                ("identity", ["managed-identity", "serve"]),
            ]:
                stream = (tmp_path / f"{name}.log").open("w")
                streams.append(stream)
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "mockazure",
                            *command,
                            "--config",
                            str(files.config_file),
                        ],
                        env=env,
                        stdout=stream,
                        stderr=stream,
                    )
                )
            async with httpx.AsyncClient(
                verify=ssl.create_default_context(cafile=str(files.ca_cert_file)), trust_env=False
            ) as public:
                for _ in range(100):
                    assert all(p.poll() is None for p in processes)
                    try:
                        ready = (
                            await public.get(f"https://localhost:{public_port}/_mock/ready")
                        ).status_code == 200
                        async with httpx.AsyncClient(trust_env=False) as probe:
                            mi_ready = (
                                await probe.get(f"http://127.0.0.1:{managed_port}/MSI/token")
                            ).status_code in (400, 403)
                        if ready and mi_ready:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail("Local managed identity servers did not become ready")

                async def cli(*args):
                    result = await asyncio.to_thread(
                        subprocess.run,
                        [
                            sys.executable,
                            "-m",
                            "mockazure",
                            "managed-identity",
                            *args,
                            "--config",
                            str(files.config_file),
                        ],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    assert result.returncode == 0, "Managed identity CLI failed"
                    return result

                first_file = tmp_path / "host-header"
                result = await cli(
                    "create-host",
                    "--tenant-id",
                    str(tenant),
                    "--name",
                    "cli-host",
                    "--user-identity-id",
                    user["id"],
                    "--output",
                    str(first_file),
                )
                first_header = first_file.read_text().strip()
                assert len(first_header) >= 32
                assert first_header not in result.stdout + result.stderr
                host_id = json.loads(result.stdout)["host_id"]
                second_file = tmp_path / "rotated-header"
                result = await cli(
                    "rotate-header",
                    "--tenant-id",
                    str(tenant),
                    "--host-id",
                    host_id,
                    "--output",
                    str(second_file),
                )
                assert second_file.read_text().strip() != first_header
                assert second_file.read_text().strip() not in result.stdout + result.stderr
                tokens = []
                for kwargs in (
                    {},
                    {"client_id": user["client_id"]},
                    {"identity_config": {"object_id": user["principal_id"]}},
                    {"identity_config": {"resource_id": user["resource_id"]}},
                ):
                    credential = ManagedIdentityCredential(**kwargs)
                    try:
                        token = await asyncio.to_thread(
                            credential.get_token, "https://graph.microsoft.com/.default"
                        )
                    finally:
                        await asyncio.to_thread(credential.close)
                    tokens.append(token.token)
                    headers = {"Authorization": "Bearer " + token.token}
                    assert (
                        await public.get(
                            f"https://localhost:{public_port}/v1.0/organization", headers=headers
                        )
                    ).status_code == 200
                    assert (
                        await public.get(
                            f"https://localhost:{public_port}/v1.0/users", headers=headers
                        )
                    ).status_code == 403
                await grant(admin, tenant, user["client_id"], ["User.Read.All"])
                credential = ManagedIdentityCredential(client_id=user["client_id"])
                try:
                    allowed = await asyncio.to_thread(
                        credential.get_token, "https://graph.microsoft.com/.default"
                    )
                finally:
                    await asyncio.to_thread(credential.close)
                assert (
                    await public.get(
                        f"https://localhost:{public_port}/v1.0/users",
                        headers={"Authorization": "Bearer " + allowed.token},
                    )
                ).status_code == 200
                monkeypatch.setenv("IDENTITY_HEADER", "invalid")
                credential = ManagedIdentityCredential()
                try:
                    with pytest.raises(ClientAuthenticationError) as error:
                        await asyncio.to_thread(
                            credential.get_token, "https://graph.microsoft.com/.default"
                        )
                    assert "403" in str(error.value) and "None, None" not in str(error.value)
                finally:
                    await asyncio.to_thread(credential.close)
            from mockazure.identity.tokens import validate_access_token

            async with database.sessions() as session:
                context = await validate_access_token(
                    tokens[0], "graph", datetime.now(UTC), session, endpoints=local.endpoints
                )
                assert context.subject_id == UUID(system["principal_id"])
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                process.wait(timeout=15)
            for stream in streams:
                stream.close()
        for name in ("provider", "identity"):
            assert binding["header"] not in (tmp_path / f"{name}.log").read_text()
    await drain(database)
    async with database.sessions() as session:
        values = list(
            await session.scalars(
                select(EvidenceObservation.payload).where(EvidenceObservation.family == "signIn")
            )
        )
        assert len(values) == 5 and all(
            v["signInEventTypes"] == ["managedIdentity"] for v in values
        )


async def test_no_fallback_and_secret_output(database, settings, tmp_path):
    async with identity_client(database, settings) as (admin, tenant, *_):
        user = await identity(admin, tenant)
        binding = await host(admin, tenant, users=[user["id"]])
        from mockazure.managed_identity.app import create_managed_identity_app

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_managed_identity_app(settings, database)),
            base_url="http://127.0.0.1:49446",
        ) as client:
            response = await client.get(
                "/MSI/token",
                params=arguments(
                    resource="http://169.254.169.254/metadata/identity/oauth2/token",
                    client_id=user["client_id"],
                ),
                headers={"X-IDENTITY-HEADER": binding["header"]},
            )
            assert response.status_code == 400 and response.json()["error"] == "invalid_resource"
        exported = await admin.get(f"/_mock/tenants/{tenant}/directory/export")
        assert binding["header"] not in exported.text
        from mockazure.managed_identity.models import ManagedHost

        async with database.sessions() as session:
            stored = await session.get(ManagedHost, UUID(binding["host_id"]))
            assert stored.header_hash != binding["header"] and len(stored.header_hash) == 64
        assert (
            await admin.get(
                "/MSI/token", params=arguments(), headers={"X-IDENTITY-HEADER": binding["header"]}
            )
        ).status_code == 501


async def test_current_disabled_application_rejects_existing_token(database, settings):
    async with identity_client(database, settings) as (client, tenant, app, secret, *_):
        response = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": app,
                "client_secret": secret["secret"],
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        access = {"Authorization": "Bearer " + response.json()["access_token"]}
        assert (await client.get("/v1.0/organization", headers=access)).status_code == 200
        assert (
            await client.patch(
                f"/_mock/tenants/{tenant}/identity/applications/{app}", json={"enabled": False}
            )
        ).status_code == 200
        assert (await client.get("/v1.0/organization", headers=access)).status_code == 403
