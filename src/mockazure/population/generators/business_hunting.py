"""Twenty correlated fixture tables over the connected business identity namespace."""

from mockazure.hunting.contracts import catalog
from mockazure.hunting.schemas import validate_row
from mockazure.population.corpus.models import ContentSpec
from mockazure.population.corpus.render import render
from mockazure.population.generators import hunting_devices, hunting_identity, hunting_mail
from mockazure.population.generators.business import Business
from mockazure.population.types import Mutation

VERSION = "business-hunting-v1"
FAMILIES = {table: "hunting." + table for table in catalog()}
FAMILIES.update(alerts="security.alerts_v2", settings="hunting.settings", client="identity.clients")
OVERLAYS = {
    "suspicious-consent": ("Consent to application", "Microsoft Entra ID", "Application"),
    "mailbox-forwarding": ("Set-Mailbox", "Exchange", "Mailbox"),
    "unusual-download": ("FileDownloaded", "SharePoint", "File"),
    "privilege-change": ("Add member to role", "Microsoft Entra ID", "Role"),
    "unusual-secret-access": ("SecretGet", "Azure Key Vault", "Secret"),
}


class HuntingBusiness(Business):
    def __init__(self, *args):
        super().__init__(*args)
        self.documents = {}

    def device_id(self, i):
        return self.id("defender-device", i % self.users).replace("-", "")

    def device_name(self, i):
        return f"BUS-{self.label}-{i % self.users:05d}"

    def rendered(self, project, revision):
        key = project, revision
        if key not in self.documents:
            self.documents[key] = render(ContentSpec.model_validate(self.document(*key)))
        return self.documents[key]

    def alert_id(self, i):
        return self.id("business-hunting-alert", i % self.users)


def record(g, table, i):
    base = {"Timestamp": g.time(i)}
    if table.startswith("Identity"):
        return base | hunting_identity.row(g, table, i)
    if table.startswith("Email") or table == "UrlClickEvents":
        return base | hunting_mail.row(g, table, i)
    if table.startswith("Device"):
        return base | hunting_devices.row(g, table, i)
    if table == "CloudAppEvents":
        person = g.person(i)
        overlay = g.parameters.get("overlay")
        if overlay is not None and overlay not in OVERLAYS:
            raise ValueError("Unknown declarative hunting overlay")
        action, app, kind = OVERLAYS[overlay] if overlay else ("FileAccessed", "SharePoint", "File")
        value = base | {
            "ActionType": action,
            "Application": app,
            "AccountObjectId": person["id"],
            "AccountId": person["userPrincipalName"],
            "AccountDisplayName": person["displayName"],
            "ObjectId": g.id("document", i % g.projects),
            "ObjectType": kind,
            "ReportId": g.id("hunting-report", i),
            "AdditionalFields": {
                "simulation": "fixture",
                "overlay": overlay or "ordinary-business",
            },
        }
        if overlay in {"suspicious-consent", "unusual-secret-access"}:
            # No fabricated application/vault object ID. These fixtures identify
            # their actual subject; optional unresolved target properties stay null.
            value.pop("ObjectId")
        elif overlay in {"mailbox-forwarding", "privilege-change"}:
            value["ObjectId"] = (
                person["userPrincipalName"] if overlay == "mailbox-forwarding" else person["id"]
            )
            value["ObjectType"] = "Mailbox" if overlay == "mailbox-forwarding" else "User"
        return value
    value = base | {
        "AlertId": g.alert_id(i),
        "Title": f"Synthetic business alert {i % g.users}",
        "Severity": "Informational",
        "ServiceSource": "Microsoft Defender for Endpoint",
        "DetectionSource": "Custom detection",
    }
    if table == "AlertEvidence":
        value.update(
            EntityType="User",
            EvidenceRole="Impacted",
            AccountObjectId=g.person(i)["id"],
            AccountUpn=g.person(i)["userPrincipalName"],
            DeviceId=g.device_id(i),
            DeviceName=g.device_name(i),
        )
    return value


def generate(name, parameters, refs, *, offset, limit):
    table = name.removeprefix("hunting.")
    if table not in FAMILIES:
        raise ValueError("Unknown hunting generator")
    g = HuntingBusiness(parameters, refs, offset, limit)
    output = []
    for i in g.indexes:
        operation = "import"
        if table == "settings":
            if g.count != 1:
                raise ValueError("One settings record per tenant")
            operation = "provision"
            body = {
                "id": str(g.tenant),
                "entitlements": ["identity", "endpoint", "email", "cloud-apps", "alerts"],
            }
        elif table == "client":
            if g.count != 1:
                raise ValueError("One hunting client per namespace")
            operation = "provision"
            body = {
                "id": "hunting-" + g.label,
                "display_name": "Local hunting reader " + g.namespace,
                "permissions": [
                    {
                        "resource": "graph",
                        "kind": "application",
                        "permissions": ["ThreatHunting.Read.All", "SecurityAlert.Read.All"],
                    }
                ],
            }
        elif table == "alerts":
            body = {
                "id": g.alert_id(i),
                "title": f"Synthetic business alert {i}",
                "severity": "informational",
                "tenantId": str(g.tenant),
                "status": "new",
                "createdDateTime": g.time(i),
                "lastUpdateDateTime": g.time(i),
                "serviceSource": "microsoftDefenderForEndpoint",
                "detectionSource": "customDetection",
                "evidence": [
                    {
                        "@odata.type": "#microsoft.graph.security.userEvidence",
                        "createdDateTime": g.time(i),
                        "userAccount": {
                            "azureAdUserId": g.person(i)["id"],
                            "userPrincipalName": g.person(i)["userPrincipalName"],
                        },
                    }
                ],
            }
        else:
            overlay = parameters.get("overlay", "baseline")
            body = {
                "id": g.id("hunting-" + overlay + "-" + table, i),
                "record": validate_row(table, record(g, table, i)),
            }
        output.append(
            Mutation(
                operation=operation,
                logical_key=f"{name}.{parameters.get('overlay', 'baseline')}:{i}",
                payload=body,
            )
        )
    return output
