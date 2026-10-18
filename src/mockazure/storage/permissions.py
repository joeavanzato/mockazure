"""Documented Storage control Actions versus content dataActions."""

from sqlalchemy import or_, select

from mockazure.identity.models import Application, ServicePrincipal, User
from mockazure.providers.arm.authorization import grant_query, permitted_roles, security_groups
from mockazure.providers.arm.models import RoleAssignment
from mockazure.providers.arm.scopes import parse_scope
from mockazure.providers.storage.errors import StorageError

PREFIX = "Microsoft.Storage/storageAccounts/"


def operation_permission(service, action):
    if service == "queue":
        operations = {
            "enqueue": "add/action",
            "peek": "read",
            "receive": "process/action",
            "update_message": "write",
            "delete_message": "process/action",
            "clear": "delete",
        }
        if action in operations:
            return PREFIX + "queueServices/queues/messages/" + operations[action], True
        suffix = {
            "create": "write",
            "metadata": "write",
            "read": "read",
            "list": "read",
            "delete": "delete",
        }.get(action)
        if suffix:
            return PREFIX + "queueServices/queues/" + suffix, False
    if service == "table":
        actions = {
            "insert_entity": "add/action",
            "replace_entity": "update/action",
            "merge_entity": "update/action",
            "delete_entity": "delete",
            "query_entities": "read",
            "read_entity": "read",
        }
        if action in actions:
            return PREFIX + "tableServices/tables/entities/" + actions[action], True
        suffix = {"list": "read", "create": "write", "delete": "delete"}.get(action)
        if suffix:
            return PREFIX + "tableServices/tables/" + suffix, False
    if service not in {"blob", "file"}:
        raise StorageError(
            501, "MockAzureOperationNotImplemented", "Storage permission mapping unavailable"
        )
    if action == "delegation" and service == "blob":
        return PREFIX + "blobServices/generateUserDelegationKey/action", False
    if action in {"read_blob", "write_blob", "delete_blob", "list_blobs"} and service == "blob":
        return PREFIX + "blobServices/containers/blobs/" + {
            "read_blob": "read",
            "list_blobs": "read",
            "write_blob": "write",
            "delete_blob": "delete",
        }[action], True
    if action in {"read_file", "write_file", "delete_file", "list_files"} and service == "file":
        return PREFIX + "fileServices/fileshares/files/" + {
            "read_file": "read",
            "list_files": "read",
            "write_file": "write",
            "delete_file": "delete",
        }[action], True
    suffix = {
        "create": "write",
        "metadata": "write",
        "read": "read",
        "get_metadata": "read",
        "list": "read",
        "delete": "delete",
        "get_acl": "read",
        "set_acl": "setAcl/action",
    }.get(action)
    if suffix is None:
        raise StorageError(
            501, "MockAzureOperationNotImplemented", "Storage operation is not implemented"
        )
    return PREFIX + (
        "blobServices/containers/" if service == "blob" else "fileServices/shares/"
    ) + suffix, False


def scopes(account_id, service, resource):
    parsed = parse_scope(account_id)
    values = [*parsed.parent_keys, parsed.key]
    if resource:
        # Account management and native RBAC can assign container/share scopes in future.
        values.append(
            parsed.key
            + "/"
            + (
                {
                    "blob": "blobservices/default/containers/",
                    "file": "fileservices/default/fileshares/",
                    "table": "tableservices/default/tables/",
                    "queue": "queueservices/default/queues/",
                }[service]
            )
            + resource.split("/")[0]
        )
    return values


