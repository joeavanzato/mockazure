from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import DateTime, cast, delete, or_, select, update

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.identity.models import Application, ServicePrincipal, User
from mockazure.licensing.service import entitlements
from mockazure.oauth.models import LoginSession
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.security.audit import record_security_audit
from mockazure.security.permissions import actor_object_id
from mockazure.security.pim.contracts import duration, invalid, schedule_bounds, typed
from mockazure.security.pim.models import PimRequest, PimSchedule
from mockazure.security.pim.policies import ADMINS, READERS, policy_rules
from mockazure.security.pim.schedules import public_request
from mockazure.security.repository import lock_security, missing
from mockazure.security.roles import definition_name


async def expire_cancelled_requests(session, tenant, now):
    await session.execute(
        delete(PimRequest).where(
            PimRequest.tenant_id == tenant,
            PimRequest.payload["status"].as_string() == "Canceled",
            cast(PimRequest.payload["completedDateTime"].as_string(), DateTime(timezone=True))
            <= now - timedelta(days=30),
        )
    )


async def require_access(session, actor, kind, *, write=False, own=False, remove=False):
    label = "Eligibility" if kind == "eligibility" else "Assignment"
    names = {"Role" + label + "Schedule.ReadWrite.Directory", "RoleManagement.ReadWrite.Directory"}
    if remove and kind == "assignment":
        names |= {
            "RoleAssignmentSchedule.Remove.Directory",
            "RoleEligibilitySchedule.Remove.Directory",
        }
    if not write:
        names |= {
            "Role" + label + "Schedule.Read.Directory",
            "RoleManagement.Read.All",
            "RoleManagement.Read.Directory",
        }
    await permitted(
        actor,
        OperationRequirement(
            "graph",
            frozenset(names),
            frozenset(names),
            frozenset() if own else ADMINS if write else READERS,
        ),
        session,
    )
    if (await entitlements(session, actor.tenant_id, None)).tenant_tier != "P2":
        raise GraphError(
            403, "Authorization_RequestDenied", "PIM requires the local Entra P2 entitlement"
        )


async def require_mfa(session, actor, now):
    login = await session.get(LoginSession, actor.session_id) if actor.session_id else None
    if (
        actor.token_kind != "delegated"
        or login is None
        or login.tenant_id != actor.tenant_id
        or login.user_id != actor.subject_id
        or login.client_id != actor.client_id
        or login.revoked_at is not None
        or login.expires_at <= now
        or login.mfa != "completed"
    ):
        raise GraphError(
            403, "Authorization_RequestDenied", "A current MFA-authenticated session is required"
        )
    from mockazure.security.authentication_context import valid_method_outcome
    from mockazure.security.method_policies import enforcement_enabled

    if await enforcement_enabled(session, actor.tenant_id) and not await valid_method_outcome(
        session, login, now
    ):
        raise GraphError(
            403, "Authorization_RequestDenied", "A valid local MFA method outcome is required"
        )


