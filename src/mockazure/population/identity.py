"""Provision explicit local permissions and resolve server-held credentials."""

from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr

from mockazure.admin.errors import AdminError
from mockazure.identity.keys import read_wrapping_key
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


class IdentityFixture(Input):
    id: str = Field(min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9._:-]+$")
    display_name: str = Field(min_length=1, max_length=200)
    permissions: list[RequiredPermission] = Field(default_factory=list, max_length=64)


async def resolve_identity(ctx, name):
    if not name:
        raise AdminError(422, "IdentityRequired", "Select a named local test identity")
    async with ctx.database.sessions.begin() as session:
        row = await session.get(LabIdentity, (ctx.tenant_id, name))
        if row is None:
            raise AdminError(404, "IdentityNotFound", "Named local test identity not found")
        cipher = Fernet(read_wrapping_key(ctx.settings.data_dir))
        return ClientCredential(
            row.client_id, SecretStr(cipher.decrypt(row.encrypted_secret.encode()).decode())
        )


def adapters():
    def normalize(payload):
        value = IdentityFixture.model_validate(payload)
        if any(permission.kind != "application" for permission in value.permissions):
            raise ValueError("Named service identities use application permissions")
        return value.model_dump(mode="json")

    async def write(session, ctx, pending):
        from mockazure.security.repository import lock_security

        await lock_security(session, ctx.tenant_id)
        cipher = Fernet(read_wrapping_key(ctx.settings.data_dir))
        repo = IdentityRepository(session)
        for _, payload in pending:
            value = IdentityFixture.model_validate(payload)
            app = await repo.create_application(
                ctx.tenant_id,
                ApplicationCreate(
                    display_name=value.display_name,
                    client_mode="confidential",
                    required_permissions=value.permissions,
                ),
            )
            credential = await repo.add_credential(
                ctx.tenant_id, app.client_id, CredentialCreate(kind="secret")
            )
            for permission in value.permissions:
                await repo.set_grant(
                    ctx.tenant_id,
                    GrantCreate(client_id=app.client_id, **permission.model_dump()),
                    uuid4(),
                )
            session.add(
                LabIdentity(
                    tenant_id=ctx.tenant_id,
                    name=value.id,
                    client_id=app.client_id,
                    encrypted_secret=cipher.encrypt(credential["secret"].encode()).decode(),
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
                ],
            ),
            ReadSpec(LabIdentity, LabIdentity.name, LabIdentity.definition, LabIdentity.revision),
            normalize,
            write,
        )
    ]