async def require_oauth(session, actor, account, service, action, resource, request):
    permission, data = operation_permission(service, action)
    needed = [(permission, data)]
    if (
        service == "table"
        and action in {"replace_entity", "merge_entity"}
        and not request.headers.get("If-Match")
    ):
        needed.append((PREFIX + "tableServices/tables/entities/add/action", True))
    if service == "file":
        if not data or request.headers.get("x-ms-file-request-intent") != "backup":
            raise StorageError(
                403,
                "AuthorizationPermissionMismatch",
                "Files requires a supported data operation and backup intent",
            )
        suffix = (
            "readFileBackupSemantics/action"
            if action in {"read_file", "list_files"}
            else "writeFileBackupSemantics/action"
        )
        needed.append((PREFIX + "fileServices/" + suffix, True))
    if service == "table" and action in {"insert_entity", "replace_entity", "merge_entity"}:
        full_write = PREFIX + "tableServices/tables/entities/write"
        grants = await grant_query(
            session, actor, full_write, audience="https://storage.azure.com/", data=True
        )
        if await session.scalar(
            grants.where(
                RoleAssignment.scope_key.in_(scopes(account.scope_key, service, resource))
            ).limit(1)
        ):
            return frozenset({full_write})
    if service == "queue" and action in {"enqueue", "receive", "delete_message"}:
        alternatives = {
            "enqueue": ["write"],
            "receive": ["delete", "read"],
            "delete_message": ["delete"],
        }[action]
        granted = []
        for suffix in alternatives:
            value = PREFIX + "queueServices/queues/messages/" + suffix
            grants = await grant_query(
                session, actor, value, audience="https://storage.azure.com/", data=True
            )
            if await session.scalar(
                grants.where(
                    RoleAssignment.scope_key.in_(scopes(account.scope_key, service, resource))
                ).limit(1)
            ):
                granted.append(value)
        if len(granted) == len(alternatives):
            return frozenset(granted)
    for value, is_data in needed:
        grants = await grant_query(
            session, actor, value, audience="https://storage.azure.com/", data=is_data
        )
        if not await session.scalar(
            grants.where(
                RoleAssignment.scope_key.in_(scopes(account.scope_key, service, resource))
            ).limit(1)
        ):
            raise StorageError(
                403,
                "AuthorizationPermissionMismatch",
                "Current identity lacks a required scoped Storage permission",
            )
    return frozenset(p for p, _ in needed)


async def require_delegation_issuer(session, key, service, action, resource):
    # A delegation signature is not an OAuth access token. Resolve the persisted
    # issuing identity directly and intersect its current RBAC grants with SAS.
    app = await session.get(Application, key.client_id, populate_existing=True)
    principal = await session.scalar(
        select(ServicePrincipal).where(
            ServicePrincipal.tenant_id == key.tenant_id,
            ServicePrincipal.client_id == key.client_id,
            ServicePrincipal.enabled.is_(True),
            ServicePrincipal.deleted_at.is_(None),
        )
    )
    if (
        app is None
        or not app.enabled
        or app.deleted_at is not None
        or app.tenant_id != key.tenant_id
        or principal is None
    ):
        raise StorageError(
            403, "AuthorizationPermissionMismatch", "Delegation issuer is unavailable"
        )
    identity = principal.id
    direct = RoleAssignment.client_id == key.client_id
    if key.user_id is not None:
        user = await session.get(User, key.user_id, populate_existing=True)
        if (
            user is None
            or user.tenant_id != key.tenant_id
            or not user.enabled
            or user.deleted_at is not None
            or user.password_change_required
        ):
            raise StorageError(
                403, "AuthorizationPermissionMismatch", "Delegation user is unavailable"
            )
        identity = user.id
        direct = RoleAssignment.user_id == user.id
    if identity != key.object_id:
        raise StorageError(403, "AuthorizationPermissionMismatch", "Delegation issuer has changed")
    permission, data = operation_permission(service, action)
    allowed = await session.scalar(
        select(RoleAssignment.id)
        .where(
            RoleAssignment.tenant_id == key.tenant_id,
            RoleAssignment.role_id.in_(permitted_roles(permission, data=data)),
            RoleAssignment.scope_key.in_(scopes(key.account_id, service, resource)),
            or_(direct, RoleAssignment.group_id.in_(security_groups(key.tenant_id, identity))),
        )
        .limit(1)
    )
    if not allowed:
        raise StorageError(
            403, "AuthorizationPermissionMismatch", "Delegation issuer lacks current Storage access"
        )
