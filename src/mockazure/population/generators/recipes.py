"""Small connected setup recipes, composed by declarative templates."""

from datetime import timedelta

from mockazure.population.generators.cloud_resources import resources
from mockazure.population.generators.common import Generation
from mockazure.population.types import Mutation

ROLES = [
    "Contributor",
    "Storage Blob Data Contributor",
    "Storage File Data Privileged Contributor",
    "Storage Queue Data Contributor",
    "Storage Table Data Contributor",
    "Key Vault Secrets Officer",
]
PROVIDERS = ["Microsoft.Storage", "Microsoft.KeyVault", "Microsoft.OperationalInsights"]
RECIPE_FAMILIES = {
    "client": ("identity.clients", "provision", "fixture"),
    "mail-settings": ("mail.settings", "provision", "fixture"),
    "mailbox": ("mail.mailboxes", "provision", "fixture"),
    "mail-folders": ("mail.folders", "append", "fixture"),
    "calendar": ("mail.events", "create", "service"),
    "license": ("identity.skus", "provision", "fixture"),
    "subscription": ("arm.subscriptions", "import", "fixture"),
    "group": ("arm.resourceGroups", "import", "fixture"),
    "roles": ("arm.roles", "assign", "fixture"),
    "providers": ("arm.providers", "register", "service"),
    "account": ("storage.accounts", "create", "service"),
    "vault": ("keyvault.vaults", "create", "service"),
    "workspace": ("monitor.workspaces", "create", "service"),
    "secret": ("keyvault.secrets", "set", "service"),
    "container": ("storage.containers", "create", "service"),
    "share": ("storage.shares", "create", "service"),
    "queue": ("storage.queues", "create", "service"),
    "table": ("storage.tables", "create", "service"),
    "content": ("content.objects", "generate", "fixture"),
    "blob": ("storage.blobs", "upload", "service"),
    "file": ("storage.files", "upload", "service"),
    "entity": ("storage.entities", "insert", "service"),
    "queue-message": ("storage.messages", "enqueue", "service"),
    "diagnostics": ("monitor.diagnostics", "create", "service"),
    "site-drive-link": ("sharepoint.relationships", "link", "fixture"),
    "team-channel-link": ("teams.relationships", "link", "fixture"),
}


