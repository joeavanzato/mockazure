from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request, Response
from pydantic import Field, ValidationError, field_validator

from mockazure.evidence.risk import apply_risk_action
from mockazure.evidence.schemas import StrictModel
from mockazure.providers.graph.body import invalid, read_json
from mockazure.providers.graph.probes import principal


class RiskActionInput(StrictModel):
    userIds: tuple[UUID, ...] = Field(min_length=1, max_length=100)

    @field_validator("userIds")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate user ID")
        return value


def register_risk_actions(router):
    def register(action):
        async def operation(request: Request):
            try:
                value = RiskActionInput.model_validate(await read_json(request))
            except ValidationError:
                invalid("Invalid risk action body")
            async with request.app.state.database.sessions.begin() as session:
                actor = await principal(request, session)
                await apply_risk_action(
                    session,
                    actor,
                    action,
                    value.userIds,
                    datetime.now(UTC),
                    UUID(request.state.request_id),
                )
            return Response(status_code=204)

        router.add_api_route(
            "/identityProtection/riskyUsers/" + action, operation, methods=["POST"]
        )

    for action in ("confirmCompromised", "dismiss"):
        register(action)
