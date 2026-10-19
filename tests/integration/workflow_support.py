"""Signed workflow test tokens backed by actual current Graph consent."""

from sqlalchemy import select

from mockazure.identity.models import Grant
from tests.integration.graph_support import headers_for as signed_headers


async def headers_for(database, settings, tenant, app, user, permissions, kind="application"):
    # Unlike pure token-validation fixtures, durable workflows must retain consent.
    # Keep token permissions exact even when the application has additional grants.
    subject = user if kind == "delegated" else None
    async with database.sessions.begin() as session:
        row = await session.scalar(
            select(Grant).where(
                Grant.tenant_id == tenant,
                Grant.client_id == app,
                Grant.resource == "graph",
                Grant.kind == kind,
                Grant.user_id == subject,
            )
        )
        if row is None:
            row = Grant(
                tenant_id=tenant,
                client_id=app,
                resource="graph",
                kind=kind,
                user_id=subject,
                permissions=[],
            )
            session.add(row)
        row.permissions = sorted(set(row.permissions) | set(permissions))
    return await signed_headers(database, settings, tenant, app, user, permissions, kind)
