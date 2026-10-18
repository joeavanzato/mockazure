"""Typed authentication facts captured at the transaction that established the outcome."""

import asyncio
from datetime import timedelta
from ipaddress import ip_address
from typing import Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import exists, select

from mockazure.evidence.clock import evidence_now
from mockazure.evidence.models import EvidenceSource
from mockazure.evidence.repository import append_source, lock_state
from mockazure.evidence.schemas import (
    EvidenceSettings,
    Projection,
    ProjectionBatch,
    SourceInput,
    SourceRef,
    StrictModel,
)
from mockazure.identity.models import Application, ServicePrincipal, User

ERRORS = {
    0: None,
    50126: "Invalid username or password",
    50057: "User account is disabled",
    50034: "User account no longer exists",
    65004: "User declined consent",
    50074: "User declined required authentication",
    50076: "Conditional Access requires multi-factor authentication",
    53003: "Access blocked by Conditional Access",
    7000215: "Invalid client secret",
    7000112: "Application is disabled",
    700027: "Client assertion validation failed",
    7000222: "Client secret has expired",
}


class ActorSnapshot(StrictModel):
    id: UUID | None = None
    display_name: str | None = Field(default=None, max_length=200)
    upn: str | None = Field(default=None, max_length=320)

    @field_validator("*")
    @classmethod
    def valid_text(cls, value):
        if isinstance(value, str):
            if "\0" in value:
                raise ValueError("Invalid text")
            value.encode("utf-8")
        return value


class ClientSnapshot(StrictModel):
    display_name: str | None = Field(default=None, max_length=200)
    service_principal_id: UUID | None = None


class AuthenticationFact(StrictModel):
    attempt_id: UUID
    tenant_id: UUID
    client_id: UUID | None
    subject_id: UUID | None
    flow: Literal[
        "authorization_code",
        "device_code",
        "device_approval",
        "refresh_token",
        "client_credentials",
        "managed_identity",
        "legacy_unknown",
    ]
    event_type: Literal[
        "interactiveUser", "nonInteractiveUser", "servicePrincipal", "managedIdentity"
    ]
    occurred_at: AwareDatetime
    resource: Literal["graph", "exchange", "arm", "activity"] | None
    correlation_id: UUID
    outcome: Literal["success", "failure"]
    error_code: int = Field(strict=True)
    actor_snapshot: ActorSnapshot
    client_snapshot: ClientSnapshot
    ip_address: str | None = Field(default=None, max_length=45)
    mfa: Literal["completed", "denied", "not_performed"] = "not_performed"
    conditional_access_status: Literal["notApplied", "success", "failure"] = "notApplied"
    conditional_access_results: tuple[dict, ...] = ()
    client_app_type: Literal["browser", "mobileAppsAndDesktopClients"] | None = None
    requires_mfa: bool = False
    legacy_incomplete: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if self.error_code not in ERRORS or (self.error_code == 0) != (self.outcome == "success"):
            raise ValueError("Unsupported authentication outcome")
        if self.actor_snapshot.id != self.subject_id:
            raise ValueError("Authentication subject mismatch")
        if self.ip_address:
            ip_address(self.ip_address)
        from mockazure.providers.graph.schema_reference import validate_payload

        for result in self.conditional_access_results:
            validate_payload("v1.0", "appliedConditionalAccessPolicy", result, partial=True)
        return self


def event_type_for(flow):
    return {
        "refresh_token": "nonInteractiveUser",
        "client_credentials": "servicePrincipal",
        "managed_identity": "managedIdentity",
    }.get(flow, "interactiveUser")


