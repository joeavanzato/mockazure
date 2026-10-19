"""Read-only scalar projections over a fixed set of service tables."""

from mockazure.admin.errors import AdminError
from mockazure.population.catalog import get_adapter
from mockazure.population.types import ResourceQuery

# JSON values are never passed through wholesale. Unknown/nested values are omitted.
TABLES = {
    "directory.users": ("id", "displayName", "userPrincipalName", "accountEnabled"),
    "directory.groups": ("id", "displayName", "mailNickname"),
    "security.alerts_v2": ("id", "title", "severity", "status"),
    "sharepoint.site": ("id", "displayName", "name"),
    "teams.team": ("id", "displayName"),
    "intune.managedDevice": ("id", "displayName", "name"),
    "mail.mailboxes": ("id", "primary_smtp", "alias", "mailbox_type"),
    "arm.resourceGroups": ("id", "name", "location"),
    "storage.accounts": ("id", "name", "kind", "location"),
    "keyvault.vaults": ("id", "name", "location"),
    "monitor.workspaces": ("id", "name", "customer_id"),
    "activity.records": ("Id", "Operation", "CreationTime", "UserId"),
}


def descriptors():
    return [
        {"key": key, "scope": "tenant", "columns": list(fields)} for key, fields in TABLES.items()
    ]


async def inspect_table(ctx, table_key, tenant_id, limit, continuation):
    if table_key not in TABLES or tenant_id is None or ctx.tenant_id != tenant_id:
        raise AdminError(422, "InvalidInspectorScope", "Select an approved table and tenant")
    page = await get_adapter(table_key).browse(
        ctx, ResourceQuery(limit=limit, continuation=continuation)
    )
    for row in page.items:
        row.properties = {
            key: value
            for key, value in row.properties.items()
            if key in TABLES[table_key]
            and (value is None or type(value) in {str, bool, int, float})
        }
    return page
