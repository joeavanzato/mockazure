"""Managed identity hosting is a credential-bound view of shared identity and grants."""

import hashlib
import secrets
from uuid import UUID, uuid4

from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.endpoints import resolve_resource
from mockazure.evidence.authentication import (
    ActorSnapshot,
    AuthenticationFact,
    ClientSnapshot,
    record_authentication,
)
from mockazure.evidence.clock import evidence_now
from mockazure.identity.keys import TOKEN_SECONDS, SigningKeyStore, read_wrapping_key
from mockazure.identity.models import Application, Grant, ServicePrincipal
from mockazure.identity.tokens import issue_access_token
from mockazure.identity.types import PrincipalContext
from mockazure.licensing.service import lock_directory
from mockazure.managed_identity.models import HostIdentity, ManagedHost, ManagedIdentity
from mockazure.managed_identity.schemas import ManagedIdentityView, ManagedTokenResponse


class ManagedIdentityError(Exception):
    def __init__(self, status, error, message, authentication_fact=None):
        self.status, self.error, self.message = status, error, message
        self.authentication_fact = authentication_fact
        super().__init__(message)


def bad_selector():
    return ManagedIdentityError(
        400, "invalid_request", "Requested identity is not attached to this host"
    )


def hash_header(header):
    return hashlib.sha256(header.encode("utf-8")).hexdigest()


async def owned_identity(session, tenant_id, identity_id, kind=None):
    row = await session.scalar(
        select(ManagedIdentity).where(
            ManagedIdentity.tenant_id == tenant_id, ManagedIdentity.id == identity_id
        )
    )
    if row is None or (kind and row.kind != kind):
        raise AdminError(404, "IdentityNotFound", "Managed identity not found")
    return row


async def identity_view(session, row):
    app = await session.get(Application, row.client_id)
    sp = await session.get(ServicePrincipal, row.id)
    return ManagedIdentityView(
        id=row.id,
        client_id=row.client_id,
        principal_id=row.id,
        display_name=app.display_name,
        kind=row.kind,
        resource_id=row.resource_id,
        enabled=app.enabled and sp.enabled and app.deleted_at is None and sp.deleted_at is None,
    )


async def create_managed_identity(session, tenant_id, value):
    if await session.get(Tenant, tenant_id) is None:
        raise AdminError(404, "TenantNotFound", "Tenant not found")
    await lock_directory(session, tenant_id)
    if await session.scalar(
        select(ManagedIdentity.id).where(
            ManagedIdentity.tenant_id == tenant_id,
            ManagedIdentity.resource_key == value.resource_id.lower(),
        )
    ):
        raise AdminError(409, "IdentityConflict", "Resource already has a managed identity")
    app = Application(
        tenant_id=tenant_id,
        client_mode="managed",
        display_name=value.display_name,
        required_permissions=[],
        redirect_uris=[],
        enabled=True,
    )
    session.add(app)
    await session.flush()
    sp = ServicePrincipal(tenant_id=tenant_id, client_id=app.client_id, enabled=True)
    session.add(sp)
    await session.flush()
    row = ManagedIdentity(
        id=sp.id,
        tenant_id=tenant_id,
        client_id=app.client_id,
        kind=value.kind,
        resource_id=value.resource_id,
        resource_key=value.resource_id.lower(),
    )
    session.add(row)
    await session.flush()
    return await identity_view(session, row)


async def set_identity_enabled(session, tenant_id, identity_id, enabled):
    await lock_directory(session, tenant_id)
    row = await owned_identity(session, tenant_id, identity_id)
    app = await session.get(Application, row.client_id)
    sp = await session.get(ServicePrincipal, row.id)
    if enabled and (app.deleted_at is not None or sp.deleted_at is not None):
        raise AdminError(409, "IdentityDeleted", "Deleted managed identity cannot be enabled")
    app.enabled = sp.enabled = enabled
    await session.flush()
    return await identity_view(session, row)


