"""Independent archive reads, fixture provenance and authentication clock checks."""

import asyncio
import json
import ssl
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from sqlalchemy import select

from integrations.python.arm import LocalSession
from mockazure.activity.models import Content
from mockazure.endpoints import local_origin
from mockazure.evidence.models import EvidenceSource
from mockazure.identity.keys import SigningKeyStore, read_wrapping_key
from mockazure.workloads.models import WorkloadVersion
from tools.living_tenant_clients import acquire


def archive_collect(settings, refs, key):
    from azure.core.pipeline.transport import RequestsTransport
    from azure.storage.blob import BlobServiceClient

    endpoint = local_origin(settings.endpoints.storage)
    account = refs["account"].rsplit("/", 1)[1]
    ids = set()
    with LocalSession(endpoint, str(settings.ca_file)) as session:
        with BlobServiceClient(
            endpoint + "/" + account,
            credential={"account_name": account, "account_key": key},
            transport=RequestsTransport(session=session, connection_verify=str(settings.ca_file)),
            retry_total=0,
            api_version="2025-11-05",
        ) as client:
            for container in client.list_containers(name_starts_with="insights-logs-"):
                for blob in client.get_container_client(container.name).list_blobs():
                    raw = (
                        client.get_blob_client(container.name, blob.name).download_blob().readall()
                    )
                    ids.update(row["correlationId"] for row in json.loads(raw)["records"])
    return ids


async def import_check(admin, ctx, entities, transport):
    from tools.living_evidence import checked, drain

    async def snapshot():
        async with ctx.database.sessions() as session:
            sources = set(
                await session.scalars(
                    select(EvidenceSource.id).where(
                        EvidenceSource.tenant_id == ctx.tenant_id,
                        EvidenceSource.kind == "workload",
                    )
                )
            )
            deliveries = set(
                await session.scalars(
                    select(Content.record_id).where(
                        Content.tenant_id == ctx.tenant_id,
                    )
                )
            )
        return sources, deliveries

    await drain(admin, ctx)
    before = await snapshot()
    identifier = entities["business.documents:0"]
    async with ctx.database.sessions() as session:
        row = await session.scalar(
            select(WorkloadVersion).where(
                WorkloadVersion.tenant_id == ctx.tenant_id,
                WorkloadVersion.workload == "sharepoint",
                WorkloadVersion.kind == "driveItem",
                WorkloadVersion.id == identifier,
                WorkloadVersion.valid_to.is_(None),
            )
        )
        assert row is not None
        payload = dict(row.payload, name="historical-import-review.docx")
        record = {"kind": row.kind, "id": row.id, "payload": payload}
        if row.content_id:
            record["content_id"] = str(row.content_id)
        scope = row.scope
    await admin.request(
        "POST",
        f"/_mock/tenants/{ctx.tenant_id}/workloads/sharepoint/import",
        json={
            "scope": scope,
            "records": [record],
        },
    )
    await drain(admin, ctx)
    after = await snapshot()
    stored = checked(
        await transport.request(
            "graph",
            "GET",
            f"/v1.0/drives/{entities['business.drives:0']}/items/{identifier}",
        )
    )
    return {
        "stored": stored["name"] == payload["name"],
        "resource_id": identifier,
        "native_sources": sorted(map(str, after[0] - before[0])),
        "native_deliveries": sorted(map(str, after[1] - before[1])),
        "provenance": "Administrative workload history import; public Graph readback.",
    }


async def clock_check(admin, ctx, credential):
    from tools.living_evidence import checked

    settings = ctx.settings
    raw = await asyncio.to_thread(acquire, settings, ctx, credential, "https://graph.microsoft.com")
    claims = jwt.decode(raw, options={"verify_signature": False})
    expiry = claims["exp"]
    # Construct a correctly signed but already expired local token to exercise the
    # real HTTPS validator without sleeping for an hour or altering the host clock.
    now = datetime.now(UTC)
    expired_claims = dict(
        claims,
        iat=int((now - timedelta(hours=2)).timestamp()),
        nbf=int((now - timedelta(hours=2)).timestamp()),
        exp=int((now - timedelta(minutes=5)).timestamp()),
    )
    async with ctx.database.sessions.begin() as session:
        keys = SigningKeyStore(session, read_wrapping_key(settings.data_dir), settings.endpoints)
        key = await keys.active_key(now)
        expired = jwt.encode(
            expired_claims,
            keys.private_key(key),
            algorithm="RS256",
            headers={"kid": key.kid, "typ": "at+jwt"},
        )
    base = f"/_mock/tenants/{ctx.tenant_id}/evidence/settings"
    previous = await admin.request("GET", base)
    try:
        await admin.request(
            "PATCH",
            base,
            json={
                "clock_mode": "fixed",
                "fixed_time": (now + timedelta(days=2)).isoformat(),
            },
        )
        async with httpx.AsyncClient(
            verify=ssl.create_default_context(cafile=str(settings.ca_file)),
            trust_env=False,
            follow_redirects=False,
            timeout=30,
        ) as http:
            url = local_origin(settings.endpoints.graph) + "/v1.0/users"
            valid = await http.get(url, headers={"Authorization": "Bearer " + raw})
            checked(valid)
            invalid = await http.get(url, headers={"Authorization": "Bearer " + expired})
            assert invalid.status_code == 401
        return {
            "advanced_seconds": 172800,
            "access_token_expiry_before": expiry,
            "access_token_expiry_after": jwt.decode(raw, options={"verify_signature": False})[
                "exp"
            ],
            "valid_token_status": valid.status_code,
            "expired_token_status": invalid.status_code,
            "method": (
                "Public HTTPS requests with an issued token "
                "and a locally signed expired control token."
            ),
        }
    finally:
        await admin.request("PATCH", base, json=previous)
