import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import func, select

from mockazure.directory.history import prune_history
from mockazure.directory.models import EvidenceOutbox
from mockazure.directory.mutations import AdminActor, DirectoryCommand, mutate_directory
from mockazure.evidence.models import EvidenceObservation, EvidenceSource, ProjectionReceipt
from mockazure.identity.models import Application
from mockazure.jobs.worker import run_one
from mockazure.providers.graph.schema_reference import validate_payload
from tests.integration.evidence_support import tenant
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_mutations import WRITE, group_body, user_body


async def drain(database):
    for _ in range(100):
        if not await run_one(database, owner=uuid4(), now=datetime.now(UTC) + timedelta(seconds=1)):
            return
    pytest.fail("Evidence did not drain")


async def graph_audits(database):
    async with database.sessions() as session:
        rows = (
            await session.execute(
                select(EvidenceObservation, EvidenceSource)
                .join(
                    EvidenceSource,
                    (EvidenceSource.tenant_id == EvidenceObservation.tenant_id)
                    & (EvidenceSource.kind == EvidenceObservation.source_kind)
                    & (EvidenceSource.id == EvidenceObservation.source_id),
                )
                .where(EvidenceSource.facts["provenance"].as_string() == "graph")
                .order_by(EvidenceSource.source_revision)
            )
        ).all()
        return [(record.payload, source.facts) for record, source in rows]


async def test_directory_audit_mapping(database, settings):
    async with graph_client(database, settings) as (client, tid, app, actor):
        headers = await headers_for(database, settings, tid, app, None, WRITE)
        created = await client.post("/v1.0/users", headers=headers, json=user_body("audit"))
        assert created.status_code == 201
        uid = created.json()["id"]
        for patch in [
            {"accountEnabled": False},
            {"accountEnabled": True},
            {"displayName": "Renamed", "accountEnabled": False},
        ]:
            assert (
                await client.patch(f"/v1.0/users/{uid}", headers=headers, json=patch)
            ).status_code == 204
        group = await client.post("/v1.0/groups", headers=headers, json=group_body("AuditGroup"))
        assert group.status_code == 201
        gid = group.json()["id"]
        assert (
            await client.patch(
                f"/v1.0/groups/{gid}", headers=headers, json={"description": "Updated"}
            )
        ).status_code == 204
        assert (
            await client.post(
                f"/v1.0/groups/{gid}/members/$ref",
                headers=headers,
                json={
                    "@odata.id": str(settings.endpoints.graph).rstrip("/")
                    + f"/v1.0/directoryObjects/{uid}"
                },
            )
        ).status_code == 204
        assert (
            await client.delete(f"/v1.0/groups/{gid}/members/{uid}/$ref", headers=headers)
        ).status_code == 204
        assert (await client.delete(f"/v1.0/users/{uid}", headers=headers)).status_code == 204
        assert (await client.delete(f"/v1.0/groups/{gid}", headers=headers)).status_code == 204
    await drain(database)
    audits = await graph_audits(database)
    assert [a["activityDisplayName"] for a, _ in audits] == [
        "Add user",
        "Disable account",
        "Enable account",
        "Update user",
        "Add group",
        "Update group",
        "Add member to group",
        "Remove member from group",
        "Hard Delete user",
        "Delete group",
    ]
    for audit, facts in audits:
        validate_payload("v1.0", "directoryAudit", audit)
        assert audit["result"] == "success" and audit["initiatedBy"]["app"]["appId"] == str(app)
        assert UUID(audit["correlationId"])
        assert facts["schema_version"] == 2
        for target in audit["targetResources"]:
            for difference in target["modifiedProperties"]:
                json.loads(difference["oldValue"])
                json.loads(difference["newValue"])
    assert audits[0][0]["correlationId"] == created.headers["x-request-id"]
    membership = audits[6][0]
    assert {t["id"] for t in membership["targetResources"]} == {uid, gid}
    serialized = json.dumps(audits)
    assert "passwordProfile" not in serialized and "local-directory-password" not in serialized


async def test_rename_delete_history_cleanup_keeps_event_labels(database, settings):
    async with graph_client(database, settings) as (client, tid, app, actor):
        headers = await headers_for(database, settings, tid, app, None, WRITE)
        response = await client.post("/v1.0/users", headers=headers, json=user_body("original"))
        uid = response.json()["id"]
        async with database.sessions.begin() as session:
            application = await session.get(Application, app)
            original_app_name = application.display_name
            application.display_name = "Later app"
        assert (
            await client.patch(
                f"/v1.0/users/{uid}", headers=headers, json={"displayName": "Later target"}
            )
        ).status_code == 204
        assert (await client.delete(f"/v1.0/users/{uid}", headers=headers)).status_code == 204
        async with database.sessions.begin() as session:
            await prune_history(session, tid, datetime.now(UTC) + timedelta(days=1))
    await drain(database)
    audits = await graph_audits(database)
    first = audits[0][0]
    assert first["initiatedBy"]["app"]["displayName"] == original_app_name
    assert first["targetResources"][0]["displayName"] == "original"
    assert audits[1][0]["initiatedBy"]["app"]["displayName"] == "Later app"
    assert audits[-1][0]["targetResources"][0]["displayName"] == "Later target"


