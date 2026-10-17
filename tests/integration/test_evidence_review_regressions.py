import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from mockazure.evidence.datasets import EvidenceExportQuery, export_evidence
from mockazure.evidence.models import EvidenceCursor, EvidenceVersion
from mockazure.evidence.projectors import project_source
from mockazure.evidence.repository import load_source, publish_batch
from mockazure.evidence.schemas import SourceRef
from tests.integration.test_evidence_directory import drain
from tests.integration.test_evidence_operations import configure, dataset, drain_api
from tests.integration.test_graph_evidence_reads import publish, record
from tests.integration.test_graph_risk_actions import PATH, observe, risk_client


async def test_current_risk_survives_retention_and_pins_old_versions(database, settings):
    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        await observe(database, tenant, user, now - timedelta(seconds=5))
        await drain(database)
        filler = UUID(int=1)
        await publish(database, tenant, "riskyUser", record("riskyUser", filler, now), now)
        await configure(client, tenant, now + timedelta(days=40), retention=1)
        base = f"/_mock/tenants/{tenant}/evidence"
        response = await client.get(PATH + "/" + str(user), headers=headers)
        assert response.status_code == 200, response.text
        page = (await client.get(PATH + "?$top=1", headers=headers)).json()
        assert page["value"][0]["id"] == str(filler)
        stream = export_evidence(database.sessions, tenant, EvidenceExportQuery(family="riskyUser"))
        prefix = await anext(stream)
        assert (
            await client.post(
                PATH + "/confirmCompromised", headers=headers, json={"userIds": [str(user)]}
            )
        ).status_code == 204
        assert (await client.post(base + "/cleanup", json={"max_rows": 1000})).status_code == 200
        replay = (await client.get(page["@odata.nextLink"], headers=headers)).json()
        assert replay["value"][0]["riskState"] == "atRisk"
        exported = json.loads(prefix + b"".join([part async for part in stream]))
        assert {r["payload"]["id"] for r in exported["records"]} == {str(user), str(filler)}
        assert all(r["payload"]["riskState"] == "atRisk" for r in exported["records"])
        async with database.sessions.begin() as session:
            await session.execute(update(EvidenceCursor).values(expires_at=datetime.now(UTC)))
        for _ in range(3):
            assert (
                await client.post(base + "/cleanup", json={"max_rows": 1000})
            ).status_code == 200
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceVersion)
                    .where(
                        EvidenceVersion.tenant_id == tenant,
                        EvidenceVersion.family == "riskyUser",
                        EvidenceVersion.valid_to.is_not(None),
                    )
                )
                == 0
            )
        assert (
            await client.post(PATH + "/dismiss", headers=headers, json={"userIds": [str(user)]})
        ).status_code == 204
        assert (await client.get(PATH + "/" + str(user), headers=headers)).json()[
            "riskState"
        ] == "dismissed"
        assert len((await client.get(base + "/export?family=riskyUser")).json()["records"]) == 2


@pytest.mark.parametrize("new_first", [True, False])
async def test_old_detection_cannot_clear_newer_linked_risk(database, settings, new_first):
    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        signin = record("signIn", user, now - timedelta(seconds=20))
        identifier = UUID(signin["id"])
        await publish(database, tenant, "signIn", signin, now - timedelta(seconds=20))
        await observe(database, tenant, user, now - timedelta(seconds=10), sign_in_id=identifier)
        await drain(database)
        old_id, new_id = uuid4(), uuid4()
        old = await observe(
            database,
            tenant,
            user,
            now - timedelta(seconds=5),
            request_id=old_id,
            sign_in_id=identifier,
        )
        assert (
            await client.post(PATH + "/dismiss", headers=headers, json={"userIds": [str(user)]})
        ).status_code == 204
        await configure(client, tenant, now + timedelta(seconds=2))
        fresh = await observe(
            database,
            tenant,
            user,
            now + timedelta(seconds=1),
            request_id=new_id,
            sign_in_id=identifier,
        )
        for source_id in (new_id, old_id) if new_first else (old_id, new_id):
            async with database.sessions.begin() as session:
                source = await load_source(
                    session, SourceRef(tenant_id=tenant, kind="risk", id=source_id)
                )
                await publish_batch(session, project_source(source))
        logged = (
            await client.get("/v1.0/auditLogs/signIns/" + str(identifier), headers=headers)
        ).json()
        assert (logged["riskState"], logged["riskLevelAggregated"]) == ("atRisk", "high")
        assert (await client.get(PATH + "/" + str(user), headers=headers)).json()[
            "riskState"
        ] == "atRisk"
        for detection, state in ((old, "dismissed"), (fresh, "atRisk")):
            result = await client.get(
                "/v1.0/identityProtection/riskDetections/" + str(detection.detection_id),
                headers=headers,
            )
            assert result.json()["riskState"] == state


@pytest.mark.parametrize(
    "family,field,expression,version",
    [
        ("directoryAudit", "targetResources", "targetResources/any(t:t/id eq 'match')", "v1.0"),
        ("signIn", "signInEventTypes", "signInEventTypes/any(t:t eq 'nonInteractiveUser')", "beta"),
    ],
)
async def test_nullable_collection_any_filters(
    database, settings, family, field, expression, version
):
    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        rows = []
        for kind in ("null", "omitted", "empty", "populated"):
            payload = record(family, user, now, event="nonInteractiveUser")
            if kind == "omitted":
                payload.pop(field)
            else:
                payload[field] = {
                    "null": None,
                    "empty": [],
                    "populated": [{"id": "match"}]
                    if family == "directoryAudit"
                    else ["nonInteractiveUser"],
                }[kind]
            rows.append(
                {
                    "family": family,
                    "api_version": version,
                    "payload": payload,
                    "occurred_at": now.isoformat(),
                    "available_at": now.isoformat(),
                    "origin": "historical",
                    "entitlement_snapshot": "P2",
                }
            )
        response = await client.post(f"/_mock/tenants/{tenant}/evidence/import", json=dataset(rows))
        assert response.status_code == 200, response.text
        await drain_api(client, tenant)
        response = await client.get(
            f"/{version}/auditLogs/" + ("signIns" if family == "signIn" else "directoryAudits"),
            params={"$filter": expression},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["value"]] == [rows[-1]["payload"]["id"]]
