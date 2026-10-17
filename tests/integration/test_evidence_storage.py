import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from evidence_support import NOW, audit, batch, source, tenant, visible_record_ids
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.exc import IntegrityError

from mockazure.db.engine import close_database, create_database
from mockazure.db.migrations import configuration, migrate


async def test_upgrade_m3a_preserves_state_and_pending_sources(settings):
    """Missing or destructive upgrade would lose old directory cursors/facts/jobs."""
    owner, generation, client, outbox, job, outcome = [uuid4() for _ in range(6)]
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        config = configuration()
        config.attributes["connection"] = connection
        command.upgrade(config, "010")
        connection.execute(
            text(
                "INSERT INTO tenants(id,slug,display_name,domain,licences,revision) "
                "VALUES (:id,'old','Old','old.test','[]',1)"
            ),
            {"id": owner},
        )
        # Snapshot every populated legacy row, including opaque cursor binding.
        connection.execute(
            text("INSERT INTO directory_state(tenant_id,revision,generation) VALUES (:id,4,:gen)"),
            {"id": owner, "gen": generation},
        )
        connection.execute(
            text(
                "INSERT INTO jobs(id,tenant_id,kind,state,attempts,payload) "
                "VALUES (:id,:tenant,'tenant.export','pending',0,'{}')"
            ),
            {"id": job, "tenant": owner},
        )
        connection.execute(
            text(
                "INSERT INTO directory_evidence_outbox"
                "(id,tenant_id,revision,object_id,kind,facts) "
                "VALUES (:id,:tenant,4,:object,'user',:facts)"
            ),
            {
                "id": outbox,
                "tenant": owner,
                "object": client,
                "facts": '{"operation":"update","changed_fields":["displayName"]}',
            },
        )
        connection.execute(
            text(
                "INSERT INTO identity_authentication_outcomes"
                "(id,tenant_id,client_id,flow,timestamp,correlation_id,result,mfa) "
                "VALUES (:id,:tenant,:client,'client_credentials',"
                ":now,:correlation,'success','none')"
            ),
            {"id": outcome, "tenant": owner, "client": client, "now": NOW, "correlation": uuid4()},
        )
        connection.execute(
            text(
                "INSERT INTO directory_continuations"
                "(token_hash,tenant_id,state,expires_at) VALUES (:hash,:tenant,:state,:expires)"
            ),
            {
                "hash": "a" * 64,
                "tenant": owner,
                "state": '{"route":"users","revision":4,"binding":"unchanged"}',
                "expires": NOW + timedelta(hours=1),
            },
        )
        before = {
            table: connection.execute(text(f"SELECT row_to_json(t)::text FROM {table} t"))
            .scalars()
            .all()
            for table in (
                "tenants",
                "directory_state",
                "jobs",
                "directory_evidence_outbox",
                "identity_authentication_outcomes",
                "directory_continuations",
            )
        }
    engine.dispose()
    migrate(settings.database_url.get_secret_value())
    database = create_database(settings)
    try:
        async with database.sessions() as session:
            for table, rows in before.items():
                # New internal job columns may default, old values remain identical.
                import json

                after = (
                    (await session.execute(text(f"SELECT row_to_json(t)::text FROM {table} t")))
                    .scalars()
                    .all()
                )
                for old, new in zip(rows, after, strict=True):
                    assert json.loads(old).items() <= json.loads(new).items()
            assert await session.scalar(text("SELECT count(*) FROM evidence_sources")) == 0
            assert await session.scalar(text("SELECT count(*) FROM evidence_versions")) == 0
            assert await session.scalar(text("SELECT claim_epoch FROM jobs")) == 0
    finally:
        await close_database(database)


async def test_publication_commit_order_and_rollback(database):
    from mockazure.evidence.repository import append_source, publish_batch

    async with database.sessions.begin() as session:
        owner = await tenant(session)
        first_ref = await append_source(session, source(owner))
        second_ref = await append_source(session, source(owner))
    started = asyncio.Event()

    async def second():
        async with database.sessions.begin() as session:
            started.set()
            return await publish_batch(session, await batch(session, second_ref))

    async with database.sessions() as session:
        first_result = await publish_batch(session, await batch(session, first_ref))
        task = asyncio.create_task(second())
        try:
            await started.wait()
            done, _ = await asyncio.wait({task}, timeout=0.15)
            assert not done
            async with database.sessions() as reader:
                assert await visible_record_ids(reader, owner) == []
            await session.commit()
            second_result = await asyncio.wait_for(task, 5)
            assert second_result.revision == first_result.revision + 1
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    async with database.sessions() as session:
        ref = await append_source(session, source(owner))
        await publish_batch(session, await batch(session, ref))
        await session.rollback()
    async with database.sessions() as session:
        assert len(await visible_record_ids(session, owner)) == 2