async def test_import_admin_and_noop_origins(database):
    async with database.sessions.begin() as session:
        tid = await tenant(session, "contoso")
        created = await mutate_directory(
            session,
            tid,
            DirectoryCommand("user", "create", None, user_body("admin")),
            AdminActor(),
            datetime.now(UTC),
        )
        uid = created.object.id
        imported = EvidenceOutbox(
            tenant_id=tid,
            object_id=tid,
            kind="directory",
            revision=100,
            facts={
                "schema_version": 1,
                "operation": "import",
                "provenance": {"source": "test"},
                "object_count": 0,
                "relationship_count": 0,
                "target_ids": [],
            },
        )
        session.add(imported)
        await session.flush()
        import_id = imported.id
    async with database.sessions.begin() as session:
        await mutate_directory(
            session,
            tid,
            DirectoryCommand("user", "update", uid, {"displayName": "admin"}),
            AdminActor(),
            datetime.now(UTC),
        )
    with pytest.raises(RuntimeError):
        async with database.sessions.begin() as session:
            await mutate_directory(
                session,
                tid,
                DirectoryCommand("user", "update", uid, {"displayName": "rolled back"}),
                AdminActor(),
                datetime.now(UTC),
            )
            raise RuntimeError("rollback")
    await drain(database)
    async with database.sessions() as session:
        records = list(await session.scalars(select(EvidenceObservation)))
        assert len(records) == 1 and records[0].payload["initiatedBy"] == {}
        receipts = list(await session.scalars(select(ProjectionReceipt)))
        assert len(receipts) == 3
        assert (
            await session.get(ProjectionReceipt, (tid, "directory", import_id))
        ).status == "suppressed"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(EvidenceOutbox)
                .where(EvidenceOutbox.projected_at.is_(None))
            )
            == 0
        )
        sources = list(await session.scalars(select(EvidenceSource)))
        assert "rolled back" not in json.dumps([s.facts for s in sources])


async def test_legacy_pending_fact_adapter(database):
    async with database.sessions.begin() as session:
        tid = await tenant(session)
        uid, old_actor = uuid4(), uuid4()
        row = EvidenceOutbox(
            tenant_id=tid,
            object_id=uid,
            kind="user",
            revision=1,
            facts={
                "schema_version": 1,
                "operation": "update",
                "actor_id": str(old_actor),
                "client_id": str(old_actor),
                "provenance": "graph",
                "modified_properties": {"displayName": {"before": "Before", "after": "After"}},
            },
        )
        session.add(row)
        await session.flush()
        source_id = row.id
    await drain(database)
    await drain(database)
    async with database.sessions() as session:
        records = list(await session.scalars(select(EvidenceObservation)))
        assert len(records) == 1
        audit = records[0].payload
        assert audit["id"] == str(uuid5(source_id, "mockazure:m3b:directoryAudit:0"))
        assert audit["correlationId"] == str(uuid5(source_id, "correlation"))
        assert audit["initiatedBy"] == {}
        target = audit["targetResources"][0]
        assert target["id"] == str(uid) and target.get("displayName") is None
        assert target["modifiedProperties"] == [
            {"displayName": "displayName", "oldValue": '"Before"', "newValue": '"After"'}
        ]
        receipt = await session.get(ProjectionReceipt, (tid, "directory", source_id))
        assert receipt is not None


async def test_delegated_actor_initial_members_and_delete_cascade(database, settings):
    from mockazure.identity.models import DirectoryRole

    async with graph_client(database, settings) as (client, tid, app, actor):
        async with database.sessions.begin() as session:
            session.add(DirectoryRole(tenant_id=tid, user_id=actor, role="User Administrator"))
        headers = await headers_for(database, settings, tid, app, actor, WRITE, kind="delegated")
        response = await client.post("/v1.0/users", headers=headers, json=user_body("member"))
        assert response.status_code == 201
        member = response.json()["id"]
        body = group_body("Initial")
        body["members@odata.bind"] = [settings.endpoints.graph + "/v1.0/directoryObjects/" + member]
        response = await client.post("/v1.0/groups", headers=headers, json=body)
        assert response.status_code == 201
        assert (await client.delete(f"/v1.0/users/{member}", headers=headers)).status_code == 204
        denied = await headers_for(database, settings, tid, app, None, {"User.Read.All"})
        assert (
            await client.post("/v1.0/users", headers=denied, json=user_body("denied"))
        ).status_code == 403
    await drain(database)
    audits = await graph_audits(database)
    assert [a["activityDisplayName"] for a, _ in audits] == [
        "Add user",
        "Add group",
        "Hard Delete user",
    ]
    for audit, _ in audits:
        assert audit["initiatedBy"] == {
            "user": {
                "id": str(actor),
                "displayName": "Directory Reader",
                "userPrincipalName": "reader@contoso.test",
            }
        }
    difference = next(
        v
        for v in audits[1][0]["targetResources"][0]["modifiedProperties"]
        if v["displayName"] == "members"
    )
    assert json.loads(difference["oldValue"]) == []
    assert json.loads(difference["newValue"]) == [member]


async def test_legacy_scanner_shares_bound_and_unknown_activity_is_not_fabricated(database):
    from mockazure.db.job_model import Job
    from mockazure.evidence.repository import append_source
    from mockazure.evidence.sources import scan_sources
    from tests.integration.evidence_support import source

    async with database.sessions.begin() as session:
        tid = await tenant(session)
        row = EvidenceOutbox(
            tenant_id=tid,
            object_id=uuid4(),
            kind="user",
            revision=1,
            facts={"operation": "fixture", "provenance": "test"},
        )
        session.add(row)
        await session.flush()
        old_id = row.id
        await append_source(session, source(tid))
    async with database.sessions.begin() as session:
        assert await scan_sources(session, limit=1) == 1
        assert await session.scalar(select(func.count()).select_from(Job)) == 1
    await drain(database)
    async with database.sessions() as session:
        receipt = await session.get(ProjectionReceipt, (tid, "directory", old_id))
        assert receipt.status == "legacy_incomplete" and receipt.record_ids == []
