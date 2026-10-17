from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import insert, update

from mockazure.evidence.models import EvidenceObservation, EvidenceSource, EvidenceVersion
from mockazure.evidence.repository import append_source, load_source, lock_state, publish_batch
from mockazure.evidence.schemas import Projection, ProjectionBatch, SourceInput, SourceRef
from mockazure.providers.graph.schema_reference import validate_payload
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_licensing import P1, PLAN1, seed_sku, set_role

PATHS = {
    "signIn": "auditLogs/signIns",
    "directoryAudit": "auditLogs/directoryAudits",
    "riskDetection": "identityProtection/riskDetections",
    "riskyUser": "identityProtection/riskyUsers",
}
READ = {"AuditLog.Read.All", "IdentityRiskEvent.Read.All", "IdentityRiskyUser.Read.All"}


def record(family, user, when, *, event="interactiveUser", identifier=None, **extra):
    value = {"id": str(identifier or uuid4()), "@odata.type": "#microsoft.graph." + family}
    time = when.isoformat()
    if family == "signIn":
        value.update(
            userId=str(user),
            servicePrincipalId="",
            createdDateTime=time,
            userPrincipalName="reader@contoso.test",
            userDisplayName="Reader",
            isInteractive=event == "interactiveUser",
            signInEventTypes=[event],
            status={"errorCode": 0, "failureReason": None, "additionalDetails": None},
            riskLevelDuringSignIn="high",
            riskLevelAggregated="high",
            riskState="atRisk",
            riskDetail="none",
            riskEventTypes_v2=[],
            ipAddress=None,
            clientAppUsed=None,
        )
    elif family == "directoryAudit":
        value.update(
            activityDateTime=time,
            activityDisplayName="Update user",
            category="UserManagement",
            result="success",
            initiatedBy={"user": {"id": str(user), "userPrincipalName": "O'Brien%_λ@contoso.test"}},
            targetResources=[
                {"id": str(user), "type": "User", "userPrincipalName": "target@contoso.test"}
            ],
            additionalDetails=[],
        )
    elif family == "riskDetection":
        value.update(
            userId=str(user),
            riskEventType="unfamiliarFeatures",
            riskLevel="high",
            riskState="atRisk",
            riskDetail="none",
            activity="signin",
            activityDateTime=time,
            detectedDateTime=time,
            lastUpdatedDateTime=time,
            additionalInfo='[{"Key":"detail","Value":"premium"}]',
        )
    else:
        value.update(
            id=str(user),
            userDisplayName="Reader",
            userPrincipalName="reader@contoso.test",
            isDeleted=False,
            isProcessing=False,
            riskLevel="high",
            riskState="atRisk",
            riskDetail="none",
            riskLastUpdatedDateTime=time,
        )
    value.update(extra)
    validate_payload("beta", family, value)
    return value


async def publish(database, tenant, family, payload, when, *, available=None, version="beta"):
    subject = payload.get("userId") or (payload["id"] if family == "riskyUser" else None)
    async with database.sessions.begin() as session:
        ref = SourceRef(tenant_id=tenant, kind="historical", id=uuid4())
        await append_source(
            session,
            SourceInput(
                ref=ref,
                occurred_at=when,
                available_at=available or when,
                facts={"family": family, "version": version, "payload": payload},
            ),
        )
        source = await load_source(session, ref)
        await publish_batch(
            session,
            ProjectionBatch(
                source=ref,
                generation=source.generation,
                receipt_status="projected",
                items=(
                    Projection(
                        family=family,
                        id=UUID(payload["id"]),
                        occurred_at=when,
                        available_at=available or when,
                        payload=payload,
                        subject_id=UUID(subject) if subject else None,
                        source_ref=ref,
                    ),
                ),
            ),
        )