async def submit_request(session, actor, kind, body, wall_now):
    if not isinstance(body, dict) or set(body) - {
        "action",
        "principalId",
        "roleDefinitionId",
        "directoryScopeId",
        "appScopeId",
        "justification",
        "scheduleInfo",
        "ticketInfo",
        "customData",
        "isValidationOnly",
    }:
        invalid()
    typed(
        "unifiedRole"
        + ("Eligibility" if kind == "eligibility" else "Assignment")
        + "ScheduleRequest",
        body,
    )
    action = body.get("action")
    actions = {"adminAssign", "adminRemove"}
    if kind == "assignment":
        actions |= {"selfActivate", "selfDeactivate"}
    if (
        action not in actions
        or body.get("directoryScopeId") != "/"
        or body.get("appScopeId") is not None
    ):
        invalid(
            "Only tenant-scope adminAssign/adminRemove and "
            "assignment selfActivate/selfDeactivate are supported"
        )
    if body.get("isValidationOnly"):
        invalid("Validation-only requests are not supported")
    try:
        principal_id = UUID(body["principalId"])
        role = definition_name(body["roleDefinitionId"])
    except (KeyError, ValueError, TypeError, AttributeError):
        invalid()
    own = action in {"selfActivate", "selfDeactivate"}
    removing = action in {"adminRemove", "selfDeactivate"}
    await lock_security(session, actor.tenant_id)
    await require_access(session, actor, kind, write=True, own=own, remove=removing)
    if own and (actor.token_kind != "delegated" or actor.subject_id != principal_id):
        raise GraphError(
            403, "Authorization_RequestDenied", "Self activation must target the signed-in user"
        )
    user = await session.get(User, principal_id, populate_existing=True)
    sp = (
        await session.get(ServicePrincipal, principal_id, populate_existing=True)
        if user is None
        else None
    )
    target = user or sp
    if (
        target is None
        or target.tenant_id != actor.tenant_id
        or target.deleted_at is not None
        or not target.enabled
        or user
        and user.profile.get("externalUserState") == "PendingAcceptance"
    ):
        missing()
    if sp:
        app = await session.get(Application, sp.client_id)
        if kind == "eligibility" or own or not app or not app.enabled or app.deleted_at:
            invalid("Only administrative active schedules support service-principal targets")
    for field in ("justification", "customData"):
        if field in body and (not isinstance(body[field], str) or len(body[field]) > 1024):
            invalid("PIM text field exceeds its bound")
    ticket = body.get("ticketInfo") or {}
    if not isinstance(ticket, dict) or set(ticket) - {"ticketNumber", "ticketSystem"}:
        invalid()
    if any(not isinstance(v, str) or len(v) > 256 for v in ticket.values()):
        invalid()
    rules = await policy_rules(session, actor.tenant_id, role)
    suffix = "EndUser_Assignment" if own else "Admin_" + kind.capitalize()
    if not removing:
        enabled = rules["Enablement_" + suffix]["enabledRules"]
        if "Justification" in enabled and not body.get("justification", "").strip():
            invalid("Role policy requires justification")
        if "Ticketing" in enabled and not all(
            ticket.get(k) for k in ("ticketNumber", "ticketSystem")
        ):
            invalid("Role policy requires ticket information")
        if own and rules["Approval_EndUser_Assignment"]["setting"]["isApprovalRequired"]:
            raise GraphError(501, "NotSupported", "Approval-required activation is not implemented")
        if "MultiFactorAuthentication" in enabled:
            await require_mfa(session, actor, wall_now)
        expiration = rules["Expiration_" + suffix]
        start, end = schedule_bounds(
            body.get("scheduleInfo"),
            wall_now,
            maximum=duration(expiration["maximumDuration"]),
            expiration_required=expiration["isExpirationRequired"],
        )
    query = (
        select(PimSchedule)
        .where(
            PimSchedule.tenant_id == actor.tenant_id,
            PimSchedule.principal_id == principal_id,
            PimSchedule.role == role,
            PimSchedule.kind == kind,
            PimSchedule.revoked_at.is_(None),
            or_(PimSchedule.ends_at.is_(None), PimSchedule.ends_at > wall_now),
        )
        .with_for_update()
    )
    existing = list(await session.scalars(query))
    request_id = uuid4()
    schedule = None
    if removing:
        if own:
            existing = [r for r in existing if r.eligibility_id and r.starts_at <= wall_now]
        if not existing:
            invalid("No matching schedule to remove")
        for row in existing:
            row.revoked_at = wall_now
        if kind == "eligibility":
            await session.execute(
                update(PimSchedule)
                .where(
                    PimSchedule.tenant_id == actor.tenant_id,
                    PimSchedule.eligibility_id.in_([r.id for r in existing]),
                    PimSchedule.revoked_at.is_(None),
                )
                .values(revoked_at=wall_now)
            )
        schedule = existing[0]
    else:
        if any(
            (r.ends_at is None or r.ends_at > start) and (end is None or r.starts_at < end)
            for r in existing
        ):
            raise GraphError(409, "Request_Conflict", "An overlapping role schedule already exists")
        eligibility = None
        if own:
            eligibility = await session.scalar(
                select(PimSchedule)
                .where(
                    PimSchedule.tenant_id == actor.tenant_id,
                    PimSchedule.principal_id == principal_id,
                    PimSchedule.role == role,
                    PimSchedule.kind == "eligibility",
                    PimSchedule.revoked_at.is_(None),
                    PimSchedule.starts_at <= wall_now,
                    or_(PimSchedule.ends_at.is_(None), PimSchedule.ends_at >= end),
                )
                .order_by(PimSchedule.id)
                .limit(1)
            )
            if eligibility is None or start > wall_now:
                raise GraphError(
                    403, "Authorization_RequestDenied", "Current eligibility must cover activation"
                )
        identifier = uuid4()
        payload = {
            "id": str(identifier),
            "principalId": str(principal_id),
            "roleDefinitionId": body["roleDefinitionId"],
            "directoryScopeId": "/",
            "appScopeId": None,
            "createdDateTime": wall_now.isoformat(),
            "modifiedDateTime": wall_now.isoformat(),
            "createdUsing": str(request_id),
            "memberType": "Direct",
            "status": "Provisioned",
            "scheduleInfo": {
                "startDateTime": start.isoformat(),
                "expiration": {"type": "afterDateTime", "endDateTime": end.isoformat()}
                if end
                else {"type": "noExpiration"},
            },
        }
        if kind == "assignment":
            payload["assignmentType"] = "Activated" if own else "Assigned"
        schedule = PimSchedule(
            tenant_id=actor.tenant_id,
            id=identifier,
            principal_id=principal_id,
            kind=kind,
            role=role,
            starts_at=start,
            ends_at=end,
            eligibility_id=eligibility.id if eligibility else None,
            payload=payload,
        )
        session.add(schedule)
        await session.flush()
    actor_id = await actor_object_id(session, actor)
    payload = {
        **body,
        "id": str(request_id),
        "status": "Granted" if not removing and start > wall_now else "Provisioned",
        "createdDateTime": wall_now.isoformat(),
        "completedDateTime": None if not removing and start > wall_now else wall_now.isoformat(),
        "createdBy": {"user" if actor.subject_id else "application": {"id": str(actor_id)}},
        "targetScheduleId": str(schedule.id),
        "approvalId": None,
    }
    request = PimRequest(
        tenant_id=actor.tenant_id,
        id=request_id,
        principal_id=principal_id,
        kind=kind,
        schedule_id=schedule.id,
        payload=payload,
    )
    session.add(request)
    await session.flush()
    await record_security_audit(
        session,
        actor,
        ("Remove" if removing else "Add")
        + (" eligible role schedule" if kind == "eligibility" else " active role schedule"),
        {
            "id": str(request_id),
            "type": "unifiedRole" + kind.capitalize() + "ScheduleRequest",
            "displayName": role,
        },
        {},
        payload,
        wall_now,
    )
    return public_request(request, schedule, wall_now)


async def cancel_request(session, actor, kind, request_id, wall_now):
    await lock_security(session, actor.tenant_id)
    await expire_cancelled_requests(session, actor.tenant_id, wall_now)
    row = await session.get(PimRequest, (actor.tenant_id, request_id))
    if row is None or row.kind != kind:
        missing()
    own = actor.token_kind == "delegated" and actor.subject_id == row.principal_id
    await require_access(session, actor, kind, write=True, own=own)
    schedule = await session.get(PimSchedule, (actor.tenant_id, row.schedule_id))
    if public_request(row, schedule, wall_now)["status"] != "Granted" or schedule.revoked_at:
        invalid("Only a Granted, not-yet-provisioned request can be canceled")
    before = dict(row.payload)
    row.payload = dict(row.payload) | {
        "status": "Canceled",
        "completedDateTime": wall_now.isoformat(),
    }
    schedule.revoked_at = wall_now
    await record_security_audit(
        session,
        actor,
        "Cancel role assignment request",
        {
            "id": str(row.id),
            "type": "unifiedRole" + kind.capitalize() + "ScheduleRequest",
            "displayName": schedule.role,
        },
        before,
        row.payload,
        wall_now,
    )