async def validate_attachments(session, tenant_id, system_id, user_ids, host_id=None):
    if system_id:
        await owned_identity(session, tenant_id, system_id, "system")
        bound = await session.scalar(
            select(ManagedHost.id).where(ManagedHost.system_identity_id == system_id)
        )
        if bound is not None and bound != host_id:
            raise AdminError(
                409, "IdentityInUse", "System identity is already attached to another host"
            )
    for identifier in user_ids:
        await owned_identity(session, tenant_id, identifier, "user")


async def create_host(session, tenant_id, value):
    if await session.get(Tenant, tenant_id) is None:
        raise AdminError(404, "TenantNotFound", "Tenant not found")
    await lock_directory(session, tenant_id)
    await validate_attachments(
        session, tenant_id, value.system_identity_id, value.user_identity_ids
    )
    if await session.scalar(
        select(ManagedHost.id).where(
            ManagedHost.tenant_id == tenant_id, ManagedHost.name == value.name
        )
    ):
        raise AdminError(409, "HostConflict", "Host name is already in use")
    header = secrets.token_urlsafe(48)
    row = ManagedHost(
        id=uuid4(),
        tenant_id=tenant_id,
        name=value.name,
        enabled=True,
        system_identity_id=value.system_identity_id,
        header_hash=hash_header(header),
    )
    session.add(row)
    await session.flush()
    for identifier in value.user_identity_ids:
        session.add(HostIdentity(tenant_id=tenant_id, host_id=row.id, identity_id=identifier))
    await session.flush()
    return {"host_id": str(row.id), "header": header}


async def owned_host(session, tenant_id, host_id):
    row = await session.scalar(
        select(ManagedHost)
        .where(ManagedHost.id == host_id, ManagedHost.tenant_id == tenant_id)
        .with_for_update()
    )
    if row is None:
        raise AdminError(404, "HostNotFound", "Managed identity host not found")
    return row


async def update_host(session, tenant_id, host_id, value):
    await lock_directory(session, tenant_id)
    row = await owned_host(session, tenant_id, host_id)
    users = list(
        await session.scalars(
            select(HostIdentity.identity_id).where(
                HostIdentity.tenant_id == tenant_id, HostIdentity.host_id == host_id
            )
        )
    )
    new_system = (
        value.system_identity_id
        if "system_identity_id" in value.model_fields_set
        else row.system_identity_id
    )
    new_users = value.user_identity_ids if "user_identity_ids" in value.model_fields_set else users
    if new_users is None or ("enabled" in value.model_fields_set and value.enabled is None):
        raise AdminError(422, "InvalidHost", "Host properties cannot be null")
    await validate_attachments(session, tenant_id, new_system, new_users, host_id)
    if row.system_identity_id and row.system_identity_id != new_system:
        await set_identity_enabled(session, tenant_id, row.system_identity_id, False)
    row.system_identity_id = new_system
    if value.enabled is not None:
        row.enabled = value.enabled
    await session.execute(
        delete(HostIdentity).where(
            HostIdentity.tenant_id == tenant_id, HostIdentity.host_id == host_id
        )
    )
    for identifier in new_users:
        session.add(HostIdentity(tenant_id=tenant_id, host_id=host_id, identity_id=identifier))
    await session.flush()
    return {
        "host_id": str(row.id),
        "enabled": row.enabled,
        "system_identity_id": row.system_identity_id,
        "user_identity_ids": new_users,
    }


async def rotate_host_header(session, tenant_id, host_id):
    row = await owned_host(session, tenant_id, host_id)
    header = secrets.token_urlsafe(48)
    row.header_hash = hash_header(header)
    await session.flush()
    return {"host_id": str(row.id), "header": header}


