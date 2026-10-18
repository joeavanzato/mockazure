"""Tenant-scoped Exchange configuration import/export and bounded command reads."""

from uuid import UUID

from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.identity.models import User
from mockazure.mail.collection_models import (
    ExchangeConfiguration,
    ExchangeConfigurationRecord,
    ExchangePermissionRecord,
)
from mockazure.mail.models import Mailbox
from mockazure.providers.graph.errors import GraphError


async def export_collection_state(session, tenant_id):
    if await session.get(Tenant, tenant_id, with_for_update=True) is None:
        raise AdminError(404, "TenantNotFound", "Tenant not found")
    row = await session.get(ExchangeConfiguration, tenant_id)
    result = {
        "organization": row.organization if row else {},
        "transport": row.transport if row else {},
        "accepted_domains": [],
        "transport_rules": [],
        "mailbox_permissions": [],
        "recipient_permissions": [],
    }
    for record in await session.scalars(
        select(ExchangeConfigurationRecord)
        .where(ExchangeConfigurationRecord.tenant_id == tenant_id)
        .order_by(ExchangeConfigurationRecord.ordinal, ExchangeConfigurationRecord.id)
    ):
        key = "accepted_domains" if record.kind == "acceptedDomain" else "transport_rules"
        result[key].append(record.payload)
    for record in await session.scalars(
        select(ExchangePermissionRecord)
        .join(
            Mailbox,
            (Mailbox.tenant_id == ExchangePermissionRecord.tenant_id)
            & (Mailbox.user_id == ExchangePermissionRecord.mailbox_user_id),
        )
        .join(User, (User.tenant_id == Mailbox.tenant_id) & (User.id == Mailbox.user_id))
        .where(ExchangePermissionRecord.tenant_id == tenant_id, User.deleted_at.is_(None))
        .order_by(ExchangePermissionRecord.ordinal, ExchangePermissionRecord.id)
    ):
        result[record.family + "_permissions"].append(
            {"id": str(record.id), "mailbox_user_id": str(record.mailbox_user_id), **record.payload}
        )
    return result


async def replace_collection_state(session, tenant_id, value):
    # One tenant lock serializes complete replacements; validation precedes deletion.
    tenant = await session.get(Tenant, tenant_id, with_for_update=True)
    if tenant is None:
        raise AdminError(404, "TenantNotFound", "Tenant not found")
    permissions = value.mailbox_permissions + value.recipient_permissions
    identifiers = {record.mailbox_user_id for record in permissions}
    found = set(
        await session.scalars(
            select(Mailbox.user_id)
            .join(User, (User.tenant_id == Mailbox.tenant_id) & (User.id == Mailbox.user_id))
            .where(
                Mailbox.tenant_id == tenant_id,
                Mailbox.user_id.in_(identifiers),
                User.deleted_at.is_(None),
            )
        )
    )
    if found != identifiers:
        raise AdminError(404, "MailboxNotFound", "Permission mailbox was not found in this tenant")
    for model in (ExchangePermissionRecord, ExchangeConfigurationRecord, ExchangeConfiguration):
        await session.execute(delete(model).where(model.tenant_id == tenant_id))
    session.add(
        ExchangeConfiguration(
            tenant_id=tenant_id,
            organization=value.organization.model_dump(mode="json", exclude_unset=True),
            transport=value.transport.model_dump(mode="json", exclude_unset=True),
        )
    )
    for kind, records in (
        ("acceptedDomain", value.accepted_domains),
        ("transportRule", value.transport_rules),
    ):
        for ordinal, record in enumerate(records):
            session.add(
                ExchangeConfigurationRecord(
                    tenant_id=tenant_id,
                    kind=kind,
                    id=record.Guid,
                    ordinal=ordinal,
                    payload=record.model_dump(mode="json", exclude_unset=True),
                )
            )
    for family, records in (
        ("mailbox", value.mailbox_permissions),
        ("recipient", value.recipient_permissions),
    ):
        for ordinal, record in enumerate(records):
            session.add(
                ExchangePermissionRecord(
                    tenant_id=tenant_id,
                    family=family,
                    id=record.id,
                    mailbox_user_id=record.mailbox_user_id,
                    ordinal=ordinal,
                    payload=record.model_dump(
                        mode="json", exclude={"id", "mailbox_user_id"}, exclude_unset=True
                    ),
                )
            )
    await session.flush()
    return await export_collection_state(session, tenant_id)


async def read_configuration(
    session, tenant_id: UUID, command: str, parameters: dict
) -> list[dict]:
    from mockazure.providers.exchange.commands import invalid, result_size

    tenant = await session.get(Tenant, tenant_id)
    row = await session.get(ExchangeConfiguration, tenant_id)
    if command in {"Get-OrganizationConfig", "Get-TransportConfig"}:
        identity = parameters.get("Identity")
        if identity and identity.casefold() not in {str(tenant_id), tenant.display_name.casefold()}:
            raise GraphError(
                404, "ManagementObjectNotFoundException", "Configuration identity was not found"
            )
        data = (
            (row.organization if command == "Get-OrganizationConfig" else row.transport)
            if row
            else {}
        )
        return [
            {
                "Identity": str(tenant_id),
                "Name": tenant.display_name,
                "Guid": str(tenant_id),
                **(
                    {"ExternalDirectoryOrganizationId": str(tenant_id)}
                    if command == "Get-OrganizationConfig"
                    else {}
                ),
                **data,
            }
        ]
    kind = {"Get-AcceptedDomain": "acceptedDomain", "Get-TransportRule": "transportRule"}[command]
    records = [
        record.payload
        for record in await session.scalars(
            select(ExchangeConfigurationRecord)
            .where(
                ExchangeConfigurationRecord.tenant_id == tenant_id,
                ExchangeConfigurationRecord.kind == kind,
            )
            .order_by(ExchangeConfigurationRecord.ordinal, ExchangeConfigurationRecord.id)
        )
    ]
    if identity := parameters.get("Identity"):
        records = [
            record
            for record in records
            if identity.casefold()
            in {
                str(record["Guid"]).casefold(),
                record["Name"].casefold(),
                record.get("DomainName", "").casefold(),
            }
        ]
        if len(records) != 1:
            raise GraphError(
                404 if not records else 400,
                "ManagementObjectNotFoundException",
                "Configuration identity was not found uniquely",
            )
    if "State" in parameters:
        if parameters["State"] not in {"Enabled", "Disabled"}:
            invalid("Unsupported transport rule state")
        records = [record for record in records if record["State"] == parameters["State"]]
    if kind == "transportRule":
        records.sort(key=lambda record: (record["Priority"], record["Guid"]))
    return [{"Identity": record["Name"], **record} for record in records[: result_size(parameters)]]