def generate(name, parameters, refs, *, offset, limit):
    kind = name.removeprefix("recipe.")
    if kind not in RECIPE_FAMILIES:
        raise ValueError("Unknown setup recipe")
    g = Generation(parameters, refs, offset, limit)
    maximum = (
        len(ROLES)
        if kind == "roles"
        else len(PROVIDERS)
        if kind == "providers"
        else 3
        if kind == "mail-folders"
        else 1
    )
    if g.count > maximum:
        raise ValueError("Recipe count exceeds its fixed setup inventory")
    _, operation, mode = RECIPE_FAMILIES[kind]
    output = []
    for i in g.indexes:
        parent = None
        if kind == "client":
            body = {
                "id": "lab-" + g.label,
                "display_name": "Lab client " + g.namespace,
                "permissions": [
                    {
                        "resource": "graph",
                        "kind": "application",
                        "permissions": [
                            "Directory.Read.All",
                            "AuditLog.Read.All",
                            "Mail.ReadWrite",
                            "Calendars.ReadWrite",
                            "Sites.Read.All",
                            "DeviceManagementManagedDevices.Read.All",
                        ],
                    },
                    {
                        "resource": "activity",
                        "kind": "application",
                        "permissions": ["ActivityFeed.Read"],
                    },
                ],
            }
        elif kind == "mail-settings":
            body = {"id": str(g.tenant), "exchange_enabled": True, "audit_tier": "premium"}
        elif kind == "mailbox":
            body = {"id": refs["primary-user"], "primary_smtp": g.upn(0), "alias": "lab-" + g.label}
        elif kind == "mail-folders":
            folder = ["inbox", "drafts", "sentitems"][i]
            parent, body = (
                refs["primary-user"],
                {"id": folder, "displayName": folder.title(), "well_known_name": folder},
            )
        elif kind == "calendar":
            body = {
                "variables": {"user": refs["primary-user"]},
                "body": {
                    "subject": "Synthetic evidence review",
                    "transactionId": g.id("meeting"),
                    "start": {
                        "dateTime": g.reference.replace(tzinfo=None).isoformat(),
                        "timeZone": "UTC",
                    },
                    "end": {
                        "dateTime": (g.reference + timedelta(hours=1))
                        .replace(tzinfo=None)
                        .isoformat(),
                        "timeZone": "UTC",
                    },
                },
            }
        elif kind == "license":
            body = {
                "id": "061f9ace-7d42-4136-88ac-31dc755f143f",
                "sku_part_number": "INTUNE_A",
                "enabled": g.users,
                "service_plans": [
                    {
                        "servicePlanId": "c1ec4a95-1f05-45b3-a911-aa3fa01094f5",
                        "servicePlanName": "INTUNE_A",
                        "provisioningStatus": "Success",
                    }
                ],
            }
        elif kind == "subscription":
            body = {"id": g.id("subscription"), "displayName": "Lab " + g.namespace}
        elif kind == "group":
            body = {
                "id": "/subscriptions/" + refs["subscription"] + "/resourceGroups/lab-" + g.label,
                "location": "eastus",
                "tags": {"dataset": g.namespace},
            }
        elif kind == "roles":
            body = {
                "id": g.id("role", i),
                "scope": "/subscriptions/" + refs["subscription"],
                "role": ROLES[i],
                "identity_ref": refs["client"],
            }
        elif kind == "providers":
            body = {"variables": {"subscription": refs["subscription"], "namespace": PROVIDERS[i]}}
        elif kind in {"account", "vault", "workspace"}:
            type_name = {
                "account": "Microsoft.Storage/storageAccounts",
                "vault": "Microsoft.KeyVault/vaults",
                "workspace": "Microsoft.OperationalInsights/workspaces",
            }[kind]
            resource = next(
                r
                for r in resources(refs["group"], str(g.tenant), g.label)
                if r["type"] == type_name
            )
            body = {"variables": {"resource": resource["id"]}, "body": resource["body"]}
        elif kind == "content":
            body = {
                "text": "Synthetic " + g.namespace + " evidence.\n",
                "repeat": 128,
                "media_type": "text/plain",
            }
        elif kind == "secret":
            body = {
                "variables": {"vault": refs["vault"].rsplit("/", 1)[-1], "secret": "lab-example"},
                "body": {
                    "value": "synthetic-" + g.id("secret-value"),
                    "contentType": "text/plain",
                    "tags": {"dataset": g.namespace},
                },
            }
        elif kind in {
            "container",
            "share",
            "queue",
            "table",
            "blob",
            "file",
            "entity",
            "queue-message",
        }:
            variables = {"account": refs["account"].rsplit("/", 1)[-1]}
            body = {"variables": variables}
            if kind in {"container", "share", "queue"}:
                variables[kind] = "evidence"
            elif kind == "table":
                body["body"] = {"TableName": "Evidence"}
            elif kind in {"blob", "file"}:
                variables["container" if kind == "blob" else "share"] = "evidence"
                variables[kind] = "sample.txt"
                body["content_id"] = refs["content"]
            elif kind == "entity":
                variables["table"] = refs["table"]
                body["body"] = {
                    "PartitionKey": g.label,
                    "RowKey": "example",
                    "Description": "Synthetic evidence",
                    "Count": 1,
                }
            else:
                variables["queue"] = "evidence"
                body["body"] = {"text": "Synthetic queue item " + g.namespace}
        elif kind == "diagnostics":
            body = {
                "variables": {
                    "resource": refs["account"]
                    + "/blobServices/default/providers/Microsoft.Insights/diagnosticSettings/lab"
                },
                "body": {
                    "properties": {
                        "workspaceId": refs["group"]
                        + "/providers/Microsoft.OperationalInsights/workspaces/logs-"
                        + g.label,
                        "logAnalyticsDestinationType": "Dedicated",
                        "logs": [{"category": "StorageWrite", "enabled": True}],
                    }
                },
            }
        elif kind == "site-drive-link":
            parent, body = (
                refs["site"],
                {
                    "source": {"kind": "site", "id": refs["site"]},
                    "relation": "drives",
                    "target": {"kind": "drive", "id": refs["drive"]},
                },
            )
        else:
            parent, body = (
                refs["team"],
                {
                    "source": {"kind": "team", "id": refs["team"]},
                    "relation": "channels",
                    "target": {"kind": "channel", "id": refs["channel"]},
                },
            )
        output.append(
            Mutation(
                operation=operation,
                logical_key=f"{name}:{i}",
                parent=parent,
                payload=body,
                identity_ref=refs["client"] if mode == "service" else None,
            )
        )
    return output