async def test_routes_query_and_schema_matrix(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        headers = await headers_for(database, settings, tenant, app, None, READ)
        now = datetime.now(UTC) - timedelta(minutes=1)
        for family, path in PATHS.items():
            payload = record(family, user, now)
            await publish(database, tenant, family, payload, now)
            for version in ("v1.0", "beta"):
                response = await client.get(f"/{version}/{path}", headers=headers)
                assert response.status_code == 200, response.text
                values = response.json()["value"]
                assert len(values) == 1
                validate_payload(version, family, values[0])
                one = await client.get(f"/{version}/{path}/{payload['id']}", headers=headers)
                assert one.status_code == 200 and one.json()["id"] == payload["id"]
                assert (
                    await client.get(f"/{version}/{path}?$expand=any", headers=headers)
                ).status_code == 400
        managed = record("signIn", user, now, event="managedIdentity")
        await publish(database, tenant, "signIn", managed, now)
        assert (
            await client.get("/beta/auditLogs/signIns/" + managed["id"], headers=headers)
        ).status_code == 200
        assert (
            await client.get("/v1.0/auditLogs/signIns/" + managed["id"], headers=headers)
        ).status_code == 404
        assert (
            len((await client.get("/beta/auditLogs/signIns", headers=headers)).json()["value"]) == 1
        )
        explicit = "signInEventTypes/any(t:t eq 'interactiveUser' or t eq 'managedIdentity')"
        response = await client.get(
            "/beta/auditLogs/signIns", params={"$filter": explicit}, headers=headers
        )
        assert len(response.json()["value"]) == 2
        assert (
            await client.get(
                "/v1.0/auditLogs/signIns", params={"$filter": explicit}, headers=headers
            )
        ).status_code == 400
        risk = await client.get(
            "/v1.0/identityProtection/riskDetections",
            params={"$select": "riskLevel"},
            headers=headers,
        )
        assert set(risk.json()["value"][0]) == {"id", "@odata.type", "riskLevel"}


async def test_sql_filters_match_original_mes_predicates(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        now = datetime.now(UTC) - timedelta(minutes=1)
        a = record("directoryAudit", user, now)
        b = record(
            "directoryAudit",
            user,
            now - timedelta(days=2),
            initiatedBy={},
            targetResources=[{"id": "outside-window", "type": "User"}],
        )
        await publish(database, tenant, "directoryAudit", a, now)
        await publish(database, tenant, "directoryAudit", b, now - timedelta(days=2))
        expression = (
            "(activityDateTime ge "
            + (now - timedelta(hours=1)).isoformat()
            + " and startsWith(initiatedBy/user/userPrincipalName,'O''Brien%_λ')) or "
            "targetResources/any(t:t/id eq 'outside-window')"
        )
        response = await client.get(
            "/v1.0/auditLogs/directoryAudits", params={"$filter": expression}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert {v["id"] for v in response.json()["value"]} == {a["id"], b["id"]}
        for bad in (
            "targetResources/any(t:t/secret eq 'x')",
            "targetResources/any(t:category eq 'UserManagement')",
        ):
            assert (
                await client.get(
                    "/v1.0/auditLogs/directoryAudits", params={"$filter": bad}, headers=headers
                )
            ).status_code == 400


async def test_self_scope_and_redaction_oracle(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant, sku=P1, plan=PLAN1)
        now = datetime.now(UTC) - timedelta(minutes=1)
        own = record("signIn", user, now)
        other = record("signIn", uuid4(), now)
        await publish(database, tenant, "signIn", own, now)
        await publish(database, tenant, "signIn", other, now)
        headers = await headers_for(
            database, settings, tenant, app, user, {"User.Read"}, "delegated"
        )
        response = await client.get(
            "/v1.0/auditLogs/signIns",
            params={"$filter": f"userId eq '{user}' or userId eq '{other['userId']}'"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert [v["id"] for v in response.json()["value"]] == [own["id"]]
        assert response.json()["value"][0]["riskLevelDuringSignIn"] == "hidden"
        response = await client.get(
            "/v1.0/auditLogs/signIns",
            params={"$filter": "riskLevelDuringSignIn eq 'high'"},
            headers=headers,
        )
        assert response.json()["value"] == []
        assert (
            await client.get("/v1.0/auditLogs/signIns/" + other["id"], headers=headers)
        ).status_code == 404
        await set_role(database, tenant, user, "Security Reader")
        privileged = await headers_for(database, settings, tenant, app, user, READ, "delegated")
        first = (await client.get("/v1.0/auditLogs/signIns?$top=1", headers=privileged)).json()
        assert "@odata.nextLink" in first
        await set_role(database, tenant, user, None)
        assert (await client.get(first["@odata.nextLink"], headers=privileged)).status_code == 400
        app_headers = await headers_for(database, settings, tenant, app, None, READ)
        first = (await client.get("/v1.0/auditLogs/signIns?$top=1", headers=app_headers)).json()
        await seed_sku(client, tenant)
        assert (await client.get(first["@odata.nextLink"], headers=app_headers)).status_code == 400


async def test_evidence_snapshot_late_publish_and_risk_version(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        now = datetime.now(UTC) - timedelta(minutes=1)
        values = [record("signIn", user, now) for _ in range(10000)]
        # Bulk fixture uses the same committed revision and immutable/version tables.
        async with database.sessions.begin() as session:
            state = await lock_state(session, tenant)
            state.source_revision += 1
            state.publication_revision += 1
            sid = uuid4()
            session.add(
                EvidenceSource(
                    tenant_id=tenant,
                    kind="historical",
                    id=sid,
                    generation=state.generation,
                    source_revision=state.source_revision,
                    occurred_at=now,
                    available_at=now,
                    facts={"version": "beta"},
                    content_hash="0" * 64,
                )
            )
            await session.flush()
            await session.execute(
                insert(EvidenceObservation),
                [
                    dict(
                        tenant_id=tenant,
                        family="signIn",
                        record_id=UUID(v["id"]),
                        source_kind="historical",
                        source_id=sid,
                        occurred_at=now,
                        available_at=now,
                        subject_id=user,
                        payload=v,
                    )
                    for v in values
                ],
            )
            await session.execute(
                insert(EvidenceVersion),
                [
                    dict(
                        tenant_id=tenant,
                        family="signIn",
                        record_id=UUID(v["id"]),
                        generation=state.generation,
                        source_kind="historical",
                        source_id=sid,
                        valid_from=state.publication_revision,
                        event_time=now,
                        available_at=now,
                        subject_id=user,
                        event_type="interactiveUser",
                        payload=v,
                    )
                    for v in values
                ],
            )
        headers = await headers_for(database, settings, tenant, app, None, READ)
        response = await client.get("/v1.0/auditLogs/signIns", headers=headers)
        assert response.status_code == 200, response.text
        first = response.json()
        assert len(first["value"]) == 1000
        link = first["@odata.nextLink"]
        late = record("signIn", user, now - timedelta(seconds=1))
        await publish(database, tenant, "signIn", late, now - timedelta(seconds=1))
        future = record("signIn", user, now)
        await publish(database, tenant, "signIn", future, now, available=now + timedelta(days=1))
        # Overlay an unread record after snapshot creation; old property set must survive.
        target = max(values, key=lambda v: v["id"])
        async with database.sessions.begin() as session:
            state = await lock_state(session, tenant)
            state.publication_revision += 1
            revision = state.publication_revision
            await session.execute(
                update(EvidenceVersion)
                .where(
                    EvidenceVersion.tenant_id == tenant,
                    EvidenceVersion.record_id == UUID(target["id"]),
                    EvidenceVersion.valid_to.is_(None),
                )
                .values(valid_to=revision)
            )
            session.add(
                EvidenceVersion(
                    tenant_id=tenant,
                    family="signIn",
                    record_id=UUID(target["id"]),
                    generation=state.generation,
                    source_kind="historical",
                    source_id=sid,
                    valid_from=revision,
                    event_time=now,
                    available_at=now,
                    subject_id=user,
                    event_type="interactiveUser",
                    payload={**target, "riskState": "dismissed"},
                )
            )
        from mockazure.admin.app import create_app

        replica = create_app(settings, database)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=replica), base_url=str(client.base_url)
        ) as second:
            replay = (await second.get(link, headers=headers)).json()
            assert replay == (await second.get(link, headers=headers)).json()
            actual = list(first["value"])
            while link:
                page = (await second.get(link, headers=headers)).json()
                actual.extend(page["value"])
                link = page.get("@odata.nextLink")
        assert [v["id"] for v in actual] == sorted(v["id"] for v in values)
        assert all(v["riskState"] == "atRisk" for v in actual)
        replica.state.clock = lambda: datetime.now(UTC) + timedelta(hours=2)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=replica), base_url=str(client.base_url)
        ) as second:
            assert (await second.get(first["@odata.nextLink"], headers=headers)).status_code == 400


async def test_evidence_batch_and_budget(database, settings):
    from mockazure.providers.graph.throttle import consume_budget

    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        headers = await headers_for(database, settings, tenant, app, None, READ | {"User.Read.All"})
        fixed_now = datetime.now(UTC).replace(second=5, microsecond=0)
        client._transport.app.state.clock = lambda: fixed_now
        for family, path in PATHS.items():
            response = await client.post(
                f"/_mock/tenants/{tenant}/graph/faults",
                json={
                    "client_id": str(app),
                    "resource": family,
                    "kind": "next_read_429",
                    "retry_after": 7,
                },
            )
            assert response.status_code == 201, response.text
            throttled = await client.get("/v1.0/" + path, headers=headers)
            assert throttled.status_code == 429 and throttled.headers["Retry-After"] == "7"
            assert (await client.get("/v1.0/" + path, headers=headers)).status_code == 200
        batch = await client.post(
            "/v1.0/$batch",
            headers=headers,
            json={
                "requests": [
                    {"id": "1", "method": "GET", "url": "/users"},
                    {"id": "2", "method": "GET", "url": "/auditLogs/signIns"},
                    {"id": "3", "method": "GET", "url": "/identityProtection/riskyUsers"},
                ]
            },
        )
        assert batch.status_code == 200, batch.text
        assert [x["status"] for x in batch.json()["responses"]] == [200, 200, 200]
        async with database.sessions.begin() as session:
            for _ in range(600):
                await consume_budget(session, tenant, app, "signIn", fixed_now, 600, 60)
        assert (await client.get("/v1.0/auditLogs/signIns", headers=headers)).status_code == 429
        assert (
            await client.get("/v1.0/auditLogs/directoryAudits", headers=headers)
        ).status_code == 200


async def test_effective_user_licence_masks_sql_and_sparse_history(database, settings):
    from tests.integration.test_graph_licensing import assign
    from tests.integration.test_graph_mutations import WRITE

    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        now = datetime.now(UTC) - timedelta(minutes=1)
        raw = record(
            "signIn", user, now, riskEventTypes_v2=["unfamiliarFeatures", "leakedCredentials"]
        )
        await publish(database, tenant, "signIn", raw, now)
        detection = record("riskDetection", user, now)
        await publish(database, tenant, "riskDetection", detection, now)
        headers = await headers_for(database, settings, tenant, app, None, READ)
        value = (await client.get("/v1.0/auditLogs/signIns", headers=headers)).json()["value"][0]
        assert value["riskEventTypes_v2"] == ["generic", "leakedCredentials"]
        response = await client.get(
            "/v1.0/identityProtection/riskDetections",
            params={"$filter": "riskEventType eq 'unfamiliarFeatures'"},
            headers=headers,
        )
        assert response.json()["value"] == []
        response = await client.get(
            "/v1.0/identityProtection/riskDetections",
            params={"$filter": "riskEventType eq 'generic'"},
            headers=headers,
        )
        assert response.json()["value"][0]["additionalInfo"] is None
        write = await headers_for(database, settings, tenant, app, None, WRITE)
        assert (
            await client.patch(f"/v1.0/users/{user}", headers=write, json={"usageLocation": "US"})
        ).status_code == 204
        assert (
            await client.post(f"/v1.0/users/{user}/assignLicense", headers=write, json=assign())
        ).status_code == 200
        response = await client.get(
            "/v1.0/auditLogs/signIns",
            params={"$filter": "riskLevelDuringSignIn eq 'high'"},
            headers=headers,
        )
        assert [v["id"] for v in response.json()["value"]] == [raw["id"]]
        # v1 imports need a version adapter for beta's nonnullable SP field, not a false UUID.
        sparse = {
            "id": str(uuid4()),
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
                    facts={"version": "v1.0", "family": "signIn", "payload": sparse},
                ),
            )
            from mockazure.evidence.projectors import project_source

            await publish_batch(session, project_source(await load_source(session, ref)))
        response = await client.get("/beta/auditLogs/signIns/" + sparse["id"], headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["servicePrincipalId"] == ""
        validate_payload("beta", "signIn", response.json())


async def test_evidence_permissions_roles_and_licence_matrix(database, settings):
    from mockazure.providers.graph.evidence.permissions import PERMISSIONS, ROLES

    async with graph_client(database, settings) as (client, tenant, app, user):
        all_headers = await headers_for(database, settings, tenant, app, None, READ)
        for family, path in PATHS.items():
            expected = 200 if family == "directoryAudit" else 403
            assert (await client.get("/v1.0/" + path, headers=all_headers)).status_code == expected
        await seed_sku(client, tenant, sku=P1, plan=PLAN1)
        assert (
            await client.get("/v1.0/identityProtection/riskyUsers", headers=all_headers)
        ).status_code == 403
        await seed_sku(client, tenant)
        for family, path in PATHS.items():
            for permission in PERMISSIONS[family]:
                application = await headers_for(database, settings, tenant, app, None, {permission})
                assert (await client.get("/v1.0/" + path, headers=application)).status_code == 200
                for role in ROLES[family] | {"Global Administrator"}:
                    await set_role(database, tenant, user, role)
                    delegated = await headers_for(
                        database, settings, tenant, app, user, {permission}, "delegated"
                    )
                    assert (await client.get("/v1.0/" + path, headers=delegated)).status_code == 200
                await set_role(database, tenant, user, None)
                expected = 200 if family == "signIn" else 403
                assert (
                    await client.get("/v1.0/" + path, headers=delegated)
                ).status_code == expected
        denied = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        for path in PATHS.values():
            assert (
                await client.get("/v1.0/" + path + "/" + str(uuid4()), headers=denied)
            ).status_code == 403
        batch = await client.post(
            "/v1.0/$batch",
            headers=denied,
            json={
                "requests": [
                    {"id": "a", "method": "GET", "url": "/users"},
                    {"id": "b", "method": "GET", "url": "/auditLogs/signIns"},
                ]
            },
        )
        assert [v["status"] for v in batch.json()["responses"]] == [200, 403]


async def test_sparse_v1_interactive_record_matches_beta_event_filter(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        headers = await headers_for(database, settings, tenant, app, None, READ)
        now = datetime.now(UTC) - timedelta(minutes=1)
        payload = record("signIn", user, now)
        payload.pop("signInEventTypes")
        payload.pop("servicePrincipalId")
        await publish(database, tenant, "signIn", payload, now, version="v1.0")
        response = await client.get(
            "/beta/auditLogs/signIns",
            params={"$filter": "signInEventTypes/any(t: t eq 'interactiveUser')"},
            headers=headers,
        )
        assert response.status_code == 200
        assert [row["id"] for row in response.json()["value"]] == [payload["id"]]
        assert response.json()["value"][0]["signInEventTypes"] == ["interactiveUser"]
        v1 = await client.get("/v1.0/auditLogs/signIns/" + payload["id"], headers=headers)
        assert "signInEventTypes" not in v1.json()
        # A false or unknown flag cannot establish a workload/user event type.
        for flag in (False, None):
            other = record("signIn", user, now, isInteractive=flag)
            other.pop("signInEventTypes")
            await publish(database, tenant, "signIn", other, now, version="v1.0")
        response = await client.get(
            "/beta/auditLogs/signIns",
            params={"$filter": "signInEventTypes/any(t: t eq 'nonInteractiveUser')"},
            headers=headers,
        )
        assert response.json()["value"] == []
