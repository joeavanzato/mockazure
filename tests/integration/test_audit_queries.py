from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from mockazure.db.job_model import Job
from mockazure.evidence.projectors import project_source
from mockazure.evidence.repository import append_source, load_source, publish_batch
from mockazure.evidence.schemas import SourceInput, SourceRef
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from tests.integration.graph_support import graph_client, headers_for

PATH = "/v1.0/security/auditLog/queries"


async def entitlement(client, tenant):
    result = await client.put(
        f"/_mock/tenants/{tenant}/mail/settings",
        json={"exchange_enabled": True, "audit_tier": "standard"},
    )
    assert result.status_code == 200, result.text


async def seed(database, tenant, count=3, service="Exchange", when=None):
    when = when or datetime.now(UTC) - timedelta(hours=1)
    records = []
    async with database.sessions.begin() as session:
        for index in range(count):
            identifier = uuid4()
            payload = {
                "id": str(identifier),
                "organizationId": str(tenant),
                "createdDateTime": when.isoformat(),
                "service": service,
                "operation": "Set-Mailbox" if service == "Exchange" else "FileAccessed",
                "auditLogRecordType": "ExchangeAdmin" if service == "Exchange" else "OneDrive",
                "userPrincipalName": "actor@example.test",
                "userType": "Regular",
                "objectId": f"mailbox-{index}",
                "clientIp": "192.0.2.42",
                "administrativeUnits": ["audit-unit"],
                "auditData": {
                    "dynamicProperties": {"Marker": f"needle-{index}", "Operation": "common-only"}
                },
            }
            ref = SourceRef(tenant_id=tenant, kind="historical", id=identifier)
            await append_source(
                session,
                SourceInput(
                    ref=ref,
                    occurred_at=when,
                    available_at=when,
                    facts={"family": "auditLogRecord", "payload": payload},
                ),
            )
            await publish_batch(session, project_source(await load_source(session, ref)))
            records.append(payload)
    return records


def query_body(**values):
    now = datetime.now(UTC)
    return {
        "displayName": "Investigation",
        "filterStartDateTime": (now - timedelta(days=2)).isoformat(),
        "filterEndDateTime": now.isoformat(),
        **values,
    }


async def claim(database, tenant, now=None, lease=60):
    async with database.sessions.begin() as session:
        return await claim_job(
            session,
            owner=uuid4(),
            now=now or datetime.now(UTC),
            lease_seconds=lease,
            tenant_id=tenant,
            kind="audit.query",
        )


async def finish(database, tenant):
    job = await claim(database, tenant)
    assert job is not None
    result = await dispatch_job(database, job, datetime.now(UTC))
    assert result.state == "succeeded", result
    return job


