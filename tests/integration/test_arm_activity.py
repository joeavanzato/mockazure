from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from mockazure.evidence.datasets import EvidenceDataset
from mockazure.evidence.models import EvidenceVersion
from mockazure.identity.models import ServicePrincipal
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from mockazure.providers.arm.contracts import builtin_roles
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_arm_inventory import access
from tests.integration.test_arm_state import seed_arm
from tests.unit.test_arm_activity_contract import event_payload


def historical(tenant, sub, scope, when="2026-10-01T01:02:03.1234567Z"):
    payload = event_payload(when=when, scope=scope)
    payload.update(
        tenantId=str(tenant), subscriptionId=str(sub), resourceGroupName=scope.split("/")[4]
    )
    return {
        "family": "activityLog",
        "api_version": "2015-04-01",
        "payload": payload,
        "occurred_at": when,
        "available_at": when,
    }


def dataset(records):
    return {
        "schema_version": 1,
        "provenance": {"source": "local ARM historical fixture"},
        "records": records,
    }


async def test_activity_shared_import_publication_and_native_export(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        evidence = f"/_mock/tenants/{tenant}/evidence"
        response = await client.patch(
            evidence + "/settings",
            json={"delay_seconds": 0, "clock_mode": "fixed", "fixed_time": "2026-10-02T00:00:00Z"},
        )
        assert response.status_code == 200, response.text
        first = historical(tenant, sub, group)
        second = historical(tenant, sub, group, when="2026-10-01T01:02:03.1234568Z")
        imported = await client.post(evidence + "/import", json=dataset([first, second]))
        assert imported.status_code == 200, imported.text
        assert imported.json()["inserted"] == 2
        assert set(imported.json()["record_ids"]) == {
            first["payload"]["eventDataId"],
            second["payload"]["eventDataId"],
        }
        again = await client.post(evidence + "/import", json=dataset([first, second]))
        assert again.status_code == 200 and again.json()["unchanged"] == 2
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Owner")
        args = {"api-version": "2021-04-01"}
        assert (
            await client.patch(
                group, params=args, headers=headers, json={"tags": {"Evidence": "native"}}
            )
        ).status_code == 200
        assert (
            await client.put(
                resource["id"] + "/providers/Microsoft.Resources/tags/default",
                params=args,
                headers=headers,
                json={"properties": {"tags": {"Collected": "yes"}}},
            )
        ).status_code == 200
        role_prefix = f"/subscriptions/{sub}/providers/Microsoft.Authorization"
        granted = await client.put(
            group + "/providers/Microsoft.Authorization/roleAssignments/" + str(uuid4()),
            params={"api-version": "2022-04-01"},
            headers=headers,
            json={
                "properties": {
                    "principalId": str(user),
                    "roleDefinitionId": role_prefix
                    + "/roleDefinitions/"
                    + builtin_roles()["Reader"]["id"],
                }
            },
        )
        assert granted.status_code == 201, granted.text
        deleted = await client.delete(group, params=args, headers=headers)
        assert deleted.status_code == 202
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=30,
                tenant_id=tenant,
                kind="arm.group.delete",
            )
        assert (await dispatch_job(database, claim, now)).state == "succeeded"
        drained = await client.post(evidence + "/drain", json={"max_jobs": 1000})
        assert drained.status_code == 200 and drained.json()["failed"] == 0, drained.text
        async with database.sessions() as session:
            rows = list(
                await session.scalars(
                    select(EvidenceVersion).where(
                        EvidenceVersion.tenant_id == tenant, EvidenceVersion.family == "activityLog"
                    )
                )
            )
            assert len(rows) == 6
            captured = {str(row.record_id): row for row in rows}
            one = captured[first["payload"]["eventDataId"]]
            two = captured[second["payload"]["eventDataId"]]
            assert one.payload == first["payload"] and two.payload == second["payload"]
            assert two.arm_event_ticks == one.arm_event_ticks + 1
            assert one.arm_subscription_id == UUID(sub)
            assert one.arm_scope_key == group.lower()
        exported = await client.get(evidence + "/export", params={"family": "activityLog"})
        assert exported.status_code == 200, exported.text[:400]
        parsed = EvidenceDataset.model_validate(exported.json())
        assert len(parsed.records) == 6
        assert all(record.api_version == "2015-04-01" for record in parsed.records)
        originals = {r.payload["eventDataId"]: r.payload for r in parsed.records}
        assert originals[first["payload"]["eventDataId"]] == first["payload"]
        native = [r.payload for r in parsed.records if r.provenance.source == "native"]
        assert len(native) == 4
        assert all("httpRequest" not in row and "claims" not in row for row in native)
        assert any(row["operationName"]["value"].endswith("/delete") for row in native)


async def test_activity_import_requires_owned_subscription_and_exact_availability(
    database, settings
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        evidence = f"/_mock/tenants/{tenant}/evidence"
        foreign = uuid4()
        record = historical(tenant, str(foreign), group.replace(sub, str(foreign)))
        rejected = await client.post(evidence + "/import", json=dataset([record]))
        assert rejected.status_code == 422, rejected.text
        record = historical(tenant, sub, group)
        record["available_at"] = "2026-10-02T00:00:00Z"
        assert (await client.post(evidence + "/import", json=dataset([record]))).status_code == 422
        valid = historical(tenant, sub, group)
        assert (await client.post(evidence + "/import", json=dataset([valid]))).status_code == 200