async def make_fact(
    session,
    request,
    tenant_id,
    client_id,
    subject_id,
    flow,
    now,
    *,
    error_code=0,
    resource=None,
    mfa="not_performed",
    attempt_id=None,
    attempted_upn=None,
):
    user = (
        await session.scalar(select(User).where(User.tenant_id == tenant_id, User.id == subject_id))
        if subject_id
        else None
    )
    app = await session.get(Application, client_id) if client_id else None
    sp = (
        await session.scalar(
            select(ServicePrincipal).where(
                ServicePrincipal.tenant_id == tenant_id, ServicePrincipal.client_id == client_id
            )
        )
        if client_id
        else None
    )
    correlation = UUID(request.state.request_id)
    values = request.headers.getlist("client-request-id")
    if len(values) == 1 and len(values[0]) <= 36:
        try:
            correlation = UUID(values[0])
        except ValueError:
            pass
    peer = None
    try:
        if request.client:
            peer = str(ip_address(request.client.host))
    except ValueError:
        pass
    decision = getattr(request.state, "policy_decision", None)
    return AuthenticationFact(
        conditional_access_status=decision.status if decision else "notApplied",
        conditional_access_results=decision.results if decision else (),
        client_app_type=getattr(request.state, "policy_client_app_type", None),
        requires_mfa=decision.requires_mfa if decision else False,
        attempt_id=attempt_id or UUID(request.state.request_id),
        tenant_id=tenant_id,
        client_id=client_id,
        subject_id=subject_id,
        flow=flow,
        event_type=event_type_for(flow),
        occurred_at=await evidence_now(session, tenant_id, now),
        resource=resource,
        correlation_id=correlation,
        outcome="failure" if error_code else "success",
        error_code=error_code,
        actor_snapshot=ActorSnapshot(
            id=subject_id,
            display_name=user.display_name if user else None,
            upn=user.upn if user else attempted_upn,
        ),
        client_snapshot=ClientSnapshot(
            display_name=app.display_name if app else None,
            service_principal_id=sp.id if sp else None,
        ),
        ip_address=peer,
        mfa=mfa,
    )


async def record_authentication(session, fact: AuthenticationFact) -> SourceRef:
    fact = AuthenticationFact.model_validate(fact.model_dump())
    ref = SourceRef(tenant_id=fact.tenant_id, kind="authentication", id=fact.attempt_id)
    state = await lock_state(session, fact.tenant_id)
    if (
        fact.legacy_incomplete
        and state.legacy_auth_cutoff_at is not None
        and fact.occurred_at <= state.legacy_auth_cutoff_at
    ):
        return ref
    settings = EvidenceSettings.model_validate(state.settings)
    await append_source(
        session,
        SourceInput(
            ref=ref,
            occurred_at=fact.occurred_at,
            available_at=fact.occurred_at + timedelta(seconds=settings.delay_seconds),
            facts=fact.model_dump(mode="json"),
        ),
    )
    from mockazure.evidence.sources import enqueue_projection

    await enqueue_projection(session, ref)
    return ref


async def record_authentication_failure(database, fact: AuthenticationFact) -> None:
    if fact.outcome != "failure":
        raise ValueError("Failure transaction requires failed authentication")
    # Called by the exception handler after the operation's context has rolled back.
    async with asyncio.timeout(5):
        async with database.sessions.begin() as session:
            await record_authentication(session, fact)


async def annotate_failure(
    error, session, request, tenant_id, client_id, subject_id, flow, now, **details
):
    error.authentication_fact = await make_fact(
        session, request, tenant_id, client_id, subject_id, flow, now, **details
    )
    return error


async def scan_authentication_outcomes(session, limit, tenant_id=None):
    from mockazure.evidence.models import EvidenceState
    from mockazure.oauth.device_models import AuthenticationOutcome

    captured = exists(
        select(EvidenceSource.id).where(
            EvidenceSource.tenant_id == AuthenticationOutcome.tenant_id,
            EvidenceSource.kind == "authentication",
            EvidenceSource.id == AuthenticationOutcome.id,
        )
    )
    rows = list(
        await session.scalars(
            select(AuthenticationOutcome)
            .where(
                ~captured,
                True if tenant_id is None else AuthenticationOutcome.tenant_id == tenant_id,
                ~exists(
                    select(1).where(
                        EvidenceState.tenant_id == AuthenticationOutcome.tenant_id,
                        EvidenceState.legacy_auth_cutoff_at.is_not(None),
                        AuthenticationOutcome.timestamp <= EvidenceState.legacy_auth_cutoff_at,
                    )
                ),
            )
            .order_by(AuthenticationOutcome.timestamp, AuthenticationOutcome.id)
            .limit(limit)
        )
    )
    for row in rows:
        flow = (
            row.flow
            if row.flow in {"authorization_code", "device_code", "device_approval", "refresh_token"}
            else "legacy_unknown"
        )
        code = 0 if row.result == "success" else (50074 if row.mfa == "denied" else 65004)
        if row.result not in {"success", "access_denied"}:
            flow = "legacy_unknown"
        await record_authentication(
            session,
            AuthenticationFact(
                attempt_id=row.id,
                tenant_id=row.tenant_id,
                client_id=row.client_id,
                subject_id=row.user_id,
                flow=flow,
                event_type=event_type_for(flow),
                occurred_at=row.timestamp,
                resource=None,
                correlation_id=row.correlation_id,
                outcome="success" if code == 0 else "failure",
                error_code=code,
                actor_snapshot=ActorSnapshot(id=row.user_id),
                client_snapshot=ClientSnapshot(),
                mfa=row.mfa if row.mfa in {"completed", "denied"} else "not_performed",
                ip_address=None,
                legacy_incomplete=True,
            ),
        )
    return len(rows)


