"""Persisted lab enrollment descriptors; never credentials or challenge outcomes."""

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.identity.models import DirectoryRole, User
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.security.models import SecurityObject
from mockazure.security.repository import missing

PASSWORD_ID = "28c10230-6103-485e-b985-444c60001490"
EMAIL_ID = "3ddfcfc8-9383-446f-83cc-3ab9be4be18f"
PHONE_IDS = {
    "mobile": "3179e48a-750b-4051-897c-87b9720928f7",
    "alternateMobile": "b6332ec1-7057-4abe-9331-3d72feddfe41",
    "office": "e37fc753-ff3b-4958-9484-eaa9425c82bc",
}
METHODS = {
    "passwordMethods": ("passwordAuthenticationMethod", "Password"),
    "phoneMethods": ("phoneAuthenticationMethod", "Phone"),
    "emailMethods": ("emailAuthenticationMethod", "Email"),
    "microsoftAuthenticatorMethods": (
        "microsoftAuthenticatorAuthenticationMethod",
        "MicrosoftAuthApp",
    ),
    "fido2Methods": ("fido2AuthenticationMethod", "Passkey"),
}
READ_ROLES = frozenset(
    {
        "Global Reader",
        "Authentication Administrator",
        "Privileged Authentication Administrator",
        "Global Administrator",
    }
)
FIELDS = {
    "phoneAuthenticationMethod": {"phoneNumber", "phoneType"},
    "emailAuthenticationMethod": {"emailAddress"},
    "microsoftAuthenticatorAuthenticationMethod": {"displayName", "deviceTag", "phoneAppVersion"},
    "fido2AuthenticationMethod": {"displayName", "model", "aaGuid"},
}


def invalid(message):
    raise GraphError(400, "Request_BadRequest", message)


async def method_user(session, tenant, identifier):
    try:
        key = User.id == UUID(str(identifier))
    except ValueError:
        key = func.lower(User.upn) == str(identifier).lower()
    user = await session.scalar(
        select(User).where(
            User.tenant_id == tenant,
            User.deleted_at.is_(None),
            key,
        )
    )
    if user is None:
        missing()
    return user


def method_query(tenant, user):
    return select(SecurityObject).where(
        SecurityObject.tenant_id == tenant,
        SecurityObject.kind == "authenticationMethod",
        SecurityObject.properties["userId"].astext == str(user.id),
    )


async def stored_methods(session, tenant, user, kind=None):
    values = []
    if user.verifier:
        values.append(
            {
                "@odata.type": "#microsoft.graph.passwordAuthenticationMethod",
                "id": PASSWORD_ID,
                "password": None,
                "createdDateTime": None,
            }
        )
    rows = await session.scalars(method_query(tenant, user).order_by(SecurityObject.id).limit(100))
    values.extend(dict(row.properties["payload"]) for row in rows)
    return [v for v in values if kind is None or v["@odata.type"] == "#microsoft.graph." + kind]


async def require_method_read(session, actor, user, collection):
    self_read = actor.token_kind == "delegated" and actor.subject_id == user.id
    global_permissions = {
        "UserAuthenticationMethod.Read.All",
        "UserAuthenticationMethod.ReadWrite.All",
    }
    self_permissions = {"UserAuthenticationMethod.Read", "UserAuthenticationMethod.ReadWrite"}
    applications = set(global_permissions)
    delegated = set(global_permissions) | (self_permissions if self_read else set())
    if collection != "methods":
        family = METHODS[collection][1]
        granular_all = {
            f"UserAuthMethod-{family}.Read.All",
            f"UserAuthMethod-{family}.ReadWrite.All",
        }
        applications |= granular_all
        delegated |= granular_all
        if self_read:
            delegated |= {f"UserAuthMethod-{family}.Read", f"UserAuthMethod-{family}.ReadWrite"}
        # The current v1.0 FIDO2 operation table explicitly limits application
        # access to Passkey.Read.All; don't infer a broad permission alternative.
        if family == "Passkey":
            applications = {"UserAuthMethod-Passkey.Read.All"}
            delegated = {"UserAuthMethod-Passkey.Read.All"}
            if self_read:
                delegated |= self_permissions | {
                    "UserAuthMethod-Passkey.Read",
                    "UserAuthMethod-Passkey.ReadWrite",
                }
    await permitted(
        actor,
        OperationRequirement(
            "graph",
            frozenset(applications),
            frozenset(delegated),
            frozenset() if self_read else READ_ROLES,
        ),
        session,
    )
    if actor.token_kind != "delegated" or self_read:
        return False
    roles = set(
        await session.scalars(
            select(DirectoryRole.role).where(
                DirectoryRole.tenant_id == actor.tenant_id,
                DirectoryRole.user_id == actor.subject_id,
            )
        )
    )
    return "Authentication Administrator" in roles and not roles & {
        "Global Administrator",
        "Privileged Authentication Administrator",
        "Global Reader",
    }


