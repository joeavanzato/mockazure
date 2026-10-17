import asyncio
import copy
import json
import os
import ssl
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import event, func, insert, select, update

from mockazure.admin.app import create_app
from mockazure.bootstrap import initialize_instance
from mockazure.config import load_settings
from mockazure.db.engine import close_database, create_database
from mockazure.db.job_model import Job
from mockazure.db.migrations import migrate
from mockazure.directory.models import DirectoryState
from mockazure.evidence.models import (
    EvidenceCursor,
    EvidenceObservation,
    EvidenceSource,
    EvidenceVersion,
)
from mockazure.evidence.repository import lock_state
from tests.integration.conftest import isolated_database_url
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.identity_support import identity_client
from tests.integration.test_graph_evidence_reads import READ, record
from tests.integration.test_graph_licensing import seed_sku
from tests.integration.test_managed_identity import free_port

PROVENANCE = {
    "source": "synthetic-test",
    "description": "Local seeded evidence",
    "seed": 42,
    "generator": "test-v1",
}


def dataset(values):
    return {"schema_version": 1, "provenance": PROVENANCE, "records": values}


def imported(when, user=None, family="riskDetection", identifier=None):
    payload = record(family, user or uuid4(), when, identifier=identifier)
    if family == "signIn":
        for field in (
            "signInEventTypes",
            "authenticationProtocol",
            "authenticationRequirement",
            "authenticationDetails",
        ):
            payload.pop(field, None)
    return {
        "family": family,
        "api_version": "v1.0",
        "payload": payload,
        "occurred_at": when.isoformat(),
        "available_at": when.isoformat(),
        "origin": "historical",
        "entitlement_snapshot": "P2",
    }


async def configure(client, tenant, now, retention=30):
    response = await client.patch(
        f"/_mock/tenants/{tenant}/evidence/settings",
        json={
            "delay_seconds": 0,
            "retention_days": retention,
            "clock_mode": "fixed",
            "fixed_time": now.isoformat(),
        },
    )
    assert response.status_code == 200, response.text


async def drain_api(client, tenant):
    response = await client.post(f"/_mock/tenants/{tenant}/evidence/drain", json={"max_jobs": 1000})
    assert response.status_code == 200, response.text
    assert response.json()["failed"] == 0, response.text


async def source_count(database):
    async with database.sessions() as session:
        return await session.scalar(select(func.count()).select_from(EvidenceSource))


async def test_dataset_roundtrip_and_conflict_atomicity(database, settings):
    async with graph_client(database, settings) as (client, tenant, *_):
        now = datetime.now(UTC)
        await configure(client, tenant, now)
        values = [imported(now - timedelta(seconds=2)), imported(now - timedelta(seconds=1))]
        path = f"/_mock/tenants/{tenant}/evidence"
        first = await client.post(path + "/import", json=dataset(values))
        assert first.status_code == 200, first.text
        assert first.json()["inserted"] == 2
        replay = await client.post(path + "/import", json=dataset(values))
        assert replay.json()["unchanged"] == 2
        before = await source_count(database)
        bad = copy.deepcopy(values[0])
        bad["payload"]["riskLevel"] = "low"
        rejected = await client.post(path + "/import", json=dataset([imported(now), bad]))
        assert rejected.status_code == 409, rejected.text
        assert await source_count(database) == before
        for field, value in [
            ("access_token", "secret"),
            ("userDisplayName", "bad\0text"),
            ("appliedConditionalAccessPolicies", []),
        ]:
            invalid = imported(now)
            invalid["payload"][field] = value
            assert (
                await client.post(path + "/import", json=dataset([imported(now), invalid]))
            ).status_code == 422
            assert await source_count(database) == before
        assert (
            await client.post(
                path + "/import",
                content=b"x" * (10 * 1024 * 1024 + 1),
                headers={"Content-Type": "application/json"},
            )
        ).status_code == 413
        assert (
            await client.post(path + "/import", json=dataset([values[0]] * 10001))
        ).status_code in (413, 422)
        await drain_api(client, tenant)
        exported = (await client.get(path + "/export?family=riskDetection")).json()
        assert {r["payload"]["id"] for r in exported["records"]} == {
            r["payload"]["id"] for r in values
        }
        assert all(r["provenance"] == PROVENANCE for r in exported["records"])
        with isolated_database_url() as url:
            migrate(url)
            fresh_settings = settings.model_copy(
                update={"database_url": __import__("pydantic").SecretStr(url)}
            )
            fresh = create_database(fresh_settings)
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(fresh_settings, fresh)),
                    base_url=str(client.base_url),
                    headers=dict(client.headers),
                ) as other:
                    new = (
                        await other.post(
                            "/_mock/tenants",
                            json={
                                "slug": "restored",
                                "display_name": "Restored",
                                "domain": "restored.test",
                            },
                        )
                    ).json()["id"]
                    await configure(other, new, now)
                    response = await other.post(
                        f"/_mock/tenants/{new}/evidence/import", json=exported
                    )
                    assert response.status_code == 200, response.text
                    await drain_api(other, new)
                    result = (
                        await other.get(
                            f"/_mock/tenants/{new}/evidence/export?family=riskDetection"
                        )
                    ).json()
                    assert result["records"] == exported["records"]
            finally:
                await close_database(fresh)


