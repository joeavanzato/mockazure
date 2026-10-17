"""Bounded public UAL payloads, using the pinned Graph v1.0 structural schema."""

from datetime import datetime
from uuid import UUID

from mockazure.mail.contracts import validate_mail_payload

PRIVATE = {
    "secret",
    "verifier",
    "clientassertion",
    "authorization",
    "proxyauthorization",
    "accesstoken",
    "refreshtoken",
    "clientsecret",
    "secrettext",
    "password",
    "passwordhash",
    "privatekey",
}


def public_audit_data(value):
    if isinstance(value, dict):
        named = value.get("Name", value.get("name", value.get("Key", value.get("key"))))
        if isinstance(named, str) and named.replace("_", "").replace("-", "").lower() in PRIVATE:
            raise ValueError("Credential-bearing audit name/value pairs are unsupported")
        for key, item in value.items():
            if key.replace("_", "").replace("-", "").lower() in PRIVATE:
                raise ValueError("Credential-bearing audit fields are unsupported")
            public_audit_data(item)
    elif isinstance(value, list):
        for item in value:
            public_audit_data(item)


def validate_audit_record(payload, *, tenant=None, occurred_at=None):
    validate_mail_payload("security.auditLogRecord", payload)
    public_audit_data(payload)
    UUID(payload["id"])

    for field, maximum in (("service", 128), ("operation", 256)):
        value = payload.get(field)
        if value is not None and len(value) > maximum:
            raise ValueError("Indexed audit fields require bounded strings")
    if tenant is not None:
        try:
            organization = UUID(payload.get("organizationId", ""))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Audit organization requires a tenant UUID") from None
        if organization != tenant:
            raise ValueError("Audit organization does not match its owning tenant")
    if occurred_at is not None:
        when = payload.get("createdDateTime")
        if (
            not isinstance(when, str)
            or datetime.fromisoformat(when.replace("Z", "+00:00")) != occurred_at
        ):
            raise ValueError("Audit occurrence and creation timestamps must agree")
    return payload
