import pytest
from sqlalchemy import select

from mockazure.evidence.models import EvidenceSource
from mockazure.evidence.projectors import project_source
from mockazure.evidence.repository import append_source, load_source, publish_batch
from mockazure.evidence.schemas import SourceInput
from tests.integration.evidence_support import tenant, visible_record_ids
from tests.unit.test_operation_evidence_contracts import workload


async def test_commit_and_source_are_atomic(database):
    from mockazure.db.models import Tenant

    async with database.sessions.begin() as session:
        tid = await tenant(session)
    source = workload("drive.upload", {}, {"id": "file", "name": "plan.docx"})
    value = SourceInput(**source.model_dump(exclude={"source_revision", "generation"}))
    value = value.model_copy(update={"ref": value.ref.model_copy(update={"tenant_id": tid})})
    with pytest.raises(RuntimeError, match="rollback"):
        async with database.sessions.begin() as session:
            row = await session.get(Tenant, tid)
            row.display_name = "uncommitted mutation"
            await append_source(session, value)
            raise RuntimeError("rollback")
    async with database.sessions() as session:
        assert (await session.get(Tenant, tid)).display_name == "evidence"
        assert (
            list(
                await session.scalars(select(EvidenceSource).where(EvidenceSource.tenant_id == tid))
            )
            == []
        )


async def test_committed_action_projects_once(database):
    async with database.sessions.begin() as session:
        tid = await tenant(session)
        source = workload("drive.upload", {}, {"id": "file", "name": "plan.docx"})
        value = SourceInput(**source.model_dump(exclude={"source_revision", "generation"}))
        value = value.model_copy(update={"ref": value.ref.model_copy(update={"tenant_id": tid})})
        await append_source(session, value)
    async with database.sessions.begin() as session:
        batch = project_source(await load_source(session, value.ref))
        first = await publish_batch(session, batch)
    async with database.sessions.begin() as session:
        retry = await publish_batch(session, batch)
        collected = await visible_record_ids(session, tid)
    assert not first.already_applied and retry.already_applied
    assert len(collected) == 1
    assert collected == list(first.record_ids)


async def test_public_file_change_and_rollback_do_not_disagree(database, settings):
    from unittest.mock import patch

    from mockazure.workloads import drive_mutations
    from tests.integration.graph_support import graph_client, headers_for
    from tests.integration.test_connected_collaboration import populate

    async with graph_client(database, settings) as (client, tid, app, _):
        _, data = await populate(database, settings, tid)
        auth = await headers_for(database, settings, tid, app, None, ["Files.ReadWrite.All"])
        drive, item = data["drives"].items[0].id, data["documents"].items[0].id
        path = f"/v1.0/drives/{drive}/items/{item}"
        original = (await client.get(path, headers=auth)).json()
        with patch.object(drive_mutations, "facts", side_effect=ValueError("rollback")):
            response = await client.patch(path, headers=auth, json={"name": "rolled-back.pdf"})
        assert response.status_code >= 400
        assert (await client.get(path, headers=auth)).json()["name"] == original["name"]
        async with database.sessions() as session:
            assert (
                list(
                    await session.scalars(
                        select(EvidenceSource).where(
                            EvidenceSource.tenant_id == tid, EvidenceSource.kind == "workload"
                        )
                    )
                )
                == []
            )
        response = await client.patch(path, headers=auth, json={"name": "committed.pdf"})
        assert response.status_code == 200, response.text
        async with database.sessions.begin() as session:
            row = await session.scalar(
                select(EvidenceSource).where(
                    EvidenceSource.tenant_id == tid, EvidenceSource.kind == "workload"
                )
            )
            from mockazure.evidence.schemas import SourceRef

            source = await load_source(
                session, SourceRef(tenant_id=tid, kind="workload", id=row.id)
            )
            batch = project_source(source)
            result = await publish_batch(session, batch)
            assert len(result.record_ids) == 1
            assert batch.items[0].payload["objectId"] == item
            assert batch.items[0].payload["operation"] == "FileRenamed"


async def test_namespace_reads_have_distinct_diagnostic_facts(database, settings):
    from mockazure.cloud.models import CloudEvent
    from tests.integration.graph_support import graph_client
    from tests.integration.test_blob_workflows import call
    from tests.integration.test_diagnostic_delivery import VERSION, drain, setup

    async with graph_client(database, settings) as (client, tid, app, _):
        account, _, path, body, arm, endpoints, key, wid, logs = await setup(
            client, database, settings, tid, app
        )
        body["properties"]["logs"] = [{"category": "StorageRead", "enabled": True}]
        for service in ("blob", "file"):
            target = path if service == "blob" else path.replace("/blobServices/", "/fileServices/")
            assert (
                await client.put(target, headers=arm, params=VERSION, json=body)
            ).status_code == 200
            args = {"restype": "container" if service == "blob" else "share"}
            url = endpoints[service] + "evidence"
            assert (await call(client, "PUT", url, key, args)).status_code == 201
            # Same client correlation cannot deduplicate independent successful reads.
            for _ in range(2):
                response = await call(
                    client,
                    "GET",
                    url,
                    key,
                    args | {"comp": "acl"},
                    extra={"x-ms-client-request-id": "same-correlation"},
                )
                assert response.status_code == 200, response.text
        response = await call(
            client, "GET", endpoints["file"], key, {"comp": "list", "include": "snapshots"}
        )
        assert response.status_code == 200, response.text
        async with database.sessions() as session:
            rows = list(
                await session.scalars(
                    select(CloudEvent).where(
                        CloudEvent.tenant_id == tid, CloudEvent.category == "StorageRead"
                    )
                )
            )
        assert sorted(row.operation for row in rows) == [
            "GetContainerACL",
            "GetContainerACL",
            "GetShareACL",
            "GetShareACL",
            "ListShares",
        ]
        assert len({row.id for row in rows}) == 5
        results = await drain(database, tid)
        assert len(results) == 10 and all(r.state == "succeeded" for r in results)
        for table, expected in [("StorageBlobLogs", 2), ("StorageFileLogs", 3)]:
            result = await client.post(
                settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
                headers=logs,
                json={"query": table},
            )
            assert result.status_code == 200, result.text
            assert len(result.json()["tables"][0]["rows"]) == expected