async def bulk_fixture(database, tenant, user, now, count=1200):
    values = [record("directoryAudit", user, now) for _ in range(count)]
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
                acknowledged_at=now,
                content_hash="0" * 64,
                facts={
                    "family": "directoryAudit",
                    "version": "v1.0",
                    "payload": values[0],
                    "provenance": PROVENANCE,
                },
            )
        )
        await session.flush()
        await session.execute(
            insert(EvidenceObservation),
            [
                dict(
                    tenant_id=tenant,
                    family="directoryAudit",
                    record_id=UUID(v["id"]),
                    source_kind="historical",
                    source_id=sid,
                    occurred_at=now,
                    available_at=now,
                    subject_id=None,
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
                    family="directoryAudit",
                    record_id=UUID(v["id"]),
                    generation=state.generation,
                    source_kind="historical",
                    source_id=sid,
                    valid_from=state.publication_revision,
                    event_time=now,
                    available_at=now,
                    subject_id=None,
                    payload=v,
                )
                for v in values
            ],
        )
    return values


async def test_export_stream_snapshot_under_change(database, settings):
    from mockazure.evidence.datasets import EvidenceExportQuery, export_evidence

    async with graph_client(database, settings) as (client, tenant, app, user):
        now = datetime.now(UTC)
        await configure(client, tenant, now)
        values = await bulk_fixture(database, tenant, user, now - timedelta(seconds=1))
        stream = export_evidence(
            database.sessions, tenant, EvidenceExportQuery(family="directoryAudit")
        )
        prefix = await anext(stream)
        async with database.sessions() as session:
            pins = await session.scalar(select(func.count()).select_from(EvidenceCursor))
            assert pins == 1
        # Changes after the export pin cannot enter its frozen publication revision.
        added = imported(now, family="directoryAudit")
        assert (
            await client.post(f"/_mock/tenants/{tenant}/evidence/import", json=dataset([added]))
        ).status_code == 200
        await drain_api(client, tenant)
        await configure(client, tenant, now + timedelta(days=40))
        cleanup = await client.post(
            f"/_mock/tenants/{tenant}/evidence/cleanup", json={"max_rows": 1000}
        )
        assert cleanup.status_code == 200, cleanup.text
        fetched = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            if "FROM evidence_versions" in statement and "JOIN evidence_sources" in statement:
                fetched.append(parameters)

        event.listen(database.engine.sync_engine, "before_cursor_execute", capture)
        try:
            content = prefix + b"".join([part async for part in stream])
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", capture)
        assert len(fetched) == 4
        assert all(500 in parameters.values() for parameters in fetched)
        exported = json.loads(content)
        assert {r["payload"]["id"] for r in exported["records"]} == {v["id"] for v in values}
        async with database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(EvidenceCursor)) == 0
        abandoned = export_evidence(database.sessions, tenant, EvidenceExportQuery())
        await anext(abandoned)
        await abandoned.aclose()
        async with database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(EvidenceCursor)) == 0