async def test_tenant_constraints_and_replay_receipt(database):
    from mockazure.evidence.models import EvidenceObservation, EvidenceVersion, ProjectionReceipt
    from mockazure.evidence.repository import append_source, publish_batch

    async with database.sessions.begin() as session:
        owner, foreign = await tenant(session), await tenant(session, "foreign")
        ref = await append_source(session, source(owner))
        projection = await batch(session, ref)
        first = await publish_batch(session, projection)
    async with database.sessions.begin() as session:
        second = await publish_batch(session, projection)
        assert second.already_applied
        assert second.record_ids == first.record_ids
        assert second.revision == first.revision
        await session.execute(delete(EvidenceVersion).where(EvidenceVersion.tenant_id == owner))
    async with database.sessions.begin() as session:
        replay = await publish_batch(session, projection)
        assert replay.already_applied
        assert await visible_record_ids(session, owner) == []
        assert await session.scalar(select(func.count()).select_from(ProjectionReceipt)) == 1
    async with database.sessions() as session:
        session.add(
            EvidenceObservation(
                tenant_id=foreign,
                family="directoryAudit",
                record_id=uuid4(),
                source_kind=ref.kind,
                source_id=ref.id,
                payload=audit(),
                occurred_at=NOW,
                available_at=NOW,
                subject_id=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
    async with database.sessions.begin() as session:
        invalid = projection.model_copy(
            update={
                "items": (
                    projection.items[0].model_copy(
                        update={
                            "source_ref": projection.source.model_copy(
                                update={"tenant_id": foreign}
                            )
                        }
                    ),
                )
            }
        )
        with pytest.raises(ValueError):
            await publish_batch(session, invalid)


async def test_clock_is_separate(database):
    from mockazure.evidence.clock import evidence_now, update_settings
    from mockazure.evidence.schemas import EvidenceSettings

    async with database.sessions.begin() as session:
        owner = await tenant(session)
        assert await evidence_now(session, owner, NOW) == NOW
        settings = EvidenceSettings(clock_mode="fixed", fixed_time=NOW)
        await update_settings(session, owner, settings)
        assert await evidence_now(session, owner, NOW + timedelta(hours=2)) == NOW
        await update_settings(
            session, owner, settings.model_copy(update={"fixed_time": NOW + timedelta(days=2)})
        )
        with pytest.raises(ValueError, match="rewind"):
            await update_settings(session, owner, settings)
        # Wall time is supplied by the caller, never modified or substituted globally.
        assert await evidence_now(session, owner, NOW) == NOW + timedelta(days=2)


async def test_source_conflict_generation_and_public_schema(database):
    from mockazure.evidence.models import EvidenceState
    from mockazure.evidence.repository import append_source, load_source, publish_batch

    async with database.sessions.begin() as session:
        owner = await tenant(session)
        value = source(owner)
        ref = await append_source(session, value)
        assert await append_source(session, value) == ref
        with pytest.raises(ValueError, match="conflict"):
            await append_source(session, source(owner, source_id=ref.id))
        envelope = await load_source(session, ref)
        assert envelope.source_revision == 1
        projection = await batch(session, ref)
        state = await session.get(EvidenceState, owner)
        state.generation = uuid4()
        await session.flush()
        with pytest.raises(ValueError, match="generation"):
            await publish_batch(session, projection)
        bad = audit()
        bad["credential"] = "never-store-this"
        with pytest.raises(ValueError):
            await append_source(session, source(owner, payload=bad))


async def test_fixed_evidence_time_does_not_extend_real_token(database, settings):
    from datetime import UTC, datetime
    from uuid import UUID

    from identity_support import identity_client

    from mockazure.evidence.clock import update_settings
    from mockazure.evidence.schemas import EvidenceSettings
    from mockazure.identity.tokens import validate_access_token
    from mockazure.oauth.errors import OAuthError

    async with identity_client(database, settings) as (client, owner, app, secret, _, _):
        response = await client.post(
            f"/{owner}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": app,
                "client_secret": secret["secret"],
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        assert response.status_code == 200
        raw = response.json()["access_token"]
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            await update_settings(
                session,
                UUID(str(owner)),
                EvidenceSettings(clock_mode="fixed", fixed_time=now - timedelta(days=30)),
            )
        async with database.sessions() as session:
            context = await validate_access_token(
                raw, "graph", now, session, endpoints=settings.endpoints
            )
            assert context.tenant_id == UUID(str(owner))
            with pytest.raises(OAuthError):
                await validate_access_token(
                    raw, "graph", now + timedelta(hours=2), session, endpoints=settings.endpoints
                )


async def test_v1_historical_signin_is_not_validated_as_beta(database):
    """Beta adds a required servicePrincipalId; v1 imports must retain their v1 shape."""
    from mockazure.evidence.models import EvidenceObservation
    from mockazure.evidence.repository import append_source, load_source, publish_batch
    from mockazure.evidence.schemas import Projection, ProjectionBatch, SourceInput, SourceRef

    async with database.sessions.begin() as session:
        owner = await tenant(session)
        record_id = uuid4()
        payload = {"id": str(record_id), "createdDateTime": NOW.isoformat(), "userId": ""}
        ref = await append_source(
            session,
            SourceInput(
                ref=SourceRef(tenant_id=owner, kind="historical", id=uuid4()),
                occurred_at=NOW,
                available_at=NOW,
                facts={"family": "signIn", "version": "v1.0", "payload": payload},
            ),
        )
        envelope = await load_source(session, ref)
        await publish_batch(
            session,
            ProjectionBatch(
                source=ref,
                generation=envelope.generation,
                receipt_status="projected",
                items=(
                    Projection(
                        family="signIn",
                        id=record_id,
                        occurred_at=NOW,
                        available_at=NOW,
                        payload=payload,
                        subject_id=None,
                        source_ref=ref,
                    ),
                ),
            ),
        )
        observation = await session.get(EvidenceObservation, (owner, "signIn", record_id))
        assert observation.payload == payload
