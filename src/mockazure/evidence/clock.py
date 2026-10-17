"""Evidence time never replaces OAuth or continuation wall clocks."""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.evidence.models import EvidenceState
from mockazure.evidence.repository import lock_state
from mockazure.evidence.schemas import EvidenceSettings, aware


async def evidence_now(session: AsyncSession, tenant_id: UUID, wall_now: datetime) -> datetime:
    aware(wall_now)
    state = await session.get(EvidenceState, tenant_id)
    settings = EvidenceSettings.model_validate(state.settings if state else {})
    return settings.fixed_time if settings.clock_mode == "fixed" else wall_now


async def update_settings(
    session: AsyncSession,
    tenant_id: UUID,
    settings: EvidenceSettings,
) -> EvidenceSettings:
    settings = EvidenceSettings.model_validate(settings.model_dump())
    state = await lock_state(session, tenant_id)
    previous = EvidenceSettings.model_validate(state.settings)
    if previous.fixed_time and settings.fixed_time and settings.fixed_time < previous.fixed_time:
        raise ValueError("Cannot rewind a fixed evidence clock")
    state.settings = settings.model_dump(mode="json")
    await session.flush()
    return settings
