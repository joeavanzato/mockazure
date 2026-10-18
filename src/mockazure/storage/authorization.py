"""Current local account settings, signatures and OAuth scoped grants."""

from datetime import datetime
from email.utils import parsedate_to_datetime

from sqlalchemy import select

from mockazure.identity.tokens import validate_access_token
from mockazure.oauth.errors import OAuthError
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.models import ArmResource
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.accounts import cipher, network_allowed
from mockazure.storage.models import StorageAccount, StorageNamespace
from mockazure.storage.permissions import operation_permission, require_oauth
from mockazure.storage.sas import authorize_account_sas, authorize_service_sas
from mockazure.storage.signing import canonicalize, header_values, matches
from mockazure.storage.types import StorageAccess


async def authorize_storage(
    session, request, service: str, action: str, resource: str, now: datetime
) -> StorageAccess:
    operation_permission(service, action)
    account = request.scope["path"].split("/")[1]
    row = await session.scalar(
        select(StorageAccount)
        .where(StorageAccount.name == account)
        .execution_options(populate_existing=True)
    )
    if not row:
        raise StorageError(404, "ResourceNotFound", "Storage account was not found")
    arm = await session.get(ArmResource, (row.tenant_id, row.scope_key), populate_existing=True)
    props = arm.payload["properties"]
    if (arm.payload["kind"] == "FileStorage" and service != "file") or (
        arm.payload["kind"] in {"BlobStorage", "BlockBlobStorage"} and service != "blob"
    ):
        raise StorageError(404, "ResourceNotFound", "Service is unavailable for this account kind")
    if props.get("supportsHttpsTrafficOnly", True) and request.url.scheme != "https":
        raise StorageError(400, "AccountRequiresHttps", "This account requires HTTPS")
    if not network_allowed(props, request.client.host if request.client else None, request.headers):
        raise StorageError(
            403, "AuthorizationFailure", "Current account network policy denies this request"
        )
    pairs = [(k.decode("latin1"), v.decode("latin1")) for k, v in request.scope["headers"]]
    try:
        values = header_values(pairs)
    except ValueError:
        raise StorageError(400, "InvalidHeaderValue", "Duplicate request header") from None
    auth = values.get("authorization", "")
    scheme, _, credential = auth.partition(" ")
    actor = None
    if scheme == "Bearer":
        if "sig" in request.query_params:
            raise StorageError(
                400, "InvalidAuthenticationInfo", "Multiple authentication mechanisms"
            )
        try:
            actor = await validate_access_token(
                credential, "storage", now, session, endpoints=request.app.state.settings.endpoints
            )
            if actor.tenant_id != row.tenant_id:
                raise ArmError(403, "AuthorizationFailed", "Wrong tenant")
            permissions = await require_oauth(
                session, actor, row, service, action, resource, request
            )
        except (OAuthError, ArmError):
            raise StorageError(
                403, "AuthorizationPermissionMismatch", "Current Storage identity lacks permission"
            ) from None
        return StorageAccess(row.tenant_id, row.scope_key, "oauth", actor, permissions)
    if (
        not auth
        and "sig" not in request.query_params
        and service == "blob"
        and props.get("allowBlobPublicAccess")
    ):
        namespace = await session.get(
            StorageNamespace, (row.tenant_id, row.scope_key, "blob", resource.split("/")[0])
        )
        public = namespace.properties.get("public_access") if namespace else None
        if (public in {"blob", "container"} and action == "read_blob") or (
            public == "container" and action in {"read", "get_metadata", "list_blobs"}
        ):
            return StorageAccess(
                row.tenant_id, row.scope_key, "anonymous", None, frozenset({"r", "l"})
            )
    if not auth and "skoid" in request.query_params:
        permissions, _ = await authorize_service_sas(
            session, [], row, request, service, action, resource, now
        )
        return StorageAccess(row.tenant_id, row.scope_key, "user_delegation_sas", None, permissions)
    if not props.get("allowSharedKeyAccess", True):
        raise StorageError(
            403, "KeyBasedAuthenticationNotPermitted", "Account key authorization is disabled"
        )
    keys = [
        cipher(session).decrypt(getattr(row, k).encode()).decode("ascii") for k in ("key1", "key2")
    ]
    if action == "delegation":
        raise StorageError(
            403, "AuthorizationPermissionMismatch", "OAuth is required for delegation"
        )
    if scheme in {"SharedKey", "SharedKeyLite"}:
        if "sig" in request.query_params:
            raise StorageError(
                400, "InvalidAuthenticationInfo", "Multiple authentication mechanisms"
            )
        owner, _, sig = credential.partition(":")
        try:
            date = parsedate_to_datetime(values.get("x-ms-date", values.get("date", "")))
            if date.tzinfo is None or abs((now - date).total_seconds()) > 900 or owner != account:
                raise ValueError("Stale date or account")
            value = canonicalize(
                request.method,
                request.scope["raw_path"],
                request.query_params.multi_items(),
                pairs,
                account,
                service,
                values.get("x-ms-version", ""),
                lite=scheme == "SharedKeyLite",
            )
            if not matches(keys, sig, value):
                raise ValueError("Signature mismatch")
        except (ValueError, TypeError, UnicodeError):
            raise StorageError(
                403, "AuthenticationFailed", "Request signature or timestamp is invalid"
            ) from None
        return StorageAccess(row.tenant_id, row.scope_key, "shared_key", None, frozenset({"*"}))
    if not auth and "sig" in request.query_params:
        if "ss" not in request.query_params:
            permissions, _ = await authorize_service_sas(
                session, keys, row, request, service, action, resource, now
            )
            return StorageAccess(row.tenant_id, row.scope_key, "service_sas", None, permissions)
        permissions = authorize_account_sas(keys, account, request, service, action, resource, now)
        return StorageAccess(row.tenant_id, row.scope_key, "account_sas", None, permissions)
    raise StorageError(403, "AuthenticationFailed", "Storage authorization is required")