async def test_cleanup_retention_and_no_resurrection(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        now = datetime.now(UTC)
        await configure(client, tenant, now, retention=1)
        await seed_sku(client, tenant)
        values = [imported(now - timedelta(seconds=1), family="signIn") for _ in range(3)]
        path = f"/_mock/tenants/{tenant}/evidence"
        assert (await client.post(path + "/import", json=dataset(values))).status_code == 200
        await drain_api(client, tenant)
        headers = await headers_for(database, settings, tenant, app, None, READ)
        first = (await client.get("/v1.0/auditLogs/signIns?$top=1", headers=headers)).json()
        link = first["@odata.nextLink"]
        await configure(client, tenant, now + timedelta(days=2), retention=1)
        assert (await client.get("/v1.0/auditLogs/signIns", headers=headers)).json()["value"] == []
        assert (await client.post(path + "/cleanup", json={"max_rows": 1000})).status_code == 200
        assert (await client.get(link, headers=headers)).status_code == 200
        async with database.sessions.begin() as session:
            await session.execute(
                update(EvidenceCursor).values(expires_at=now - timedelta(seconds=1))
            )
        for _ in range(4):
            assert (
                await client.post(path + "/cleanup", json={"max_rows": 1000})
            ).status_code == 200
        replay = await client.post(path + "/import", json=dataset(values))
        assert replay.status_code == 200 and replay.json()["unchanged"] == 3
        await drain_api(client, tenant)
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceObservation)
                    .where(
                        EvidenceObservation.tenant_id == tenant,
                        EvidenceObservation.family == "signIn",
                    )
                )
                == 0
            )


async def test_reset_generation_and_retry_tenant_scope(database, settings):
    from mockazure.evidence.projection import process_projection
    from mockazure.jobs.repository import claim_job

    async with graph_client(database, settings) as (client, tenant, app, user):
        now = datetime.now(UTC)
        await configure(client, tenant, now)
        path = f"/_mock/tenants/{tenant}/evidence"
        value = imported(now, family="signIn")
        assert (await client.post(path + "/import", json=dataset([value]))).status_code == 200
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now + timedelta(seconds=2),
                lease_seconds=30,
                tenant_id=tenant,
                kind="evidence.project",
            )
            directory = await session.get(DirectoryState, tenant)
            prior = (directory.generation, directory.revision)
        state = (await client.get(path + "/state")).json()
        generation = state["generation"]
        assert (
            await client.post(path + "/reset", json={"expected_generation": str(uuid4())})
        ).status_code == 409
        reset = await client.post(path + "/reset", json={"expected_generation": generation})
        assert reset.status_code == 200 and reset.json()["generation"] != generation
        assert (
            await process_projection(database, claim, now + timedelta(seconds=3))
        ).state == "stale"
        async with database.sessions() as session:
            directory = await session.get(DirectoryState, tenant)
            assert prior == (directory.generation, directory.revision)
            from mockazure.identity.models import Application, User

            assert (await session.get(Application, app)).enabled
            assert (await session.get(User, user)).enabled
        await drain_api(client, tenant)
        assert (await client.get(path + "/export?family=signIn")).json()["records"] == []
        # Replaying a retained source does not resurrect retired evidence.
        assert (await client.post(path + "/import", json=dataset([value]))).json()["unchanged"] == 1
        new = imported(now, family="signIn")
        assert (await client.post(path + "/import", json=dataset([new]))).json()["inserted"] == 1
        async with database.sessions.begin() as session:
            job = await session.scalar(
                select(Job).where(
                    Job.tenant_id == tenant,
                    Job.state == "pending",
                    Job.payload["generation"].as_string() == reset.json()["generation"],
                )
            )
            job.state, job.attempts, job.claim_epoch = "failed", 3, 7
            job_id = job.id
        response = await client.post(path + f"/jobs/{job_id}/retry")
        assert response.status_code == 200 and response.json()["attempts"] == 0
        async with database.sessions() as session:
            assert (await session.get(Job, job_id)).claim_epoch == 8
        assert (await client.post(path + f"/jobs/{job_id}/retry")).status_code == 409
        other = (
            await client.post(
                "/_mock/tenants",
                json={"slug": "other", "display_name": "Other", "domain": "other.test"},
            )
        ).json()["id"]
        assert (
            await client.post(f"/_mock/tenants/{other}/evidence/jobs/{job_id}/retry")
        ).status_code == 404
        export = (await client.post(f"/_mock/tenants/{tenant}/exports")).json()["id"]
        assert (await client.post(path + f"/jobs/{export}/retry")).status_code == 404
        await drain_api(client, tenant)
        assert [
            r["payload"]["id"]
            for r in (await client.get(path + "/export?family=signIn")).json()["records"]
        ] == [new["payload"]["id"]]


