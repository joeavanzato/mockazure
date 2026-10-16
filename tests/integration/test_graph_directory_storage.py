"""Directory persistence boundaries: real PostgreSQL, no substitute repository."""

import asyncio
import json
from uuid import uuid4

import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError

from mockazure.db.engine import close_database, create_database
from mockazure.db.migrations import configuration, migrate
from mockazure.db.models import Tenant
from mockazure.identity.credentials import hash_secret, verify_secret
from mockazure.identity.models import Application, User
from mockazure.identity.repository import IdentityRepository
from mockazure.identity.types import UserCreate

PASSWORD = "local-only-directory-password"


async def tenant(session, name):
    row = Tenant(slug=name, display_name=name, domain=f"{name}.test", licences=[])
    session.add(row)
    await session.flush()
    return row


async def user(session, owner, name="analyst"):
    return await IdentityRepository(session).create_user(
        owner.id,
        UserCreate(upn=f"{name}@{owner.domain}", display_name=name, password=SecretStr(PASSWORD)),
    )


async def test_upgrade_preserves_identity_and_backfills_public_history(settings):
    tenant_id, user_id, client_id = uuid4(), uuid4(), uuid4()
    verifier = hash_secret(PASSWORD)
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        config = configuration()
        config.attributes["connection"] = connection
        command.upgrade(config, "008")
        connection.execute(
            text(
                "INSERT INTO tenants(id,slug,display_name,domain,licences,revision) "
                "VALUES (:id,'upgrade','Upgrade','upgrade.test','[]',1)"
            ),
            {"id": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO identity_users(id,tenant_id,upn,display_name,verifier,enabled) "
                "VALUES (:id,:tenant,'old@upgrade.test','Old',:verifier,true)"
            ),
            {"id": user_id, "tenant": tenant_id, "verifier": verifier},
        )
        connection.execute(
            text(
                "INSERT INTO identity_applications(client_id,tenant_id,display_name,client_mode,"
                "redirect_uris,required_permissions,enabled) "
                "VALUES (:id,:tenant,'Existing client','confidential','[]','[]',true)"
            ),
            {"id": client_id, "tenant": tenant_id},
        )
    engine.dispose()
    migrate(settings.database_url.get_secret_value())
    database = create_database(settings)
    try:
        async with database.sessions() as session:
            columns = set(
                (
                    await session.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name='identity_users'"
                        )
                    )
                ).scalars()
            )
            assert {"profile", "created_at", "deleted_at"} <= columns
            from mockazure.directory.models import ObjectVersion

            row = await session.get(User, user_id)
            assert row.id == user_id
            assert verify_secret(PASSWORD, row.verifier)
            assert (await session.get(Application, client_id)).client_id == client_id
            version = await session.scalar(
                select(ObjectVersion).where(
                    ObjectVersion.tenant_id == tenant_id, ObjectVersion.object_id == user_id
                )
            )
            assert version.properties["userPrincipalName"] == "old@upgrade.test"
            assert version.properties["accountEnabled"] is True
            assert version.valid_from == 1
            assert "verifier" not in json.dumps(version.properties)
            assert PASSWORD not in json.dumps(version.properties)
    finally:
        await close_database(database)