async def test_query_create_poll_records(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        records = await seed(database, tenant)
        await seed(database, tenant, service="OneDrive")
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        result = await client.post(
            PATH,
            headers=headers,
            json=query_body(
                serviceFilter="Exchange",
                operationFilters=["Set-Mailbox"],
                status="succeeded",
                keywordFilter="needle-1",
                userPrincipalNameFilters=["actor@example.test"],
                objectIdFilters=["mailbox-1"],
                ipAddressFilters=["192.0.2.42"],
                administrativeUnitIdFilters=["audit-unit"],
                recordTypeFilters=["exchangeAdmin"],
            ),
        )
        assert result.status_code == 201, result.text
        value = result.json()
        assert value["status"] == "notStarted"
        assert value["serviceFilters"] == ["Exchange"]
        item = PATH + "/" + value["id"]
        assert (await client.get(item + "/records", headers=headers)).status_code == 409
        await finish(database, tenant)
        value = (await client.get(item, headers=headers)).json()
        assert value["status"] == "succeeded" and value["approximateReturnedRecordCount"] == 1
        assert (await client.get(item + "/records", headers=headers)).json()["value"] == [
            records[1]
        ]
        await seed(database, tenant)
        assert (await client.get(item + "/records", headers=headers)).json()["value"] == [
            records[1]
        ]
        conflict = query_body(serviceFilter="Exchange", serviceFilters=["OneDrive"])
        assert (await client.post(PATH, headers=headers, json=conflict)).status_code == 400
        assert (
            await client.post(PATH + "?$unknown=1", headers=headers, json=query_body())
        ).status_code == 400
        assert (
            await client.get(item + "/records?$filter=id ne null", headers=headers)
        ).status_code == 400
        invalid = query_body(filterStartDateTime="2030-01-01T00:00:00Z")
        assert (await client.post(PATH, headers=headers, json=invalid)).status_code == 400
        assert (await client.get(PATH + "/" + str(uuid4()), headers=headers)).status_code == 404


async def test_query_workload_permissions(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        full = await headers_for(database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"})
        scoped = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery-Exchange.Read.All"}
        )
        legacy = await headers_for(database, settings, tenant, app, None, {"AuditLog.Read.All"})
        response = await client.post(
            PATH, headers=full, json=query_body(serviceFilters=["OneDrive"])
        )
        assert response.status_code == 201, response.text
        item = PATH + "/" + response.json()["id"]
        assert (await client.get(item, headers=scoped)).status_code == 403
        assert (await client.get(item + "/records", headers=scoped)).status_code == 403
        assert (await client.get(PATH, headers=scoped)).json()["value"] == []
        assert (
            await client.post(PATH, headers=scoped, json=query_body(serviceFilter="OneDrive"))
        ).status_code == 403
        assert (await client.get(PATH, headers=legacy)).status_code == 403
        permitted = await client.post(PATH, headers=scoped, json=query_body())
        assert permitted.status_code == 201, permitted.text
        assert permitted.json()["serviceFilters"] == ["Exchange"]
        delegated = await headers_for(
            database, settings, tenant, app, user, {"AuditLogsQuery.Read.All"}, "delegated"
        )
        assert (await client.get(PATH, headers=delegated)).status_code == 403
        role = f"/_mock/tenants/{tenant}/mail/audit-roles/{user}/View-Only Audit Logs"
        assert (await client.put(role)).status_code == 200
        assert (await client.get(PATH, headers=delegated)).status_code == 200
        assert (await client.delete(role)).status_code == 204
        assert (await client.get(PATH, headers=delegated)).status_code == 403
        await client.put(
            f"/_mock/tenants/{tenant}/mail/settings",
            json={"exchange_enabled": True, "audit_tier": "none"},
        )
        assert (await client.get(item, headers=full)).status_code == 403


async def test_query_stale_worker_and_failed_records(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await entitlement(client, tenant)
        await seed(database, tenant)
        headers = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        response = await client.post(PATH, headers=headers, json=query_body())
        assert response.status_code == 201, response.text
        item = PATH + "/" + response.json()["id"]
        now = datetime.now(UTC)
        stale = await claim(database, tenant, now, lease=1)
        winner = await claim(database, tenant, now + timedelta(seconds=2))
        assert (await dispatch_job(database, stale, now + timedelta(seconds=2))).state == "stale"
        assert (
            await dispatch_job(database, winner, now + timedelta(seconds=2))
        ).state == "succeeded"
        assert len((await client.get(item + "/records", headers=headers)).json()["value"]) == 3
        response = await client.post(PATH, headers=headers, json=query_body())
        assert response.status_code == 201
        failed = PATH + "/" + response.json()["id"]
        async with database.sessions.begin() as session:
            job = await session.scalar(
                select(Job).where(
                    Job.kind == "audit.query", Job.source_key == response.json()["id"]
                )
            )
            job.payload = {"query_id": str(uuid4())}
        bad = await claim(database, tenant)
        assert (await dispatch_job(database, bad, datetime.now(UTC))).state == "failed"
        assert (await client.get(failed, headers=headers)).json()["status"] == "failed"
        assert (await client.get(failed + "/records", headers=headers)).status_code == 409
        assert UUID(response.json()["id"])
