from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from mockazure.admin.errors import AdminError
from mockazure.evidence.clock import update_settings
from mockazure.evidence.models import EvidenceObservation, EvidenceSource, EvidenceVersion
from mockazure.evidence.repository import append_source, load_source, publish_batch
from mockazure.evidence.schemas import EvidenceSettings, SourceInput, SourceRef
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_evidence_directory import drain
from tests.integration.test_graph_evidence_reads import READ, publish, record
from tests.integration.test_graph_licensing import P1, PLAN1, assign, seed_sku, set_role
from tests.integration.test_graph_mutations import WRITE

PATH = "/v1.0/identityProtection/riskyUsers"
RISK_WRITE = READ | {"IdentityRiskyUser.ReadWrite.All"}


@asynccontextmanager
async def risk_client(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            await update_settings(
                session,
                tenant,
                EvidenceSettings(delay_seconds=0, clock_mode="fixed", fixed_time=now),
            )
        await seed_sku(client, tenant)
        write = await headers_for(database, settings, tenant, app, None, WRITE)
        assert (
            await client.patch(f"/v1.0/users/{user}", headers=write, json={"usageLocation": "US"})
        ).status_code == 204
        assert (
            await client.post(f"/v1.0/users/{user}/assignLicense", headers=write, json=assign())
        ).status_code == 200
        headers = await headers_for(database, settings, tenant, app, None, RISK_WRITE)
        yield client, tenant, app, user, headers, now


async def observe(database, tenant, user, when, **kwargs):
    from mockazure.evidence.risk import record_risk_observation
    from mockazure.evidence.schemas import RiskObservation

    value = RiskObservation(
        user_id=user,
        event_type="unfamiliarFeatures",
        level=kwargs.pop("level", "high"),
        occurred_at=when,
        request_id=kwargs.pop("request_id", uuid4()),
        **kwargs,
    )
    async with database.sessions.begin() as session:
        return await record_risk_observation(session, tenant, value)


async def test_confirm_and_dismiss_visible_effects(database, settings):
    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        signin = record("signIn", user, now - timedelta(seconds=10))
        await publish(database, tenant, "signIn", signin, now - timedelta(seconds=10))
        outcome = await observe(
            database, tenant, user, now - timedelta(seconds=5), sign_in_id=UUID(signin["id"])
        )
        await observe(database, tenant, user, now - timedelta(seconds=4))
        await drain(database)
        detection_page = (
            await client.get("/v1.0/identityProtection/riskDetections?$top=1", headers=headers)
        ).json()
        assert (await client.get(PATH, headers=headers)).json()["value"][0]["riskState"] == "atRisk"
        assert (
            await client.post(
                PATH + "/confirmCompromised", headers=headers, json={"userIds": [str(user)]}
            )
        ).status_code == 204
        value = (await client.get(PATH + "/" + str(user), headers=headers)).json()
        assert value["riskState"] == "confirmedCompromised" and value["riskLevel"] == "high"
        detection_path = "/v1.0/identityProtection/riskDetections/" + str(outcome.detection_id)
        assert (await client.get(detection_path, headers=headers)).json()["riskState"] == "atRisk"
        assert (
            await client.post(PATH + "/dismiss", headers=headers, json={"userIds": [str(user)]})
        ).status_code == 204
        value = (await client.get(PATH + "/" + str(user), headers=headers)).json()
        assert (value["riskState"], value["riskLevel"], value["riskDetail"]) == (
            "dismissed",
            "none",
            "adminDismissedAllRiskForUser",
        )
        detected = (await client.get(detection_path, headers=headers)).json()
        logged = (
            await client.get("/v1.0/auditLogs/signIns/" + signin["id"], headers=headers)
        ).json()
        assert detected["riskState"] == logged["riskState"] == "dismissed"
        assert datetime.fromisoformat(detected["lastUpdatedDateTime"]) == now
        async with database.sessions() as session:
            current = await session.scalar(
                select(EvidenceVersion).where(
                    EvidenceVersion.tenant_id == tenant,
                    EvidenceVersion.family == "riskyUser",
                    EvidenceVersion.record_id == user,
                    EvidenceVersion.valid_to.is_(None),
                )
            )
            assert current.event_time == now
        snapshot = (await client.get(detection_page["@odata.nextLink"], headers=headers)).json()
        assert snapshot["value"][0]["riskState"] == "atRisk"
        assert logged["riskLevelAggregated"] == "none" and logged["riskLevelDuringSignIn"] == "high"
        async with database.sessions() as session:
            original = await session.get(
                EvidenceObservation, (tenant, "signIn", UUID(signin["id"]))
            )
            assert original.payload["riskState"] == "atRisk"
            before = await session.scalar(
                select(func.count())
                .select_from(EvidenceSource)
                .where(EvidenceSource.kind == "risk_action")
            )
        assert (
            await client.post(PATH + "/dismiss", headers=headers, json={"userIds": [str(user)]})
        ).status_code == 204
        await drain(database)
        async with database.sessions() as session:
            assert before == await session.scalar(
                select(func.count())
                .select_from(EvidenceSource)
                .where(EvidenceSource.kind == "risk_action")
            )
        audits = (await client.get("/v1.0/auditLogs/directoryAudits", headers=headers)).json()[
            "value"
        ]
        risk_audits = [a for a in audits if a["loggedByService"] == "Identity Protection"]
        assert len(risk_audits) == 2
        assert risk_audits[0]["activityDisplayName"] == "Update user"
        assert {
            d["displayName"] for d in risk_audits[0]["targetResources"][0]["modifiedProperties"]
        } >= {"riskState", "riskDetail"}


async def test_delayed_projection_cannot_undo_dismissal(database, settings):
    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        await observe(database, tenant, user, now - timedelta(seconds=5))
        await drain(database)
        original = record("signIn", user, now - timedelta(seconds=1))
        async with database.sessions.begin() as session:
            ref = SourceRef(tenant_id=tenant, kind="historical", id=uuid4())
            await append_source(
                session,
                SourceInput(
                    ref=ref,
                    occurred_at=now - timedelta(seconds=1),
                    available_at=now,
                    facts={"family": "signIn", "version": "beta", "payload": original},
                ),
            )
        assert (
            await client.post(PATH + "/dismiss", headers=headers, json={"userIds": [str(user)]})
        ).status_code == 204
        from mockazure.evidence.projectors import project_source

        async with database.sessions.begin() as session:
            await publish_batch(session, project_source(await load_source(session, ref)))
        logged = (
            await client.get("/v1.0/auditLogs/signIns/" + original["id"], headers=headers)
        ).json()
        assert logged["riskState"] == "dismissed"
        # Equal-time historical imports remain closed despite a later source revision.
        equal = record("signIn", user, now)
        await publish(database, tenant, "signIn", equal, now)
        assert (await client.get("/v1.0/auditLogs/signIns/" + equal["id"], headers=headers)).json()[
            "riskState"
        ] == "dismissed"
        await observe(database, tenant, user, now + timedelta(seconds=1))
        async with database.sessions.begin() as session:
            await update_settings(
                session,
                tenant,
                EvidenceSettings(
                    delay_seconds=0, clock_mode="fixed", fixed_time=now + timedelta(seconds=2)
                ),
            )
        await drain(database)
        assert (await client.get(PATH + "/" + str(user), headers=headers)).json()[
            "riskState"
        ] == "atRisk"


async def test_risk_role_license_and_atomic_batch(database, settings):
    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        await observe(database, tenant, user, now - timedelta(seconds=1))
        await drain(database)
        read_only = await headers_for(database, settings, tenant, app, None, READ)
        assert (
            await client.post(PATH + "/dismiss", headers=read_only, json={"userIds": [str(user)]})
        ).status_code == 403
        delegated = await headers_for(
            database, settings, tenant, app, user, RISK_WRITE, "delegated"
        )
        await set_role(database, tenant, user, "Security Reader")
        assert (
            await client.post(PATH + "/dismiss", headers=delegated, json={"userIds": [str(user)]})
        ).status_code == 403
        other = (
            await client.post(
                "/_mock/tenants",
                json={"slug": "foreign", "display_name": "Foreign", "domain": "foreign.test"},
            )
        ).json()["id"]
        foreign = (
            await client.post(
                f"/_mock/tenants/{other}/identity/users",
                json={
                    "upn": "foreign@foreign.test",
                    "display_name": "Foreign",
                    "password": "local-foreign-password",
                },
            )
        ).json()["id"]
        async with database.sessions.begin() as session:
            await update_settings(session, UUID(other), EvidenceSettings(delay_seconds=0))
        await observe(database, UUID(other), UUID(foreign), now - timedelta(seconds=1))
        await drain(database)
        for body, status in [
            ({"userIds": [str(user), foreign]}, 404),
            ({"userIds": [str(user), str(uuid4())]}, 404),
            ({"userIds": [str(user), str(user)]}, 400),
            ({"userIds": [str(uuid4()) for _ in range(101)]}, 400),
            ({"userIds": [str(user)], "extra": "no"}, 400),
            ({"userIds": []}, 400),
        ]:
            response = await client.post(PATH + "/dismiss", headers=headers, json=body)
            assert response.status_code == status, response.text
            assert (await client.get(PATH + "/" + str(user), headers=headers)).json()[
                "riskState"
            ] == "atRisk"
        await set_role(database, tenant, user, "Security Administrator")
        assert (
            await client.post(
                PATH + "/confirmCompromised", headers=delegated, json={"userIds": [str(user)]}
            )
        ).status_code == 204
        # Removing effective P2 inventory removes write access even if the app holds the permission.
        from tests.integration.test_graph_licensing import P2, sku_input

        data = sku_input()
        data["status"] = "Suspended"
        assert (
            await client.put(f"/_mock/tenants/{tenant}/licensing/skus/{P2}", json=data)
        ).status_code == 200
        await seed_sku(client, tenant, sku=P1, plan=PLAN1)
        assert (
            await client.post(PATH + "/dismiss", headers=headers, json={"userIds": [str(user)]})
        ).status_code == 403


async def test_observation_idempotency_and_cross_links(database, settings):
    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        request = uuid4()
        first = await observe(database, tenant, user, now, request_id=request)
        second = await observe(database, tenant, user, now, request_id=request)
        assert first == second
        with pytest.raises(AdminError) as error:
            await observe(database, tenant, user, now - timedelta(seconds=1), request_id=request)
        assert error.value.status == 409
        with pytest.raises(AdminError):
            await observe(database, tenant, uuid4(), now)
        foreign = record("signIn", uuid4(), now)
        await publish(database, tenant, "signIn", foreign, now)
        with pytest.raises(AdminError):
            await observe(database, tenant, user, now, sign_in_id=UUID(foreign["id"]))
        await drain(database)
        async with database.sessions() as session:
            from mockazure.identity.models import User

            person = await session.get(User, user)
            assert person.enabled
        # Risk observation is evidence, not a new Conditional Access policy.
        write = await headers_for(database, settings, tenant, app, None, WRITE)
        assert (await client.get("/v1.0/users/" + str(user), headers=write)).status_code == 200


async def test_risk_publication_delay_preserves_visible_aggregate(database, settings):
    from mockazure.db.job_model import Job

    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        await observe(database, tenant, user, now - timedelta(seconds=1), level="low")
        await drain(database)
        async with database.sessions.begin() as session:
            await update_settings(
                session,
                tenant,
                EvidenceSettings(delay_seconds=30, clock_mode="fixed", fixed_time=now),
            )
        request = uuid4()
        await observe(database, tenant, user, now, request_id=request)
        await drain(database)
        response = await client.get(PATH + "/" + str(user), headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["riskLevel"] == "low"
        async with database.sessions() as session:
            job = await session.scalar(
                select(Job).where(Job.tenant_id == tenant, Job.source_key == "risk:" + str(request))
            )
            assert job.state == "pending" and job.attempts == 0
        async with database.sessions.begin() as session:
            await update_settings(
                session,
                tenant,
                EvidenceSettings(
                    delay_seconds=30, clock_mode="fixed", fixed_time=now + timedelta(seconds=30)
                ),
            )
        await drain(database)
        assert (await client.get(PATH + "/" + str(user), headers=headers)).json()[
            "riskLevel"
        ] == "high"


async def test_sparse_v1_signin_can_receive_risk_overlay(database, settings):
    from mockazure.evidence.projectors import project_source

    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        identifier = uuid4()
        payload = {
            "id": str(identifier),
            "userId": str(user),
            "isInteractive": True,
            "createdDateTime": now.isoformat(),
        }
        async with database.sessions.begin() as session:
            ref = SourceRef(tenant_id=tenant, kind="historical", id=uuid4())
            await append_source(
                session,
                SourceInput(
                    ref=ref,
                    occurred_at=now,
                    available_at=now,
                    facts={"family": "signIn", "version": "v1.0", "payload": payload},
                ),
            )
            await publish_batch(session, project_source(await load_source(session, ref)))
        await observe(database, tenant, user, now, sign_in_id=identifier)
        await drain(database)
        response = await client.get(PATH + "/" + str(user), headers=headers)
        assert response.status_code == 200, response.text
        assert (
            await client.get("/v1.0/auditLogs/signIns/" + str(identifier), headers=headers)
        ).json()["riskState"] == "atRisk"
