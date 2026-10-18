from types import SimpleNamespace

from mockazure.admin.routes import capabilities
from mockazure.config import Settings


async def test_security_capabilities_advertise_bounded_operational_surfaces(tmp_path):
    value = await capabilities(
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    settings=Settings(data_dir=tmp_path, database_url="postgresql://localhost/unit")
                )
            )
        )
    )
    assert value["milestone"] == 5 and value["delivery"] == "collection-evidence"
    assert {
        "arm.resources.inventory",
        "arm.resourceGroups.lifecycle",
        "arm.tags",
        "arm.roleDefinitions",
        "arm.roleAssignments",
        "arm.activityLogs",
        "graph.mail.inboxRules",
        "graph.security.auditLog.queries",
        "exchange.mailbox.configuration",
        "exchange.mailbox.statistics",
        "exchange.inboxRules",
        "exchange.sweepRules",
        "exchange.messageTrace",
        "graph.applications.lifecycle",
        "graph.servicePrincipals.lifecycle",
        "graph.devices.lifecycle",
        "graph.security.owners",
        "graph.security.credentials",
        "graph.oauth2PermissionGrants",
        "graph.servicePrincipals.appRoleAssignments",
        "graph.directoryRoles",
        "graph.roleManagement.directory",
        "graph.authenticationMethods.inspect",
        "graph.reports.userRegistrationDetails",
        "graph.users.signInActivity",
        "graph.conditionalAccess.policies",
        "graph.conditionalAccess.namedLocations",
        "graph.conditionalAccess.enforcement",
    } <= set(value["provider_operations"])
    assert "security.authenticationMethods" in value["admin_operations"]
    assert not any("no conditional-access engine" in value for value in value["limitations"])
    assert any("Physical" in value for value in value["limitations"])
