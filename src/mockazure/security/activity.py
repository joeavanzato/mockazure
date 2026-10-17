"""Compact user-lifetime attempt index; independent of evidence retention."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKeyConstraint, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base
from mockazure.evidence.clock import evidence_now
from mockazure.identity.models import User


class UserSignInActivity(Base):
    __tablename__ = "security_user_signin_activity"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["identity_users.tenant_id", "identity_users.id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    record_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID]
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    interactive: Mapped[bool]
    successful: Mapped[bool]


async def record_activity(session, tenant, item):
    if item.family != "signIn" or item.subject_id is None:
        return
    payload = item.payload
    interactive = payload.get("isInteractive")
    code = (payload.get("status") or {}).get("errorCode")
    if type(interactive) is not bool or type(code) is not int:
        return
    user = await session.scalar(
        select(User.id).where(
            User.tenant_id == tenant, User.id == item.subject_id, User.deleted_at.is_(None)
        )
    )
    if user is None:
        return
    await session.execute(
        insert(UserSignInActivity)
        .values(
            tenant_id=tenant,
            record_id=item.id,
            user_id=user,
            occurred_at=item.occurred_at,
            available_at=item.available_at,
            interactive=interactive,
            successful=code == 0,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "record_id"])
    )


async def activities(session, tenant, identifiers):
    result = {
        identifier: {
            prefix + suffix: None
            for prefix in ("lastSignIn", "lastNonInteractiveSignIn", "lastSuccessfulSignIn")
            for suffix in ("DateTime", "RequestId")
        }
        for identifier in identifiers
    }
    if not identifiers:
        return result
    now = await evidence_now(session, tenant, datetime.now(UTC))
    table = UserSignInActivity
    for prefix, predicate in (
        ("lastSignIn", table.interactive.is_(True)),
        ("lastNonInteractiveSignIn", table.interactive.is_(False)),
        ("lastSuccessfulSignIn", table.successful.is_(True)),
    ):
        rows = await session.scalars(
            select(table)
            .where(
                table.tenant_id == tenant,
                table.user_id.in_(identifiers),
                table.available_at <= now,
                predicate,
            )
            .distinct(table.user_id)
            .order_by(table.user_id, table.occurred_at.desc(), table.record_id.desc())
        )
        for row in rows:
            result[row.user_id][prefix + "DateTime"] = row.occurred_at.isoformat()
            result[row.user_id][prefix + "RequestId"] = str(row.record_id)
    return result


def activity_document(tenant, identifier, now):
    from sqlalchemy import String, cast, func

    from mockazure.security.repository import document

    table = UserSignInActivity
    result = document()
    for prefix, predicate in (
        ("lastSignIn", table.interactive.is_(True)),
        ("lastNonInteractiveSignIn", table.interactive.is_(False)),
        ("lastSuccessfulSignIn", table.successful.is_(True)),
    ):
        value = (
            select(
                document(
                    **{
                        prefix + "DateTime": table.occurred_at,
                        prefix + "RequestId": cast(table.record_id, String),
                    }
                )
            )
            .where(
                table.tenant_id == tenant,
                table.user_id == identifier,
                table.available_at <= now,
                predicate,
            )
            .order_by(table.occurred_at.desc(), table.record_id.desc())
            .limit(1)
            .scalar_subquery()
        )
        result = result.op("||")(
            func.coalesce(
                value,
                document(
                    **{
                        prefix + "DateTime": None,
                        prefix + "RequestId": None,
                    }
                ),
            )
        )
    return result


async def activity_collection(request, session, actor, query, revision):
    from sqlalchemy import or_

    from mockazure.directory.models import ObjectVersion
    from mockazure.security.paging import security_collection
    from mockazure.security.registration import require_report_read
    from mockazure.security.repository import document

    await require_report_read(session, actor)
    table = ObjectVersion
    now = await evidence_now(session, actor.tenant_id, datetime.now(UTC))
    source = (
        select(
            table.object_id.label("id"),
            table.properties.op("||")(
                document(signInActivity=activity_document(actor.tenant_id, table.object_id, now))
            ).label("payload"),
        )
        .where(
            table.tenant_id == actor.tenant_id,
            table.kind == "user",
            table.valid_from <= revision,
            or_(table.valid_to.is_(None), table.valid_to > revision),
            table.removed.is_(False),
        )
        .subquery()
    )
    return await security_collection(request, session, actor, "userActivity", query, source=source)


async def add_activity(session, actor, identifier, fields, data):
    if fields and "signInActivity" in fields:
        from mockazure.security.registration import require_report_read

        await require_report_read(session, actor)
        data["signInActivity"] = (await activities(session, actor.tenant_id, [identifier]))[
            identifier
        ]
