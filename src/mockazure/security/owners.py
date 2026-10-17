"""Bounded owner rows are captured in the same PostgreSQL snapshot as their objects."""

from sqlalchemy import String, and_, case, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from mockazure.identity.models import Application, ServicePrincipal, User
from mockazure.security.models import SecurityOwner
from mockazure.security.repository import document


def owner_payloads(source, actor):
    user = User.__table__.alias("owner_user")
    principal = ServicePrincipal.__table__.alias("owner_principal")
    app = Application.__table__.alias("owner_application")
    edge = SecurityOwner.__table__.alias("owner_edge")
    user_data = {"id": cast(edge.c.owner_id, String), "@odata.type": "#microsoft.graph.user"}
    if actor.permissions & {
        "Directory.Read.All",
        "Directory.ReadWrite.All",
        "User.Read.All",
        "User.ReadBasic.All",
    }:
        user_data.update(displayName=user.c.display_name, userPrincipalName=user.c.upn)
    value = case(
        (user.c.id.is_not(None), document(**user_data)),
        else_=document(
            id=cast(edge.c.owner_id, String),
            **{"@odata.type": "#microsoft.graph.servicePrincipal"},
            displayName=func.coalesce(
                principal.c.profile["displayName"].as_string(), app.c.display_name
            ),
        ),
    )
    rows = (
        select(value.label("value"))
        .select_from(edge)
        .outerjoin(
            user,
            and_(
                user.c.tenant_id == edge.c.tenant_id,
                user.c.id == edge.c.owner_id,
                user.c.deleted_at.is_(None),
            ),
        )
        .outerjoin(
            principal,
            and_(
                principal.c.tenant_id == edge.c.tenant_id,
                principal.c.id == edge.c.owner_id,
                principal.c.deleted_at.is_(None),
            ),
        )
        .outerjoin(app, app.c.client_id == principal.c.client_id)
        .where(
            edge.c.tenant_id == actor.tenant_id,
            edge.c.object_id == source.c.id,
            or_(user.c.id.is_not(None), principal.c.id.is_not(None)),
        )
        .order_by(edge.c.owner_id)
        .limit(101)
        .correlate(source)
        .subquery()
    )
    return func.coalesce(select(func.jsonb_agg(rows.c.value)).scalar_subquery(), literal([], JSONB))
