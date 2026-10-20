"""Bounded Graph v1.0 request shapes, backed by the pinned operation documents."""

import re
from urllib.parse import unquote, urlsplit
from uuid import UUID

from sqlalchemy import select

from mockazure.identity.models import User
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.schema_reference import validate_payload


def bad(message):
    raise GraphError(400, "BadRequest", message)


def fields(payload, allowed, required=()):
    if (
        not isinstance(payload, dict)
        or payload.keys() - set(allowed)
        or set(required) - payload.keys()
    ):
        bad("Request contains unsupported or missing properties")


async def local_user(session, tenant, identifier):
    try:
        predicate = User.id == UUID(identifier)
    except (ValueError, TypeError, AttributeError):
        if not isinstance(identifier, str) or "@" not in identifier:
            bad("A local user ID or UPN is required")
        predicate = User.upn == identifier
    user = await session.scalar(
        select(User).where(
            User.tenant_id == tenant,
            predicate,
            User.enabled.is_(True),
            User.deleted_at.is_(None),
        )
    )
    if user is None:
        raise GraphError(404, "NotFound", "Active local user was not found")
    return user


async def member_input(session, tenant, payload, kind):
    fields(payload, {"@odata.type", "roles", "user@odata.bind"}, {"user@odata.bind", "roles"})
    if (
        payload.get("@odata.type", "#microsoft.graph.aadUserConversationMember")
        != "#microsoft.graph.aadUserConversationMember"
    ):
        bad("Only aadUserConversationMember is supported")
    binding = payload["user@odata.bind"]
    if not isinstance(binding, str) or len(binding) > 2048:
        bad("Invalid user binding")
    parsed = urlsplit(binding)
    # This URI is an identifier only. It is never requested, resolved, or fetched.
    if parsed.netloc and (parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com"):
        bad("Use the canonical Graph binding URI or a relative Graph path")
    if parsed.query or parsed.fragment:
        bad("User binding must not contain a query or fragment")
    match = re.fullmatch(r"/v1\.0/users(?:\('([^']+)'\)|/([^/]+))", unquote(parsed.path))
    if not match:
        bad("Invalid user binding path")
    user = await local_user(session, tenant, match[1] or match[2])
    roles = roles_input(payload["roles"])
    guest = user.profile.get("userType") == "Guest"
    if guest:
        if "owner" in roles:
            bad("An in-tenant guest cannot become an owner")
        roles = ["guest"]
    elif "guest" in roles:
        bad("Guest role requires a guest directory identity")
    elif kind == "chat" and roles != ["owner"]:
        bad("In-tenant chat members require the owner role")
    return user, roles


def roles_input(value):
    if value not in ([], ["owner"], ["guest"]) or not isinstance(value, list):
        bad("Roles must be empty, owner, or guest")
    return list(value)


def message_input(payload, *, update=False, channel=False):
    allowed = {"body", "importance", "mentions", "attachments"}
    if channel:
        allowed |= {"subject", "summary"}
    fields(payload, allowed, () if update else ("body",))
    if not payload:
        bad("A message update must include a supported property")
    if "body" in payload and (
        not isinstance(payload["body"], dict)
        or set(payload["body"]) != {"contentType", "content"}
        or not isinstance(payload["body"].get("content"), str)
        or not payload["body"]["content"]
    ):
        bad("A message requires a nonempty text or HTML body")
    for key in ("mentions", "attachments"):
        if key in payload and (
            not isinstance(payload[key], list)
            or len(payload[key]) > 100
            or any(not isinstance(item, dict) for item in payload[key])
        ):
            bad("Message collections require at most 100 objects")
    try:
        validate_payload("v1.0", "chatMessage", payload, partial=True)
    except (ValueError, TypeError):
        bad("Request does not match the Graph v1.0 chatMessage schema")
    return payload
