"""Own-application Graph subscriptions with exact resource and current permission checks."""

import copy
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied, OperationRequirement, authorize
from mockazure.calendar.contracts import invalid
from mockazure.calendar.events import require_calendar_access
from mockazure.calendar.events import state_for as calendar_state
from mockazure.identity.models import ServicePrincipal
from mockazure.mail.authorization import GRAPH, require_mailbox_access
from mockazure.mail.messages import state_for as mail_state
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.send import actor_data, lock_workflow
from mockazure.notifications.models import GraphSubscription
from mockazure.notifications.receivers import resolve_receiver, validate_receiver
from mockazure.providers.graph.body import typed_body
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.schema_reference import validate_payload


def permission(family):
    app = (
        {"Mail.Read", "Mail.ReadWrite"}
        if family == "mail"
        else {"Calendars.Read", "Calendars.ReadWrite"}
        if family == "calendar"
        else {"Files.Read.All", "Files.ReadWrite.All"}
    )
    delegated = app | {"Mail.ReadBasic"} if family == "mail" else app
    return OperationRequirement("graph", frozenset(app), frozenset(delegated))


async def parse_target(session, actor, resource):
    if (
        not isinstance(resource, str)
        or len(resource) > 1024
        or any(c in resource for c in "?#\\%\0")
    ):
        invalid("Unsupported subscription resource")
    path = resource.strip("/")
    if path.startswith("me/"):
        if actor.token_kind != "delegated":
            invalid("me requires delegated authentication")
        path = "users/" + str(actor.subject_id) + path[2:]
    # Documented Outlook folder subscription spelling is also accepted.
    path = re.sub(r"mailFolders\('([^']+)'\)", r"mailFolders/\1", path, flags=re.I)
    match = re.fullmatch(
        r"users/([^/]+)/(events|messages|mailFolders/([^/]+)/messages)", path, re.I
    )
    if match:
        box = await resolve_mailbox(session, actor.tenant_id, match[1])
        family = "calendar" if match[2].lower() == "events" else "mail"
        if actor.token_kind == "delegated" and actor.subject_id != box.user_id:
            raise AuthorizationDenied("Delegated subscriptions require the signed-in mailbox")
        await authorize(actor, permission(family), session)
        if family == "calendar":
            await require_calendar_access(session, actor, box)
            state = await calendar_state(session, box)
        else:
            await require_mailbox_access(session, actor, box, "content.read")
            state = await mail_state(session, box, create=True)
        target = {
            "family": family,
            "owner": str(box.user_id),
            "generation": str(state.generation),
            "folder": None,
        }
        if match[3]:
            from mockazure.mail.messages import folder_by_id

            folder = await folder_by_id(session, box, match[3])
            target["folder"] = folder.id
        return target
    match = re.fullmatch(r"drives/([^/]+)/root", path)
    if match:
        from mockazure.workloads.drives import resolve_drive_item
        from mockazure.workloads.repository import current_record, scope_row
        from mockazure.workloads.resource_access import authorize_site

        await authorize(actor, permission("drive"), session)
        drive = await current_record(session, actor.tenant_id, "sharepoint", "drive", match[1])
        if drive is None:
            raise GraphError(404, "itemNotFound", "Drive was not found")
        await authorize_site(session, actor, drive.scope, "drive")
        root = await resolve_drive_item(session, actor.tenant_id, match[1], "root")
        state = await scope_row(session, actor.tenant_id, "sharepoint", drive.scope)
        return {
            "family": "drive",
            "drive": match[1],
            "scope": drive.scope,
            "root": root["row"].id,
            "root_public": root["row"].payload["id"],
            "generation": str(state.generation),
        }
    raise GraphError(501, "NotSupported", "Subscription resource is outside the supported profile")


async def require_current(session, actor, target):
    if actor.audience != GRAPH:
        raise AuthorizationDenied("Graph audience required")
    resource = (
        "drives/" + target["drive"] + "/root"
        if target["family"] == "drive"
        else "users/"
        + target["owner"]
        + "/"
        + (
            "events"
            if target["family"] == "calendar"
            else "mailFolders/" + target["folder"] + "/messages"
            if target.get("folder")
            else "messages"
        )
    )
    current = await parse_target(session, actor, resource)
    if current != target:
        raise AuthorizationDenied("Subscription target generation changed")


def expiration(value, now, family):
    try:
        if not isinstance(value, str):
            raise ValueError()
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
        if stamp > now + timedelta(minutes=42300 if family == "drive" else 10080):
            raise ValueError()
        return max(stamp.astimezone(UTC), now + timedelta(minutes=45))
    except ValueError:
        invalid("Subscription expiration exceeds the documented resource lifetime")


def public(row):
    return {
        **copy.deepcopy(row.payload),
        "id": str(row.id),
        "expirationDateTime": row.expires_at.isoformat(),
    }


async def owned(session, actor, identifier, now):
    try:
        key = UUID(identifier)
    except (ValueError, TypeError):
        raise GraphError(404, "ResourceNotFound", "Subscription was not found") from None
    row = await session.get(GraphSubscription, key)
    if (
        not row
        or row.tenant_id != actor.tenant_id
        or row.client_id != actor.client_id
        or (actor.token_kind == "delegated" and row.subject_id != actor.subject_id)
        or row.deleted
        or row.expires_at <= now
    ):
        raise GraphError(404, "ResourceNotFound", "Subscription was not found")
    await require_current(session, actor, row.target)
    return row


