from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from mockazure.admin.app import create_app


def certificate():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MockAzure test client")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, cert


@asynccontextmanager
async def identity_client(database, settings):
    from mockazure.identity.keys import initialize_identity

    await initialize_identity(settings, database)
    settings.token_file.write_text("A" * 43)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings, database)),
        base_url="https://localhost:8443",
        headers={"Authorization": "Bearer " + "A" * 43},
    ) as client:
        tenant = (
            await client.post(
                "/_mock/tenants",
                json={
                    "slug": "contoso",
                    "display_name": "Contoso",
                    "domain": "contoso.test",
                    "licences": ["E5"],
                },
            )
        ).json()["id"]
        base = f"/_mock/tenants/{tenant}/identity"
        app = (
            await client.post(
                base + "/applications",
                json={
                    "display_name": "Collector",
                    "client_mode": "confidential",
                    "required_permissions": [
                        {
                            "resource": "graph",
                            "kind": "application",
                            "permissions": ["Organization.Read.All"],
                        }
                    ],
                },
            )
        ).json()
        client_id = app["client_id"]
        secret = (
            await client.post(
                base + f"/applications/{client_id}/credentials", json={"kind": "secret"}
            )
        ).json()
        await client.put(
            base + f"/grants/{uuid4()}",
            json={
                "client_id": client_id,
                "resource": "graph",
                "kind": "application",
                "permissions": ["Organization.Read.All"],
            },
        )
        key, cert = certificate()
        await client.post(
            base + f"/applications/{client_id}/credentials",
            json={
                "kind": "certificate",
                "public_certificate": cert.public_bytes(serialization.Encoding.PEM).decode(),
            },
        )
        yield client, tenant, client_id, secret, key, cert
