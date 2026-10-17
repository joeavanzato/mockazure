"""Local maintenance uses separate lock domains and one shared deletion budget."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from mockazure.admin.mail_routes import tenant_required
from mockazure.auditlog.cleanup import cleanup_audit
from mockazure.content.repository import prune_content
from mockazure.mail.cleanup import cleanup_rules

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/maintenance")


class CleanupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_rows: int = Field(default=500, ge=1, le=1000, strict=True)


@router.post("/cleanup")
async def cleanup(tenant_id: UUID, request: Request, value: CleanupInput):
    now = datetime.now(UTC)
    database = request.app.state.database
    async with database.sessions.begin() as session:
        await tenant_required(session, tenant_id)
        audit = await cleanup_audit(session, tenant_id, now, value.max_rows)
    remaining = value.max_rows - audit["deleted"]
    # Mail mutations lock mailbox before evidence. Do not hold the audit/evidence
    # lock while acquiring mailbox locks; each domain is independently atomic.
    mail = {"deleted": 0, "expired_handles": 0, "versions": 0, "remaining": None}
    if remaining:
        async with database.sessions.begin() as session:
            await tenant_required(session, tenant_id)
            mail = await cleanup_rules(session, tenant_id, now, remaining)
    remaining -= mail["deleted"]
    content = {"deleted": 0, "remaining": None}
    if remaining:
        async with database.sessions.begin() as session:
            await tenant_required(session, tenant_id)
            content = await prune_content(session, tenant_id, now, remaining)
    return {
        "max_rows": value.max_rows,
        "deleted": audit["deleted"] + mail["deleted"] + content["deleted"],
        "audit": audit,
        "mail": mail,
        "content": content,
        "remaining": bool(
            audit["remaining"]
            or mail["remaining"] is not False
            or content["remaining"] is not False
        ),
    }
