"""Versioned secrets, safe metadata lists, and context-bound authenticated encryption."""

import json
from datetime import timedelta
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from mockazure.cloud.events import append_service_event
from mockazure.cloud.models import CloudState
from mockazure.cloud.types import ServiceEvent
from mockazure.keyvault.authorization import require_secret_access, vault_configuration
from mockazure.keyvault.contracts import bad, checked_body, name_key
from mockazure.keyvault.endpoints import vault_origin
from mockazure.keyvault.secret_models import Secret, SecretCursor, SecretVersion
from mockazure.providers.keyvault.errors import VaultError


def binding(secret, version):
    return {
        "tenant": str(secret.tenant_id),
        "vault": secret.vault_id,
        "name": secret.name_key,
        "version": version.version,
    }


def seal(session, secret, version, value):
    return (
        Fernet(session.info["keyvault_cipher"])
        .encrypt(
            json.dumps(
                binding(secret, version) | {"value": value},
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode()
        )
        .decode("ascii")
    )


def unseal(session, secret, version):
    if version.encrypted_value is None:
        raise VaultError(
            409, "SecretValueUnavailable", "This metadata-only restore has no secret value"
        )
    try:
        value = json.loads(
            Fernet(session.info["keyvault_cipher"]).decrypt(version.encrypted_value.encode())
        )
        if {k: v for k, v in value.items() if k != "value"} != binding(
            secret, version
        ) or not isinstance(value["value"], str):
            raise ValueError()
        return value["value"]
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise VaultError(
            503, "ServiceUnavailable", "Secret encryption state is unavailable"
        ) from None


def recovery_level(props):
    result = (
        "Recoverable"
        if props.get("softDeleteRetentionInDays", 90) == 90
        else "CustomizedRecoverable"
    )
    return result if props.get("enablePurgeProtection") else result + "+Purgeable"


def view(session, vault, props, secret, version, *, include_value=False, versioned=True):
    url = vault_origin(session.info["keyvault_endpoint"], vault.name) + "/secrets/" + secret.name
    if versioned:
        url += "/" + version.version
    attrs = dict(
        version.attributes,
        created=int(version.created_at.timestamp()),
        updated=int(version.updated_at.timestamp()),
        recoveryLevel=recovery_level(props),
        recoverableDays=props.get("softDeleteRetentionInDays", 90),
    )
    result = {"id": url, "attributes": attrs, "tags": version.tags, "managed": False}
    if version.content_type is not None:
        result["contentType"] = version.content_type
    if include_value:
        result["value"] = unseal(session, secret, version)
    return result


async def find_secret(session, tenant, vault_id, name, *, deleted=False):
    key = name_key(name)
    row = await session.scalar(
        select(Secret).where(
            Secret.tenant_id == tenant, Secret.vault_id == vault_id, Secret.name_key == key
        )
    )
    if row is None or (row.deleted_at is not None) != deleted:
        raise VaultError(404, "SecretNotFound", "The requested secret was not found")
    return row


async def find_version(session, secret, version):
    query = select(SecretVersion).where(
        SecretVersion.tenant_id == secret.tenant_id, SecretVersion.secret_id == secret.id
    )
    if version:
        try:
            if len(version) != 32:
                raise ValueError()
            identifier = UUID(hex=version)
        except ValueError:
            bad("Invalid secret version")
        query = query.where(SecretVersion.version == identifier.hex)
    else:
        query = query.order_by(SecretVersion.sequence.desc()).limit(1)
    row = await session.scalar(query)
    if row is None:
        raise VaultError(404, "SecretNotFound", "The requested secret version was not found")
    return row


async def record(session, actor, vault_id, name, operation, now):
    await append_service_event(
        session,
        ServiceEvent(
            uuid4(),
            actor.tenant_id,
            vault_id,
            "keyvault",
            "AuditEvent",
            operation,
            now,
            {"name": name, "outcome": "Succeeded", "authenticationType": actor.token_kind},
        ),
    )


async def mutate_secret(session, actor, vault_id, name, version, operation, body, now):
    await require_secret_access(session, actor, vault_id, name, operation)
    if operation in {"delete", "recover", "purge"}:
        from mockazure.keyvault.recovery import mutate_recovery

        return await mutate_recovery(session, actor, vault_id, name, operation, now)
    if operation not in {"set", "update"}:
        raise VaultError(501, "NotImplemented", "Secret operation is not implemented")
    checked_body(body, operation)
    key = name_key(name)
    vault, props = await vault_configuration(session, actor.tenant_id, vault_id)
    if operation == "set":
        from mockazure.keyvault.recovery import expired_name

        await expired_name(session, actor.tenant_id, vault_id, name, now)
        secret = await session.scalar(
            select(Secret).where(
                Secret.tenant_id == actor.tenant_id,
                Secret.vault_id == vault_id,
                Secret.name_key == key,
            )
        )
        if secret and secret.deleted_at:
            raise VaultError(409, "Conflict", "A deleted secret with this name is recoverable")
        if secret is None:
            secret = Secret(
                tenant_id=actor.tenant_id, vault_id=vault_id, name=name, name_key=key, revision=0
            )
            session.add(secret)
            await session.flush()
        secret.revision += 1
        row = SecretVersion(
            tenant_id=actor.tenant_id,
            id=uuid4(),
            version=uuid4().hex,
            secret_id=secret.id,
            sequence=secret.revision,
            attributes={"enabled": True} | body.get("attributes", {}),
            tags=body.get("tags", {}),
            content_type=body.get("contentType"),
            created_at=now,
            updated_at=now,
        )
        row.encrypted_value = seal(session, secret, row, body["value"])
        session.add(row)
    else:
        secret = await find_secret(session, actor.tenant_id, vault_id, name)
        row = await find_version(session, secret, version)
        row.attributes = row.attributes | body.get("attributes", {})
        if "tags" in body:
            row.tags = body["tags"]
        if "contentType" in body:
            row.content_type = body["contentType"]
        row.updated_at = now
    await session.flush()
    await record(
        session,
        actor,
        vault_id,
        secret.name,
        "SecretSet" if operation == "set" else "SecretUpdate",
        now,
    )
    # Update returns metadata; changing attributes must never implicitly grant get.
    return view(session, vault, props, secret, row, include_value=operation == "set")


async def get_secret(session, actor, vault_id, name, version, now):
    await require_secret_access(session, actor, vault_id, name, "get")
    secret = await find_secret(session, actor.tenant_id, vault_id, name)
    row = await find_version(session, secret, version)
    if not row.attributes.get("enabled", True):
        raise VaultError(403, "Forbidden", "The secret is disabled")
    # Azure explicitly permits GET outside nbf/exp; these are metadata, not a GET gate.
    vault, props = await vault_configuration(session, actor.tenant_id, vault_id)
    result = view(session, vault, props, secret, row, include_value=True)
    await record(session, actor, vault_id, secret.name, "SecretGet", now)
    return result


async def list_secrets(session, actor, vault_id, name, args, now, *, deleted=False):
    await require_secret_access(
        session,
        actor,
        vault_id,
        name,
        "list_deleted" if deleted else "versions" if name else "list",
    )
    try:
        maximum = int(args.get("maxresults", "25"))
    except ValueError:
        bad("Invalid page size")
    if not 1 <= maximum <= 25:
        bad("maxresults must be between 1 and 25")
    vault, props = await vault_configuration(session, actor.tenant_id, vault_id)
    secret = await find_secret(session, actor.tenant_id, vault_id, name) if name else None
    query = {"name": secret.name_key if secret else None, "maxresults": maximum, "deleted": deleted}
    generation = (await session.get(CloudState, actor.tenant_id)).generation
    last = None
    if args.get("$skiptoken"):
        try:
            identifier = UUID(args["$skiptoken"])
        except ValueError:
            bad("Invalid continuation")
        cursor = await session.get(SecretCursor, (actor.tenant_id, identifier))
        if (
            cursor is None
            or cursor.vault_id != vault_id
            or cursor.generation != generation
            or cursor.query != query
            or cursor.expires_at <= now
        ):
            bad("Expired or mismatched continuation")
        last = cursor.last_key
    if secret:
        sql = select(SecretVersion).where(
            SecretVersion.tenant_id == actor.tenant_id, SecretVersion.secret_id == secret.id
        )
        if last:
            sql = sql.where(SecretVersion.sequence > int(last))
        rows = list(await session.scalars(sql.order_by(SecretVersion.sequence).limit(maximum + 1)))
        values = [view(session, vault, props, secret, row) for row in rows[:maximum]]
        last_key = str(rows[maximum - 1].sequence) if len(rows) > maximum else None
    else:
        sql = select(Secret).where(
            Secret.tenant_id == actor.tenant_id,
            Secret.vault_id == vault_id,
            Secret.deleted_at.is_not(None) if deleted else Secret.deleted_at.is_(None),
        )
        if deleted:
            sql = sql.where(Secret.purge_at > now)
        if last:
            sql = sql.where(Secret.name_key > last)
        rows = list(await session.scalars(sql.order_by(Secret.name_key).limit(maximum + 1)))
        values = []
        for item in rows[:maximum]:
            row = await find_version(session, item, None)
            if deleted:
                from mockazure.keyvault.recovery import deleted_view

                values.append(deleted_view(session, vault, props, item, row, versioned=False))
            else:
                values.append(view(session, vault, props, item, row, versioned=False))
        last_key = rows[maximum - 1].name_key if len(rows) > maximum else None
    result = {"value": values}
    if last_key is not None:
        from urllib.parse import urlencode

        cursor = SecretCursor(
            tenant_id=actor.tenant_id,
            vault_id=vault_id,
            generation=generation,
            query=query,
            last_key=last_key,
            expires_at=now + timedelta(hours=1),
        )
        session.add(cursor)
        await session.flush()
        path = ("/deletedsecrets" if deleted else "/secrets") + (
            "/" + secret.name + "/versions" if secret else ""
        )
        result["nextLink"] = (
            session.info["keyvault_request_base"].rstrip("/")
            + path
            + "?"
            + urlencode(
                {"api-version": "2025-07-01", "maxresults": maximum, "$skiptoken": str(cursor.id)}
            )
        )

    await record(
        session, actor, vault_id, name or "", "SecretListVersions" if name else "SecretList", now
    )
    return result