def project_authentication(source):
    fact = AuthenticationFact.model_validate(source.facts)
    suppressed = fact.flow == "device_approval" and fact.outcome == "success"
    if suppressed or fact.flow == "legacy_unknown":
        return ProjectionBatch(
            source=source.ref,
            generation=source.generation,
            items=(),
            receipt_status="suppressed" if suppressed else "legacy_incomplete",
        )
    identifier = uuid5(source.ref.id, "mockazure:m3b:signIn:0")
    client = fact.client_snapshot
    actor = fact.actor_snapshot
    payload = {
        "id": str(identifier),
        "createdDateTime": source.occurred_at.isoformat(),
        "userId": str(fact.subject_id) if fact.subject_id else "",
        "userDisplayName": actor.display_name,
        "userPrincipalName": actor.upn,
        "appId": str(fact.client_id) if fact.client_id else None,
        "appDisplayName": client.display_name,
        "servicePrincipalId": str(client.service_principal_id)
        if client.service_principal_id
        else "",
        "servicePrincipalName": client.display_name if client.service_principal_id else None,
        "resourceId": "00000003-0000-0000-c000-000000000000" if fact.resource == "graph" else None,
        "resourceDisplayName": "Microsoft Graph" if fact.resource == "graph" else None,
        "ipAddress": fact.ip_address,
        "clientAppUsed": {
            "browser": "Browser",
            "mobileAppsAndDesktopClients": "Mobile Apps and Desktop clients",
        }.get(fact.client_app_type),
        "correlationId": str(fact.correlation_id),
        "isInteractive": fact.event_type == "interactiveUser",
        "status": {
            "errorCode": fact.error_code,
            "failureReason": ERRORS[fact.error_code],
            "additionalDetails": None,
        },
        "conditionalAccessStatus": fact.conditional_access_status,
        "appliedConditionalAccessPolicies": list(fact.conditional_access_results),
        "riskDetail": "none",
        "riskLevelAggregated": "none",
        "riskLevelDuringSignIn": "none",
        "riskState": "none",
        "riskEventTypes_v2": [],
        "signInEventTypes": [fact.event_type],
        "authenticationProtocol": {
            "authorization_code": "authorizationCodeWithPkce",
            "device_code": "deviceCode",
            "device_approval": "deviceCode",
            "refresh_token": "refreshTokenGrant",
            "client_credentials": "clientCredentials",
            "managed_identity": "oAuth2",
        }.get(fact.flow),
        "authenticationRequirement": "multiFactorAuthentication"
        if fact.requires_mfa or fact.mfa in {"completed", "denied"}
        else ("singleFactorAuthentication" if fact.subject_id else None),
        "authenticationDetails": [],
    }
    if fact.mfa == "completed" and not fact.legacy_incomplete:
        payload["authenticationDetails"] = [
            {
                "authenticationStepDateTime": None,
                "authenticationMethod": "MockAzure simulated MFA",
                "authenticationMethodDetail": None,
                "succeeded": True,
                "authenticationStepResultDetail": "Completed local simulation",
                "authenticationStepRequirement": "multiFactorAuthentication",
            }
        ]
    return ProjectionBatch(
        source=source.ref,
        generation=source.generation,
        receipt_status="legacy_incomplete" if fact.legacy_incomplete else "projected",
        items=(
            Projection(
                family="signIn",
                id=identifier,
                occurred_at=source.occurred_at,
                available_at=source.available_at,
                payload=payload,
                subject_id=fact.subject_id,
                source_ref=source.ref,
            ),
        ),
    )