async def test_cli_live_api_parity(database, settings, tmp_path):
    port = free_port()
    files = initialize_instance(
        tmp_path / "live",
        database_url=settings.database_url.get_secret_value(),
        advertised_host="localhost",
        port=port,
    )
    local = load_settings(files.config_file)
    async with identity_client(database, local) as (_, tenant, *_):
        env = {k: v for k, v in os.environ.items() if not k.startswith("MOCKAZURE_")}
        with (tmp_path / "server.log").open("w") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "mockazure", "serve", "--config", str(files.config_file)],
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                async with httpx.AsyncClient(
                    verify=ssl.create_default_context(cafile=str(files.ca_cert_file)),
                    trust_env=False,
                ) as probe:
                    for _ in range(100):
                        assert process.poll() is None
                        try:
                            if (
                                await probe.get(f"https://localhost:{port}/_mock/ready")
                            ).status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.1)
                    else:
                        raise AssertionError("Local API did not become ready")

                async def cli(command, *args, success=True, group="evidence"):
                    result = await asyncio.to_thread(
                        subprocess.run,
                        [
                            sys.executable,
                            "-m",
                            "mockazure",
                            *group.split(),
                            command,
                            "--tenant-id",
                            tenant,
                            "--config",
                            str(files.config_file),
                            *map(str, args),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=90,
                        env=env,
                    )
                    assert (result.returncode == 0) == success, (
                        "Evidence CLI result differs from expected"
                    )
                    return json.loads(result.stdout) if success else None

                from tests.integration.test_graph_licensing import P2, sku_input

                inventory = tmp_path / "inventory.json"
                inventory.write_text(json.dumps(sku_input()))
                await cli("put", "--sku-id", P2, "--file", inventory, group="licensing sku")
                assert await cli("list", group="licensing sku")
                identity = await cli(
                    "create",
                    "--display-name",
                    "CLI managed",
                    "--kind",
                    "system",
                    "--resource-id",
                    "/subscriptions/00000000-0000-0000-0000-000000000001"
                    "/resourceGroups/lab/providers/Microsoft.Web/sites/cli",
                    group="managed-identity",
                )
                private = tmp_path / "host.header"
                host = await cli(
                    "create",
                    "--name",
                    "CLI host",
                    "--system-identity-id",
                    identity["id"],
                    "--output",
                    private,
                    group="managed-identity host",
                )
                assert private.read_text().strip() not in json.dumps(host)
                rotated = tmp_path / "rotated.header"
                result = await cli(
                    "rotate",
                    "--host-id",
                    host["host_id"],
                    "--output",
                    rotated,
                    group="managed-identity host",
                )
                assert rotated.read_text().strip() not in json.dumps(result)
                assert private.read_text() != rotated.read_text()
                now = datetime.now(UTC)
                settings_file = tmp_path / "settings.json"
                settings_file.write_text(
                    json.dumps(
                        {"delay_seconds": 0, "clock_mode": "fixed", "fixed_time": now.isoformat()}
                    )
                )
                await cli("settings", "--input", settings_file)
                assert (await cli("settings"))["clock_mode"] == "fixed"
                await cli("clock", "--seconds", 1)
                evidence_file = tmp_path / "evidence.json"
                evidence_file.write_text(json.dumps(dataset([imported(now)])))
                assert (await cli("import", "--input", evidence_file))["inserted"] == 1
                jobs = (await cli("jobs"))["value"]
                target = next(j for j in jobs if j["source_kind"] == "historical")
                async with database.sessions.begin() as session:
                    job = await session.get(Job, UUID(target["id"]))
                    job.state, job.attempts = "failed", 3
                await cli("retry", "--job-id", target["id"])
                await cli("drain")
                output = tmp_path / "export.json"
                await cli("export", "--output", output)
                assert len(json.loads(output.read_text())["records"]) >= 1
                await cli("cleanup", "--max-rows", 10)
                state = await cli("state")
                await cli("reset", "--expected-generation", state["generation"])
                assert (await cli("state"))["generation"] != state["generation"]
                await cli("retry", "--job-id", uuid4(), success=False)
            finally:
                process.terminate()
                process.wait(timeout=15)


async def test_native_risk_export_preserves_signin_link(database, settings):
    from tests.integration.test_graph_evidence_reads import publish
    from tests.integration.test_graph_risk_actions import observe, risk_client

    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        signin = record("signIn", user, now - timedelta(seconds=3))
        await publish(database, tenant, "signIn", signin, now - timedelta(seconds=3))
        result = await observe(
            database, tenant, user, now - timedelta(seconds=2), sign_in_id=UUID(signin["id"])
        )
        await drain_api(client, tenant)
        response = await client.get(f"/_mock/tenants/{tenant}/evidence/export")
        assert response.status_code == 200
        records = response.json()["records"]
        detection = next(r for r in records if r["payload"]["id"] == str(result.detection_id))
        assert detection["sign_in_id"] == signin["id"]


async def test_reset_preserves_licensing_delta_and_other_tenant(database, settings):
    from tests.integration.test_graph_delta import finish
    from tests.integration.test_graph_mutations import WRITE
    from tests.integration.test_graph_risk_actions import risk_client

    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        write = await headers_for(database, settings, tenant, app, None, WRITE)
        before = (
            await client.get(
                f"/v1.0/users/{user}?$select=id,displayName,assignedLicenses", headers=write
            )
        ).json()
        _, delta = await finish(client, write, await client.get("/v1.0/users/delta", headers=write))
        other = (
            await client.post(
                "/_mock/tenants",
                json={"slug": "preserved", "display_name": "Preserved", "domain": "preserved.test"},
            )
        ).json()["id"]
        await configure(client, other, now)
        item = imported(now, family="signIn")
        base = f"/_mock/tenants/{other}/evidence"
        assert (await client.post(base + "/import", json=dataset([item]))).status_code == 200
        await drain_api(client, other)
        snapshot = (await client.get(base + "/export")).json()
        path = f"/_mock/tenants/{tenant}/evidence"
        old = (await client.get(path + "/state")).json()
        assert (
            await client.post(path + "/reset", json={"expected_generation": old["generation"]})
        ).status_code == 200
        assert (
            await client.get(
                f"/v1.0/users/{user}?$select=id,displayName,assignedLicenses", headers=write
            )
        ).json() == before
        assert before["assignedLicenses"]
        assert (await client.get(delta, headers=write)).json()["value"] == []
        assert (await client.get(base + "/export")).json() == snapshot
        assert (
            await client.patch(
                f"/v1.0/users/{user}", headers=write, json={"displayName": "After reset"}
            )
        ).status_code == 204
        # Native audit occurred on wall time, so advance fixed evidence time to include it.
        await configure(client, tenant, datetime.now(UTC) + timedelta(seconds=1))
        await drain_api(client, tenant)
        audits = (await client.get("/v1.0/auditLogs/directoryAudits", headers=headers)).json()[
            "value"
        ]
        assert len(audits) == 1
        assert audits[0]["targetResources"][0]["displayName"] == "After reset"


async def test_reset_between_legacy_scan_and_capture(database, settings, monkeypatch):
    from sqlalchemy import delete

    from mockazure.directory.models import EvidenceOutbox
    from mockazure.evidence import directory_facts
    from mockazure.evidence.cleanup import reset_evidence

    async with graph_client(database, settings) as (client, tenant, app, user):
        # Model a pre-M3B outbox record without an evidence source.
        async with database.sessions.begin() as session:
            row = await session.scalar(
                select(EvidenceOutbox)
                .where(EvidenceOutbox.tenant_id == tenant)
                .order_by(EvidenceOutbox.revision)
            )
            assert row is not None
            await session.execute(delete(Job).where(Job.tenant_id == tenant))
            await session.execute(delete(EvidenceSource).where(EvidenceSource.tenant_id == tenant))
            state = await lock_state(session, tenant)
            old_generation = state.generation
        original = directory_facts.capture_outbox
        crossed = False

        async def reset_before_capture(session, row, **kwargs):
            nonlocal crossed
            if not crossed:
                async with database.sessions.begin() as other:
                    await reset_evidence(other, tenant, old_generation)
                crossed = True
            return await original(session, row, **kwargs)

        monkeypatch.setattr(directory_facts, "capture_outbox", reset_before_capture)
        async with database.sessions.begin() as session:
            await directory_facts.scan_directory_outbox(session, 100, tenant_id=tenant)
        assert crossed
        async with database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(EvidenceSource)) == 0