async def mutate_subscription(session, actor, subscription_id, operation, body, now):
    await lock_workflow(session, actor.tenant_id)
    if actor.audience != GRAPH:
        raise AuthorizationDenied("Graph audience required")
    body = typed_body(body, "subscription")
    if operation in {"delete", "reauthorize"}:
        if body:
            invalid("This operation has no request body")
        row = await owned(session, actor, subscription_id, now)
        if operation == "delete":
            row.deleted = True
            row.revision += 1
        else:
            row.actor = actor_data(actor)
        row.updated_at = now
        return None
    allowed = (
        {"expirationDateTime", "notificationUrl"}
        if operation == "update"
        else {
            "changeType",
            "resource",
            "notificationUrl",
            "lifecycleNotificationUrl",
            "expirationDateTime",
            "clientState",
            "includeResourceData",
            "latestSupportedTlsVersion",
            "encryptionCertificate",
            "encryptionCertificateId",
            "notificationQueryOptions",
            "notificationUrlAppId",
        }
    )
    if body.keys() - allowed:
        invalid("Subscription property is not writable")
    if body.get("includeResourceData") or any(
        k in body
        for k in {
            "encryptionCertificate",
            "encryptionCertificateId",
            "notificationQueryOptions",
            "notificationUrlAppId",
        }
    ):
        raise GraphError(
            501, "NotSupported", "Rich notifications and filtered resources are not supported"
        )
    try:
        validate_payload("v1.0", "subscription", body, partial=True)
    except ValueError:
        invalid("Body does not match Graph subscription schema")
    registry = session.info.get("notification_receivers", {})
    if operation == "create":
        if not {"changeType", "resource", "notificationUrl", "expirationDateTime"} <= body.keys():
            invalid("Required subscription property is missing")
        target = await parse_target(session, actor, body["resource"])
        changes = body["changeType"].split(",") if isinstance(body["changeType"], str) else []
        if (
            not changes
            or len(changes) != len(set(changes))
            or set(changes) - {"created", "updated", "deleted"}
        ):
            invalid("Invalid changeType")
        if target["family"] == "drive" and changes != ["updated"]:
            invalid("Drive subscriptions support only updated changes")
        if body.get("clientState") is not None and len(body["clientState"]) > 128:
            invalid("clientState exceeds128characters")
        if body.get("latestSupportedTlsVersion", "v1_2") not in {"v1_2", "v1_3"}:
            invalid("Local receivers require TLS1.2 or newer")
        expires = expiration(body["expirationDateTime"], now, target["family"])
        existing = list(
            await session.scalars(
                select(GraphSubscription).where(
                    GraphSubscription.tenant_id == actor.tenant_id,
                    GraphSubscription.client_id == actor.client_id,
                    GraphSubscription.deleted.is_(False),
                    GraphSubscription.expires_at > now,
                )
            )
        )
        if any(
            r.target == target and set(r.payload["changeType"].split(",")) == set(changes)
            for r in existing
        ):
            raise GraphError(
                409, "Conflict", "Subscription already exists for this resource and changeType"
            )
        if len(existing) >= 1000:
            raise GraphError(
                429,
                "SubscriptionQuotaExceeded",
                "Local subscription quota exceeded",
                {"Retry-After": "60"},
            )
        bindings = {}
        for url in dict.fromkeys(
            body[k] for k in ("notificationUrl", "lifecycleNotificationUrl") if body.get(k)
        ):
            binding = resolve_receiver(url, registry)
            await validate_receiver(binding)
            bindings[url] = binding["fingerprint"]
        sp = await session.scalar(
            select(ServicePrincipal).where(
                ServicePrincipal.tenant_id == actor.tenant_id,
                ServicePrincipal.client_id == actor.client_id,
            )
        )
        payload = {
            **body,
            "changeType": ",".join(sorted(changes)),
            "applicationId": str(actor.client_id),
            "creatorId": str(actor.subject_id if actor.token_kind == "delegated" else sp.id),
        }
        row = GraphSubscription(
            id=uuid4(),
            tenant_id=actor.tenant_id,
            client_id=actor.client_id,
            subject_id=actor.subject_id,
            actor=actor_data(actor),
            target=target,
            receiver_bindings=bindings,
            payload=payload,
            revision=1,
            deleted=False,
            expires_at=expires,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        if not body:
            invalid("An expiration or callback update is required")
        row = await owned(session, actor, subscription_id, now)
        if "notificationUrl" in body:
            binding = resolve_receiver(body["notificationUrl"], registry)
            await validate_receiver(binding)
            row.receiver_bindings = {
                **row.receiver_bindings,
                body["notificationUrl"]: binding["fingerprint"],
            }
            row.revision += 1
        if "expirationDateTime" in body:
            row.expires_at = expiration(body["expirationDateTime"], now, row.target["family"])
        row.payload = {**row.payload, **body}
        row.actor = actor_data(actor)
        row.updated_at = now
    await session.flush()
    return public(row)