async def test_cross_tenant_membership_and_owners_are_rejected(database):
    from mockazure.directory.models import DirectoryRelation, Group

    async with database.sessions.begin() as session:
        first, second = await tenant(session, "first"), await tenant(session, "second")
        foreign_user = await user(session, second)
        local_user = await user(session, first)
        group = Group(tenant_id=first.id, display_name="Local", mail_nickname="local")
        session.add(group)
        await session.flush()
        ids = first.id, group.id, foreign_user.id, local_user.id
    for relation in ("member", "owner"):
        async with database.sessions() as session:
            session.add(
                DirectoryRelation(
                    tenant_id=ids[0], group_id=ids[1], object_id=ids[2], relation=relation
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
        async with database.sessions.begin() as session:
            session.add(
                DirectoryRelation(
                    tenant_id=ids[0], group_id=ids[1], object_id=ids[3], relation=relation
                )
            )
    async with database.sessions() as session:
        assert set((await session.scalars(select(DirectoryRelation.object_id))).all()) == {ids[3]}


async def test_commit_order_cannot_publish_later_revision_first(database):
    from mockazure.directory.history import reserve_revision
    from mockazure.directory.models import DirectoryState

    async with database.sessions.begin() as session:
        owner = await tenant(session, "ordered")
        owner_id = owner.id
    started = asyncio.Event()

    async def later():
        async with database.sessions.begin() as session:
            started.set()
            return await reserve_revision(session, owner_id)

    async with database.sessions() as first:
        first_revision = await reserve_revision(first, owner_id)
        task = asyncio.create_task(later())
        try:
            await started.wait()
            done, _ = await asyncio.wait({task}, timeout=0.15)
            assert not done
            async with database.sessions() as observer:
                state = await observer.get(DirectoryState, owner_id)
                assert state is None or state.revision < first_revision
            await first.commit()
            later_revision = await asyncio.wait_for(task, 5)
            assert later_revision == first_revision + 1
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_admin_writes_are_atomic_public_history_and_outbox(database):
    from mockazure.directory.models import EvidenceOutbox, ObjectVersion

    async with database.sessions.begin() as session:
        owner = await tenant(session, "atomic")
        owner_id = owner.id
    async with database.sessions() as session:
        owner = await session.get(Tenant, owner_id)
        await user(session, owner, "rollback")
        await session.rollback()
    async with database.sessions() as session:
        for model in (User, ObjectVersion, EvidenceOutbox):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    async with database.sessions.begin() as session:
        owner = await session.get(Tenant, owner_id)
        row = await user(session, owner)
        user_id = row.id
    async with database.sessions.begin() as session:
        await IdentityRepository(session).set_enabled(owner_id, user_id, False)
    async with database.sessions() as session:
        versions = (
            await session.scalars(select(ObjectVersion).order_by(ObjectVersion.valid_from))
        ).all()
        outbox = (await session.scalars(select(EvidenceOutbox))).all()
        assert len(versions) == len(outbox) == 2
        assert versions[0].properties["accountEnabled"] is True
        assert versions[1].properties["accountEnabled"] is False
        assert versions[0].valid_to == versions[1].valid_from
        assert versions[1].valid_to is None
        assert set(versions[1].changed_fields) == {"accountEnabled"}
        payload = json.dumps([v.properties for v in versions] + [e.facts for e in outbox])
        assert "verifier" not in payload
        assert PASSWORD not in payload
        assert "scrypt$" not in payload


async def test_history_rejects_secret_fields(database):
    from mockazure.directory.history import record_change, reserve_revision
    from mockazure.directory.types import DirectoryChange, DirectoryObject

    async with database.sessions() as session:
        owner = await tenant(session, "secrets")
        row = await user(session, owner)
        revision = await reserve_revision(session, owner.id)
        change = DirectoryChange(revision, "user", row.id, frozenset({"password"}), False)
        after = DirectoryObject("user", row.id, owner.id, {"password": PASSWORD})
        with pytest.raises(ValueError, match="public"):
            await record_change(session, change, None, after, {"operation": "update"})
        await session.rollback()


async def test_legacy_long_upn_survives_directory_projection(database):
    from mockazure.directory.models import ObjectVersion

    async with database.sessions.begin() as session:
        owner = await tenant(session, "legacy")
        row = await user(session, owner, "a" * 80)
        user_id = row.id
    async with database.sessions() as session:
        version = await session.scalar(
            select(ObjectVersion).where(ObjectVersion.object_id == user_id)
        )
        assert version.properties["userPrincipalName"] == "a" * 80 + "@legacy.test"
        assert version.properties["mailNickname"] == "a" * 64
