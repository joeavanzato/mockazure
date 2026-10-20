"""Provision explicit local permissions and resolve server-held credentials."""

import asyncio
import json
import secrets
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr
from sqlalchemy import select

from mockazure.admin.errors import AdminError
from mockazure.identity.credentials import hash_secret
from mockazure.identity.keys import read_wrapping_key
from mockazure.identity.models import ServicePrincipal, User
from mockazure.identity.repository import IdentityRepository
from mockazure.identity.types import (
    ApplicationCreate,
    CredentialCreate,
    GrantCreate,
    RequiredPermission,
)
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.identity_models import LabIdentity
from mockazure.population.transport import ClientCredential
from mockazure.population.types import FamilyDescriptor, Input, OperationDescriptor
from mockazure.security.models import SecurityOwner


class IdentityFixture(Input):
    id: str = Field(min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9._:-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    permissions: list[RequiredPermission] = Field(default_factory=list, max_length=64)
    delegated_user_id: UUID | None = None
    owner_user_id: UUID | None = None


async def resolve_identity(ctx, name):
    if not name:
        raise AdminError(422, "IdentityRequired", "Select a named local test identity")
    async with ctx.database.sessions.begin() as session:
        row = await session.get(LabIdentity, (ctx.tenant_id, name))
        if row is None:
            raise AdminError(404, "IdentityNotFound", "Named local test identity not found")
        cipher = Fernet(read_wrapping_key(ctx.settings.data_dir))
        clear = cipher.decrypt(row.encrypted_secret.encode()).decode()
        if row.definition.get("delegated_user_id"):
            user = await session.get(User, UUID(row.definition["delegated_user_id"]))
            if user is None or user.tenant_id != ctx.tenant_id:
                raise AdminError(404, "IdentityNotFound", "Named local user was not found")
            saved = json.loads(clear)
            return ClientCredential(
                row.client_id,
                SecretStr(saved["client_secret"]),
                username=user.upn,
                password=SecretStr(saved["password"]),
                scopes=tuple(row.definition["permissions"][0]["permissions"]),
            )
        return ClientCredential(row.client_id, SecretStr(clear))


def adapters():
    def normalize(payload):
        value = IdentityFixture.model_validate(payload)
        if value.delegated_user_id is not None:
            if (
                len(value.permissions) != 1
                or value.permissions[0].resource != "graph"
                or value.permissions[0].kind != "delegated"
                or not value.permissions[0].permissions
            ):
                raise ValueError("Named delegated identities require one explicit Graph scope set")
        elif any(permission.kind != "application" for permission in value.permissions):
            raise ValueError("Named service identities use application permissions")
        return value.model_dump(mode="json", exclude_none=True)

    async def write(session, ctx, pending):
        from mockazure.security.repository import lock_security

        await lock_security(session, ctx.tenant_id)
        cipher = Fernet(read_wrapping_key(ctx.settings.data_dir))
        repo = IdentityRepository(session)
        for _, payload in pending:
            value = IdentityFixture.model_validate(payload)
            if value.owner_user_id:
                owner = await session.get(User, value.owner_user_id)
                if (
                    owner is None
                    or owner.tenant_id != ctx.tenant_id
                    or owner.deleted_at is not None
                ):
                    raise AdminError(422, "MissingOwner", "Application owner must be a local user")
            password = None
            if value.delegated_user_id:
                user = await session.scalar(
                    select(User)
                    .where(
                        User.id == value.delegated_user_id,
                        User.tenant_id == ctx.tenant_id,
                        User.enabled.is_(True),
                        User.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
                if user is None or user.verifier != "!":
                    raise AdminError(
                        422,
                        "CredentialBootstrapDenied",
                        "Delegated fixture requires a local imported user without a credential",
                    )
                password = secrets.token_urlsafe(32)
                user.verifier = await asyncio.to_thread(hash_secret, password)
                user.credential_version += 1
            app = await repo.create_application(
                ctx.tenant_id,
                ApplicationCreate(
                    display_name=value.display_name,
                    client_mode="confidential",
                    redirect_uris=["http://localhost:8123/mockazure-population"]
                    if value.delegated_user_id
                    else [],
                    required_permissions=value.permissions,
                ),
            )
            credential = await repo.add_credential(
                ctx.tenant_id, app.client_id, CredentialCreate(kind="secret")
            )
            if value.owner_user_id:
                principal = await session.scalar(
                    select(ServicePrincipal).where(
                        ServicePrincipal.tenant_id == ctx.tenant_id,
                        ServicePrincipal.client_id == app.client_id,
                    )
                )
                for identifier in (app.object_id, principal.id):
                    session.add(
                        SecurityOwner(
                            tenant_id=ctx.tenant_id,
                            object_id=identifier,
                            owner_id=value.owner_user_id,
                        )
                    )
            for permission in value.permissions:
                await repo.set_grant(
                    ctx.tenant_id,
                    GrantCreate(
                        client_id=app.client_id,
                        user_id=value.delegated_user_id,
                        **permission.model_dump(),
                    ),
                    uuid4(),
                )
            session.add(
                LabIdentity(
                    tenant_id=ctx.tenant_id,
                    name=value.id,
                    client_id=app.client_id,
                    encrypted_secret=cipher.encrypt(
                        (
                            json.dumps(
                                {"client_secret": credential["secret"], "password": password}
                            )
                            if password
                            else credential["secret"]
                        ).encode()
                    ).decode(),
                    definition=payload,
                )
            )

    return [
        PayloadAdapter(
            FamilyDescriptor(
                key="identity.clients",
                service="Identity and permissions",
                api_path="/v1.0/applications",
                display_fields=["id", "display_name", "permissions"],
                operations=[
                    OperationDescriptor(
                        key="provision",
                        mode="fixture",
                        method="POST",
                        api_version="local-fixture-v1",
                        path_template="/_mock/tenants/{tenant}/resources/identity.clients/apply",
                        input_schema=IdentityFixture.model_json_schema(),
                    )
                ],
                contract_refs=["docs/identity.md"],
                limitations=[
                    "Explicit named local application identity. Credentials remain encrypted "
                    "on the server.",
                    "Existing identities are create-only; change grants through identity "
                    "management.",
                    "Optional delegated_user_id bootstraps only credentialless imported users; "
                    "Graph scopes, consent and ordinary local login/policy checks apply.",
                ],
            ),
            ReadSpec(LabIdentity, LabIdentity.name, LabIdentity.definition, LabIdentity.revision),
            normalize,
            write,
        )
    ]