async def acquire_managed_token(session, request, settings, now):
    if request.api_version != "2019-08-01":
        raise ManagedIdentityError(
            400, "invalid_request", "Unsupported managed identity API version"
        )
    host = await session.scalar(
        select(ManagedHost)
        .where(ManagedHost.header_hash == hash_header(request.header.get_secret_value()))
        .with_for_update(read=True)
    )
    if host is None:
        raise ManagedIdentityError(403, "access_denied", "Host credential is invalid")
    if not host.enabled:
        raise ManagedIdentityError(403, "access_denied", "Host identity binding is disabled")
    try:
        resource = resolve_resource(request.resource, settings.endpoints)
    except ValueError:
        raise ManagedIdentityError(
            400, "invalid_resource", "Resource audience is not configured"
        ) from None
    if request.selector is None:
        identifier = host.system_identity_id
        if identifier is None:
            raise bad_selector()
        row = await session.scalar(
            select(ManagedIdentity)
            .where(ManagedIdentity.tenant_id == host.tenant_id, ManagedIdentity.id == identifier)
            .with_for_update(read=True)
        )
    else:
        condition = ManagedIdentity.resource_key == request.selector_value.lower()
        if request.selector != "mi_res_id":
            try:
                identifier = UUID(request.selector_value)
            except (ValueError, TypeError):
                raise bad_selector() from None
            condition = (
                ManagedIdentity.client_id == identifier
                if request.selector == "client_id"
                else ManagedIdentity.id == identifier
            )
        row = await session.scalar(
            select(ManagedIdentity)
            .join(
                HostIdentity,
                (HostIdentity.identity_id == ManagedIdentity.id)
                & (HostIdentity.tenant_id == ManagedIdentity.tenant_id),
            )
            .where(
                HostIdentity.host_id == host.id,
                HostIdentity.tenant_id == host.tenant_id,
                ManagedIdentity.kind == "user",
                condition,
            )
            .with_for_update(read=True, of=ManagedIdentity)
        )
    if row is None:
        raise bad_selector()
    app = await session.scalar(
        select(Application).where(Application.client_id == row.client_id).with_for_update(read=True)
    )
    sp = await session.scalar(
        select(ServicePrincipal)
        .where(ServicePrincipal.id == row.id, ServicePrincipal.tenant_id == row.tenant_id)
        .with_for_update(read=True)
    )
    if app is None or sp is None or app.client_mode != "managed":
        raise bad_selector()
    failure = (
        not app.enabled or not sp.enabled or app.deleted_at is not None or sp.deleted_at is not None
    )
    fact = AuthenticationFact(
        attempt_id=request.attempt_id,
        tenant_id=row.tenant_id,
        client_id=row.client_id,
        subject_id=None,
        flow="managed_identity",
        event_type="managedIdentity",
        occurred_at=await evidence_now(session, row.tenant_id, now),
        resource=resource.service,
        correlation_id=request.attempt_id,
        outcome="failure" if failure else "success",
        error_code=7000112 if failure else 0,
        actor_snapshot=ActorSnapshot(),
        client_snapshot=ClientSnapshot(display_name=app.display_name, service_principal_id=row.id),
        ip_address=request.ip_address,
    )
    if failure:
        raise ManagedIdentityError(403, "access_denied", "Managed identity is disabled", fact)
    grants = list(
        await session.scalars(
            select(Grant).where(
                Grant.tenant_id == row.tenant_id,
                Grant.client_id == row.client_id,
                Grant.kind == "application",
                Grant.resource == resource.service,
                Grant.user_id.is_(None),
            )
        )
    )
    context = PrincipalContext(
        row.tenant_id,
        row.client_id,
        row.id,
        "application",
        resource.audience,
        frozenset(p for grant in grants for p in grant.permissions),
    )
    try:
        keys = SigningKeyStore(session, read_wrapping_key(settings.data_dir), settings.endpoints)
        token = await issue_access_token(context, now, keys, managed_resource_id=row.resource_id)
    except ValueError:
        raise ManagedIdentityError(
            503, "temporarily_unavailable", "Identity signing is unavailable"
        ) from None
    await record_authentication(session, fact)
    return ManagedTokenResponse(
        access_token=token,
        client_id=row.client_id,
        resource=request.resource,
        expires_on=str(int(now.timestamp()) + TOKEN_SECONDS),
    )