async def enroll_method(session, tenant, user, body):
    annotation = body.get("@odata.type")
    kinds = {"#microsoft.graph." + k: k for k in FIELDS}
    if not isinstance(annotation, str) or annotation not in kinds:
        invalid("Only declared non-secret enrollment descriptors can be configured")
    kind = kinds[annotation]
    if set(body) - FIELDS[kind] - {"@odata.type"}:
        invalid("Unsupported enrollment property; credentials cannot be imported")
    try:
        validate_payload("v1.0", kind, body, partial=True)
    except ValueError:
        invalid("Enrollment descriptor does not match the Graph v1.0 schema")
    payload = dict(body)
    identifier = str(uuid4())
    if kind == "phoneAuthenticationMethod":
        phone_type = body.get("phoneType")
        if phone_type not in PHONE_IDS or not re.fullmatch(
            r"\+[1-9][0-9]{0,2} [0-9]{4,14}(?:x[0-9]{1,8})?", body.get("phoneNumber") or ""
        ):
            invalid("A supported phone type and international phone number are required")
        identifier = PHONE_IDS[phone_type]
        if (
            phone_type == "alternateMobile"
            and await session.get(
                SecurityObject,
                (tenant, uuid5(user.id, "authenticationMethod:" + PHONE_IDS["mobile"])),
            )
            is None
        ):
            invalid("An alternate mobile method requires a primary mobile method")
        payload["smsSignInState"] = "notEnabled" if phone_type == "mobile" else "notSupported"
    elif kind == "emailAuthenticationMethod":
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", body.get("emailAddress") or ""):
            invalid("A recovery email address is required")
        identifier = EMAIL_ID
    else:
        if not body.get("displayName"):
            invalid("An enrollment displayName is required")
        payload["createdDateTime"] = datetime.now(UTC).isoformat()
    payload["id"] = identifier
    key = uuid5(user.id, "authenticationMethod:" + identifier)
    if await session.get(SecurityObject, (tenant, key)) is not None:
        raise GraphError(409, "Request_Conflict", "This method is already enrolled")
    count = await session.scalar(
        select(func.count()).select_from(method_query(tenant, user).subquery())
    )
    if count >= 100:
        invalid("This lab profile supports at most 100 non-password methods per user")
    validate_payload("v1.0", kind, payload, partial=True)
    session.add(
        SecurityObject(
            tenant_id=tenant,
            id=key,
            kind="authenticationMethod",
            properties={"userId": str(user.id), "payload": payload, "provenance": "lab-enrollment"},
        )
    )
    await session.flush()
    return payload


async def remove_method(session, tenant, user, identifier):
    row = await session.get(
        SecurityObject, (tenant, uuid5(user.id, "authenticationMethod:" + identifier))
    )
    if (
        row is None
        or row.kind != "authenticationMethod"
        or row.properties["userId"] != str(user.id)
    ):
        missing()
    if identifier == PHONE_IDS["mobile"]:
        alternate = await session.get(
            SecurityObject,
            (tenant, uuid5(user.id, "authenticationMethod:" + PHONE_IDS["alternateMobile"])),
        )
        if alternate is not None:
            invalid("Remove the alternate mobile method before the primary")
    await session.delete(row)
