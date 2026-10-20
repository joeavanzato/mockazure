"""Tenant-scoped recovery, with no restoration of deleted authentication authority."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.db.models import Tenant
from mockazure.directory.history import record_object_version, reserve_revision
from mockazure.directory.models import DirectoryRelation, Group
from mockazure.directory.repository import public_object
from mockazure.directory.types import DirectoryChange
from mockazure.identity.deleted_models import DeletedObject
from mockazure.identity.models import (
    Application,
    Credential,
    DirectoryRole,
    Grant,
    ServicePrincipal,
    ServicePrincipalRole,
    User,
)
from mockazure.oauth.models import LoginSession
from mockazure.providers.graph.body import invalid
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.security.audit import record_security_audit
from mockazure.security.models import SecurityObject, SecurityOwner
from mockazure.security.permissions import APP_ADMINS, actor_object_id, forbidden
from mockazure.security.repository import lock_security, public_security_object

KINDS = frozenset({"user", "application", "servicePrincipal"})
RETENTION = timedelta(days=30)


def missing():
    raise GraphError(404, "Request_ResourceNotFound", "Deleted object is unavailable")


def conflict(message):
    raise GraphError(409, "Request_MultipleObjectsWithSameKeyValue", message)


async def capture_deletion(session, tenant, kind, row, payload, now):
    """Called before canonical deletion and relationship cleanup, in its transaction."""
    await lock_security(session, tenant)
    identifier = row.object_id if kind == "application" else row.id
    owners = list(
        await session.scalars(
            select(SecurityOwner.owner_id).where(
                SecurityOwner.tenant_id == tenant, SecurityOwner.object_id == identifier
            )
        )
    )
    snapshot = {
        "enabled": row.enabled,
        "public": dict(payload),
        "owners": [str(x) for x in owners],
        "legacy": False,
    }
    if kind == "user":
        snapshot["manager"] = str(row.manager_id) if row.manager_id else None
        snapshot["roles"] = list(
            await session.scalars(
                select(DirectoryRole.role).where(
                    DirectoryRole.tenant_id == tenant, DirectoryRole.user_id == identifier
                )
            )
        )
        snapshot["groups"] = [
            {"id": str(edge.group_id), "relation": edge.relation}
            for edge in await session.scalars(
                select(DirectoryRelation).where(
                    DirectoryRelation.tenant_id == tenant,
                    DirectoryRelation.object_id == identifier,
                )
            )
        ]
        snapshot["owned"] = [
            str(x)
            for x in await session.scalars(
                select(SecurityOwner.object_id).where(
                    SecurityOwner.tenant_id == tenant, SecurityOwner.owner_id == identifier
                )
            )
        ]
        row.sessions_valid_from = max(now, row.sessions_valid_from or now)
        row.credential_version += 1
        await session.execute(
            update(LoginSession)
            .where(
                LoginSession.tenant_id == tenant,
                LoginSession.user_id == identifier,
                LoginSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await session.execute(
            delete(Grant).where(Grant.tenant_id == tenant, Grant.user_id == identifier)
        )
        await session.execute(
            delete(DirectoryRole).where(
                DirectoryRole.tenant_id == tenant, DirectoryRole.user_id == identifier
            )
        )
        await session.execute(
            update(User)
            .where(User.tenant_id == tenant, User.manager_id == identifier)
            .values(manager_id=None)
        )
        row.manager_id = None
    else:
        application = (
            row if kind == "application" else await session.get(Application, row.client_id)
        )
        application.authority_valid_from = max(now, application.authority_valid_from or now)
        condition = (
            Credential.client_id == row.client_id
            if kind == "application"
            else Credential.owner_id == identifier
        )
        await session.execute(
            update(Credential)
            .where(Credential.tenant_id == tenant, condition, Credential.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await session.execute(
            delete(Grant).where(Grant.tenant_id == tenant, Grant.client_id == row.client_id)
        )
        await session.execute(
            delete(ServicePrincipalRole).where(
                ServicePrincipalRole.tenant_id == tenant,
                ServicePrincipalRole.client_id == row.client_id,
            )
        )
        await session.execute(
            update(LoginSession)
            .where(
                LoginSession.tenant_id == tenant,
                LoginSession.client_id == row.client_id,
                LoginSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
    # No passwords, secrets or key material are copied into recovery metadata.
    for field in ("passwordCredentials", "keyCredentials"):
        snapshot["public"].pop(field, None)
    values = dict(
        tenant_id=tenant,
        object_id=identifier,
        kind=kind,
        deleted_at=now,
        purged_at=None,
        snapshot=snapshot,
    )
    await session.execute(
        insert(DeletedObject)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["tenant_id", "object_id"],
            set_={k: v for k, v in values.items() if k not in {"tenant_id", "object_id"}},
        )
    )


def available(tenant, now):
    return (
        DeletedObject.tenant_id == tenant,
        DeletedObject.purged_at.is_(None),
        DeletedObject.deleted_at > now - RETENTION,
    )


async def deleted_row(session, tenant, object_id, now):
    row = await session.scalar(
        select(DeletedObject).where(*available(tenant, now), DeletedObject.object_id == object_id)
    )
    if row is None:
        missing()
    return row


async def authorize_deleted(session, actor, kind, *, write=False, row=None):
    if kind not in KINDS:
        invalid("Deleted-object type is outside the supported recovery profile")
    if not write:
        permissions = (
            {"User.Read.All", "User.ReadWrite.All", "Directory.Read.All", "Directory.ReadWrite.All"}
            if kind == "user"
            else {
                "Application.Read.All",
                "Application.ReadWrite.All",
                "Directory.Read.All",
                "Directory.ReadWrite.All",
            }
        )
        await permitted(
            actor,
            OperationRequirement("graph", frozenset(permissions), frozenset(permissions)),
            session,
        )
        return
    if kind == "user":
        permissions = frozenset(
            {
                "User.DeleteRestore.All",
                "User.ReadWrite.All",
                "Directory.ReadWrite.All",
            }
        )
        roles = frozenset(
            {
                "User Administrator",
                "Authentication Administrator",
                "Privileged Authentication Administrator",
                "Global Administrator",
            }
        )
        await permitted(
            actor, OperationRequirement("graph", permissions, permissions, roles), session
        )
        if row.snapshot.get("roles"):
            # Privileged restoration needs extra documented scope and higher role.
            await permitted(
                actor,
                OperationRequirement(
                    "graph",
                    frozenset({"User.ReadWrite.All"}),
                    frozenset({"Directory.AccessAsUser.All"}),
                    frozenset({"Global Administrator", "Privileged Authentication Administrator"}),
                    application_roles=frozenset({"Global Administrator"}),
                ),
                session,
            )
        return
    full = frozenset({"Application.ReadWrite.All", "Directory.ReadWrite.All"})
    await permitted(
        actor,
        OperationRequirement(
            "graph",
            full | {"Application.ReadWrite.OwnedBy"},
            full,
            frozenset(APP_ADMINS | {"Hybrid Identity Administrator"}),
        ),
        session,
    )
    if actor.token_kind == "application" and not actor.permissions & full:
        owner = await actor_object_id(session, actor)
        if str(owner) not in row.snapshot.get("owners", []):
            forbidden()


def deleted_view(row):
    return {
        **row.snapshot["public"],
        "id": str(row.object_id),
        "@odata.type": "#microsoft.graph." + row.kind,
        "deletedDateTime": row.deleted_at.isoformat().replace("+00:00", "Z"),
    }


async def deleted_item(session, actor, object_id):
    row = await deleted_row(session, actor.tenant_id, object_id, datetime.now(UTC))
    await authorize_deleted(session, actor, row.kind)
    return deleted_view(row)


async def live_dependency(session, tenant, identifier):
    for model, column in (
        (User, User.id),
        (Application, Application.object_id),
        (ServicePrincipal, ServicePrincipal.id),
    ):
        if await session.scalar(
            select(column).where(
                model.tenant_id == tenant, column == identifier, model.deleted_at.is_(None)
            )
        ):
            return True
    return bool(
        await session.scalar(
            select(SecurityObject.id).where(
                SecurityObject.tenant_id == tenant,
                SecurityObject.id == identifier,
                SecurityObject.kind == "device",
            )
        )
    )


async def restore_relationships(session, entry, row, revision):
    tenant, identifier = entry.tenant_id, entry.object_id
    # Explicit matrix: only saved ownership and ordinary static group edges return.
    # Consent, roles, licenses, credentials, sessions and device observations do not.
    edges = [(identifier, UUID(x)) for x in entry.snapshot.get("owners", [])]
    if entry.kind == "user":
        edges += [(UUID(x), identifier) for x in entry.snapshot.get("owned", [])]
        from mockazure.directory.mutations import add_relation

        changes = {}
        for edge in entry.snapshot.get("groups", []):
            group_id = UUID(edge["id"])
            group = await session.get(Group, group_id)
            if (
                not group
                or group.tenant_id != tenant
                or group.deleted_at is not None
                or group.profile.get("isAssignableToRole")
                or group.profile.get("groupTypes")
            ):
                continue
            key = (tenant, group_id, identifier, edge["relation"])
            if await session.get(DirectoryRelation, key):
                conflict("A saved group relationship already exists")
            await add_relation(session, tenant, group_id, identifier, edge["relation"], revision)
            changes.setdefault(group_id, set()).add(edge["relation"] + "s")
        for group_id, fields in changes.items():
            group = public_object(await session.get(Group, group_id))
            await record_object_version(
                session,
                DirectoryChange(revision, "group", group_id, frozenset(fields)),
                group,
                group,
            )
        manager = entry.snapshot.get("manager")
        if manager:
            from mockazure.identity.lifecycle import require_live_user

            candidate = await session.get(User, UUID(manager))
            if candidate and candidate.tenant_id == tenant and candidate.deleted_at is None:
                seen = {identifier}
                for _ in range(1000):
                    if candidate.id in seen:
                        conflict("Restoring the manager would create a cycle")
                    seen.add(candidate.id)
                    if candidate.manager_id is None:
                        break
                    candidate = await require_live_user(session, tenant, candidate.manager_id)
                else:
                    conflict("Saved manager hierarchy exceeds local depth limit")
                row.manager_id = UUID(manager)
    for obj, owner in edges:
        if not (
            await live_dependency(session, tenant, obj)
            and await live_dependency(session, tenant, owner)
        ):
            continue
        if await session.get(SecurityOwner, (tenant, obj, owner)):
            conflict("A saved ownership relationship already exists")
        session.add(SecurityOwner(tenant_id=tenant, object_id=obj, owner_id=owner))


async def restore_object(session, actor, object_id, options, wall_now):
    await lock_security(session, actor.tenant_id)
    entry = await deleted_row(session, actor.tenant_id, object_id, wall_now)
    await authorize_deleted(session, actor, entry.kind, write=True, row=entry)
    if (
        not isinstance(options, dict)
        or set(options)
        - {
            "newUserPrincipalName",
            "autoReconcileProxyConflict",
        }
        or (options and entry.kind != "user")
    ):
        invalid("Unsupported restore parameters")
    if (
        "autoReconcileProxyConflict" in options
        and type(options["autoReconcileProxyConflict"]) is not bool
    ):
        invalid("autoReconcileProxyConflict must be Boolean")
    model = {"user": User, "application": Application, "servicePrincipal": ServicePrincipal}[
        entry.kind
    ]
    key = Application.object_id if entry.kind == "application" else model.id
    row = await session.scalar(
        select(model).where(
            model.tenant_id == actor.tenant_id, key == object_id, model.deleted_at.is_not(None)
        )
    )
    if row is None:
        missing()
    if entry.kind != "user":
        from mockazure.managed_identity.models import ManagedIdentity

        if await session.scalar(
            select(ManagedIdentity.id).where(
                ManagedIdentity.tenant_id == actor.tenant_id,
                ManagedIdentity.client_id == row.client_id,
            )
        ):
            invalid("Managed identity recovery is controlled by its Azure resource lifecycle")
    if entry.kind == "user":
        name = options.get("newUserPrincipalName", row.upn)
        tenant = await session.get(Tenant, actor.tenant_id)
        if (
            not isinstance(name, str)
            or not 3 <= len(name) <= 320
            or name.count("@") != 1
            or any(c.isspace() for c in name)
            or "\x00" in name
            or name.split("@")[1].lower() != tenant.domain.lower()
        ):
            invalid("Restored UPN must use the local tenant verified domain")
        name = name.lower()
        if await session.scalar(
            select(User.id).where(
                User.tenant_id == actor.tenant_id, User.upn == name, User.deleted_at.is_(None)
            )
        ):
            conflict("The restored user name belongs to an active user")
        addresses = list(row.profile.get("proxyAddresses", []))
        if addresses:
            others = await session.scalars(
                select(User.profile["proxyAddresses"]).where(
                    User.tenant_id == actor.tenant_id,
                    User.deleted_at.is_(None),
                    User.profile.has_key("proxyAddresses"),
                )
            )
            used = {x.casefold() for group in others if isinstance(group, list) for x in group}
            conflicts = {x for x in addresses if x.casefold() in used}
            if conflicts and not options.get("autoReconcileProxyConflict", False):
                conflict("Restored proxy addresses belong to an active user")
            row.profile = {
                **row.profile,
                "proxyAddresses": [x for x in addresses if x not in conflicts],
            }
        row.upn = name
    elif entry.kind == "servicePrincipal":
        backing = await session.get(Application, row.client_id)
        if not backing or backing.deleted_at is not None:
            conflict("Restore the backing application before its service principal")
    row.deleted_at, row.enabled = None, bool(entry.snapshot.get("enabled", False))
    revision = await reserve_revision(session, actor.tenant_id)
    await session.flush()
    await restore_relationships(session, entry, row, revision)
    if entry.kind == "user":
        current = public_object(row)
        await record_object_version(
            session,
            DirectoryChange(revision, "user", row.id, frozenset(current.properties)),
            None,
            current,
        )
        result = {"@odata.type": "#microsoft.graph.user", **current.properties}
    else:
        result = await public_security_object(session, actor.tenant_id, entry.kind, object_id)
    await record_security_audit(
        session,
        actor,
        "Restore " + entry.kind,
        {
            "id": str(object_id),
            "type": entry.kind[0].upper() + entry.kind[1:],
            "displayName": result.get("displayName"),
        },
        {},
        {},
        wall_now,
    )
    await session.delete(entry)
    await session.flush()
    return result


async def purge_object(session, actor, object_id, wall_now):
    await lock_security(session, actor.tenant_id)
    entry = await deleted_row(session, actor.tenant_id, object_id, wall_now)
    await authorize_deleted(session, actor, entry.kind, write=True, row=entry)
    await record_security_audit(
        session,
        actor,
        "Hard Delete " + entry.kind,
        {
            "id": str(object_id),
            "type": entry.kind[0].upper() + entry.kind[1:],
            "displayName": entry.snapshot["public"].get("displayName"),
        },
        {},
        {},
        wall_now,
    )
    # Preserve IDs/evidence references, erase recovery metadata and block all future restore.
    entry.purged_at = wall_now
    entry.snapshot = {}
