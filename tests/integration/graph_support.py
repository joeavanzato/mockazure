"""Local signed-token and persisted-directory fixtures, without HTTP substitutes."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from mockazure.directory.history import record_change, reserve_revision
from mockazure.directory.models import Group
from mockazure.directory.repository import public_object
from mockazure.directory.types import DirectoryChange
from mockazure.identity.keys import SigningKeyStore, read_wrapping_key
from mockazure.identity.tokens import issue_access_token
from mockazure.identity.types import PrincipalContext
from tests.integration.identity_support import identity_client

GRAPH_AUDIENCE = "00000003-0000-0000-c000-000000000000"


async def headers_for(
    database, settings, tenant, app, user, permissions, kind="application", audience=GRAPH_AUDIENCE
):
    context = PrincipalContext(
        UUID(str(tenant)),
        UUID(str(app)),
        UUID(str(user)) if user else None,
        kind,
        audience,
        frozenset(permissions),
    )
    async with database.sessions() as session:
        store = SigningKeyStore(session, read_wrapping_key(settings.data_dir), settings.endpoints)
        raw = await issue_access_token(context, datetime.now(UTC), store)
    return {"Authorization": "Bearer " + raw}


@asynccontextmanager
async def graph_client(database, settings):
    async with identity_client(database, settings) as (client, tenant, app, *_):
        result = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "reader@contoso.test",
                "display_name": "Directory Reader",
                "password": "local-directory-password",
            },
        )
        assert result.status_code == 201, result.text
        yield client, UUID(tenant), UUID(app), UUID(result.json()["id"])


async def persisted_group(session, tenant_id, name="Investigators"):
    revision = await reserve_revision(session, tenant_id)
    row = Group(tenant_id=tenant_id, display_name=name, mail_nickname=name.lower())
    session.add(row)
    await session.flush()
    obj = public_object(row)
    await record_change(
        session,
        DirectoryChange(revision, "group", row.id, frozenset(obj.properties)),
        None,
        obj,
        {"operation": "create", "provenance": "test-fixture"},
    )
    return row


async def seed_relation_versions(database, tenant):
    """Complete history for older tests deliberately constructing current edge fixtures."""
    from sqlalchemy import select

    from mockazure.directory.history import current_revision
    from mockazure.directory.models import DirectoryRelation, RelationVersion

    async with database.sessions.begin() as session:
        revision = await current_revision(session, tenant)
        for edge in await session.scalars(
            select(DirectoryRelation).where(DirectoryRelation.tenant_id == tenant)
        ):
            found = await session.scalar(
                select(RelationVersion.valid_from).where(
                    RelationVersion.tenant_id == tenant,
                    RelationVersion.group_id == edge.group_id,
                    RelationVersion.object_id == edge.object_id,
                    RelationVersion.relation == edge.relation,
                    RelationVersion.valid_to.is_(None),
                )
            )
            if found is None:
                session.add(
                    RelationVersion(
                        tenant_id=tenant,
                        group_id=edge.group_id,
                        object_id=edge.object_id,
                        relation=edge.relation,
                        valid_from=revision,
                    )
                )
