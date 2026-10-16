import asyncio
import secrets
from datetime import UTC, datetime
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.db.models import Tenant
from mockazure.identity.credentials import hash_secret
from mockazure.identity.models import Application, Credential, Grant, ServicePrincipal, User
from mockazure.identity.types import ApplicationCreate, CredentialCreate, GrantCreate, UserCreate


class IdentityError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def public_user(row: User) -> dict:
    return {k: getattr(row, k) for k in ("id", "tenant_id", "upn", "display_name", "enabled")}


def public_application(row: Application) -> dict:
    return {
        k: getattr(row, k)
        for k in (
            "client_id",
            "tenant_id",
            "display_name",
            "client_mode",
            "redirect_uris",
            "required_permissions",
            "enabled",
        )
    }


class IdentityRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def owned(self, model, tenant_id, object_id):
        key = model.client_id if model is Application else model.id
        row = await self.session.scalar(
            select(model).where(model.tenant_id == tenant_id, key == object_id)
        )
        if row is None:
            raise IdentityError(404, "IdentityNotFound", "Identity not found in tenant")
        return row

    async def tenant(self, tenant_id):
        row = await self.session.get(Tenant, tenant_id)
        if row is None:
            raise IdentityError(404, "TenantNotFound", "Tenant not found")
        return row

    async def create_user(self, tenant_id: UUID, data: UserCreate) -> User:
        from mockazure.directory.mutations import AdminActor, DirectoryCommand, mutate_directory

        await self.tenant(tenant_id)
        result = await mutate_directory(
            self.session,
            tenant_id,
            DirectoryCommand(
                "user",
                "create",
                None,
                {
                    "displayName": data.display_name,
                    "userPrincipalName": data.upn,
                    "mailNickname": data.upn.partition("@")[0][:64],
                    "accountEnabled": data.enabled,
                    "passwordProfile": {
                        "password": data.password.get_secret_value(),
                        "forceChangePasswordNextSignIn": False,
                    },
                },
            ),
            AdminActor(),
            datetime.now(UTC),
        )
        return await self.session.get(User, result.object.id)

    async def create_application(self, tenant_id: UUID, data: ApplicationCreate) -> Application:
        await self.tenant(tenant_id)
        row = Application(tenant_id=tenant_id, **data.model_dump())
        self.session.add(row)
        await self.session.flush()
        self.session.add(ServicePrincipal(tenant_id=tenant_id, client_id=row.client_id))
        await self.session.flush()
        return row

    async def add_credential(
        self, tenant_id: UUID, client_id: UUID, data: CredentialCreate
    ) -> dict:
        app = await self.owned(Application, tenant_id, client_id)
        if app.client_mode != "confidential":
            raise IdentityError(422, "InvalidClientMode", "Public clients cannot own credentials")
        row = Credential(
            tenant_id=tenant_id, client_id=client_id, kind=data.kind, expires_at=data.expires_at
        )
        secret = None
        if data.kind == "secret":
            if data.public_certificate is not None:
                raise IdentityError(422, "InvalidCredential", "Unexpected certificate")
            secret = secrets.token_urlsafe(32)
            row.verifier = await asyncio.to_thread(hash_secret, secret)
        else:
            try:
                cert = x509.load_pem_x509_certificate((data.public_certificate or "").encode())
                key = cert.public_key()
                if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
                    raise ValueError()
                if not cert.not_valid_before_utc <= datetime.now(UTC) < cert.not_valid_after_utc:
                    raise ValueError()
                row.public_certificate = cert.public_bytes(serialization.Encoding.PEM).decode(
                    "ascii"
                )
                row.thumbprint = cert.fingerprint(hashes.SHA256()).hex()
                row.expires_at = min(row.expires_at, cert.not_valid_after_utc)
            except ValueError:
                raise IdentityError(
                    422, "InvalidCertificate", "A valid RSA lab certificate is required"
                ) from None
        self.session.add(row)
        await self.session.flush()
        result = {
            "id": row.id,
            "kind": row.kind,
            "expires_at": row.expires_at,
            "thumbprint": row.thumbprint,
        }
        if secret is not None:
            result["secret"] = secret
        return result

    async def set_grant(self, tenant_id: UUID, data: GrantCreate, grant_id: UUID) -> Grant:
        app = await self.session.get(Application, data.client_id)
        sp = await self.session.scalar(
            select(ServicePrincipal).where(
                ServicePrincipal.tenant_id == tenant_id,
                ServicePrincipal.client_id == data.client_id,
            )
        )
        if app is None or sp is None:
            raise IdentityError(404, "IdentityNotFound", "Application service principal not found")
        if data.user_id is not None:
            await self.owned(User, tenant_id, data.user_id)
        if data.kind == "application" and data.user_id is not None:
            raise IdentityError(422, "InvalidGrant", "Application grants cannot name a user")
        configured = {
            p
            for entry in app.required_permissions
            if entry["resource"] == data.resource and entry["kind"] == data.kind
            for p in entry["permissions"]
        }
        if not set(data.permissions) <= configured:
            raise IdentityError(
                422, "InvalidGrant", "Permission is not configured on the application"
            )
        existing = await self.session.get(Grant, grant_id)
        if existing and existing.tenant_id != tenant_id:
            raise IdentityError(404, "IdentityNotFound", "Grant not found")
        row = existing or Grant(id=grant_id, tenant_id=tenant_id)
        for key, value in data.model_dump().items():
            setattr(row, key, value)
        self.session.add(row)
        await self.session.flush()
        return row

    async def revoke_credential(self, tenant_id: UUID, credential_id: UUID) -> None:
        row = await self.owned(Credential, tenant_id, credential_id)
        row.revoked_at = datetime.now(UTC)
        await self.session.flush()

    async def set_enabled(self, tenant_id: UUID, object_id: UUID, enabled: bool, model=User):
        if model is User:
            from mockazure.directory.mutations import AdminActor, DirectoryCommand, mutate_directory

            await mutate_directory(
                self.session,
                tenant_id,
                DirectoryCommand("user", "update", object_id, {"accountEnabled": enabled}),
                AdminActor(),
                datetime.now(UTC),
            )
            return await self.session.get(User, object_id)
        row = await self.owned(model, tenant_id, object_id)
        row.enabled = enabled
        await self.session.flush()
        return row
