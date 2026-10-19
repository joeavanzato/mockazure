"""Bounded XML policies and encrypted, account-bound delegation keys."""

from datetime import UTC, timedelta
from uuid import uuid4
from xml.etree.ElementTree import ParseError, fromstring

from sqlalchemy import func, select

from mockazure.providers.arm.authorization import current_principal
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.accounts import cipher, new_key
from mockazure.storage.containers import record
from mockazure.storage.models import StorageDelegationKey
from mockazure.storage.sas import timestamp


def parse_xml(raw, root_name):
    if len(raw) > 32768 or b"<!" in raw:
        raise StorageError(400, "InvalidXmlDocument", "Unsupported or oversized XML document")
    try:
        root = fromstring(raw)
        if root.tag != root_name or root.attrib:
            raise ValueError()
        return root
    except (ParseError, ValueError):
        raise StorageError(400, "InvalidXmlDocument", "Invalid XML document") from None


def parse_policies(raw, service):
    root = parse_xml(raw, "SignedIdentifiers")
    rows = []
    for item in root:
        if (
            item.tag != "SignedIdentifier"
            or [x.tag for x in item] != ["Id", "AccessPolicy"]
            or item.attrib
        ):
            raise StorageError(400, "InvalidXmlDocument", "Invalid signed identifier")
        identifier = item.findtext("Id", "")
        policy = item.find("AccessPolicy")
        if (
            not 1 <= len(identifier) <= 64
            or policy.attrib
            or len({x.tag for x in policy}) != len(policy)
            or set(x.tag for x in policy) - {"Start", "Expiry", "Permission"}
        ):
            raise StorageError(400, "InvalidXmlDocument", "Invalid access policy")
        values = {x.tag: x.text or "" for x in policy}
        order = "racwdl" if service == "blob" else "rcwdl"
        perms = values.get("Permission", "")
        if "".join(c for c in order if c in perms) != perms:
            raise StorageError(400, "InvalidXmlDocument", "Invalid policy permissions")
        try:
            for field in ("Start", "Expiry"):
                if values.get(field):
                    timestamp(values[field])
            if (
                values.get("Start")
                and values.get("Expiry")
                and timestamp(values["Start"]) >= timestamp(values["Expiry"])
            ):
                raise ValueError()
        except ValueError:
            raise StorageError(400, "InvalidXmlDocument", "Invalid access policy time") from None
        rows.append(
            {
                "id": identifier,
                "start": values.get("Start", ""),
                "expiry": values.get("Expiry", ""),
                "permission": perms,
            }
        )
    if len(rows) > 5 or len({r["id"] for r in rows}) != len(rows):
        raise StorageError(
            400, "InvalidXmlDocument", "Up to five distinct policy identifiers are allowed"
        )
    return rows


async def delegation_key(session, access, raw, now):
    root = parse_xml(raw, "KeyInfo")
    if [x.tag for x in root] != ["Start", "Expiry"]:
        raise StorageError(400, "InvalidXmlDocument", "Start and Expiry are required")
    try:
        start, expiry = (
            timestamp(root.findtext("Start", "")),
            timestamp(root.findtext("Expiry", "")),
        )
        if (
            start >= expiry
            or expiry <= now
            or expiry - now > timedelta(days=7)
            or start < now - timedelta(days=7)
            or expiry - start > timedelta(days=7)
        ):
            raise ValueError()
    except ValueError:
        raise StorageError(
            400, "InvalidXmlDocument", "Delegation interval must be within seven days"
        ) from None
    actor = access.principal
    if actor is None:
        raise StorageError(
            403, "AuthorizationPermissionMismatch", "OAuth is required for delegation"
        )
    oid = await current_principal(session, actor, audience="https://storage.azure.com/")
    row = await session.scalar(
        select(StorageDelegationKey).where(
            StorageDelegationKey.tenant_id == access.tenant_id,
            StorageDelegationKey.account_id == access.account_id,
            StorageDelegationKey.object_id == oid,
            StorageDelegationKey.starts_at == start,
            StorageDelegationKey.expires_at == expiry,
            StorageDelegationKey.version == "2025-11-05",
        )
    )
    if row is None:
        count = await session.scalar(
            select(func.count())
            .select_from(StorageDelegationKey)
            .where(
                StorageDelegationKey.tenant_id == access.tenant_id,
                StorageDelegationKey.expires_at > now,
            )
        )
        if count >= 1000:
            raise StorageError(409, "KeyLimitExceeded", "Local active delegation key limit reached")
        row = StorageDelegationKey(
            id=uuid4(),
            tenant_id=access.tenant_id,
            account_id=access.account_id,
            object_id=oid,
            client_id=actor.client_id,
            user_id=actor.subject_id if actor.token_kind == "delegated" else None,
            starts_at=start,
            expires_at=expiry,
            version="2025-11-05",
            secret=new_key(session),
        )
        session.add(row)
        await record(session, access, "blob", "", "GetUserDelegationKey", now)
    return {
        "SignedOid": str(oid),
        "SignedTid": str(access.tenant_id),
        "SignedStart": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "SignedExpiry": expiry.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "SignedService": "b",
        "SignedVersion": row.version,
        "Value": cipher(session).decrypt(row.secret.encode()).decode(),
    }
