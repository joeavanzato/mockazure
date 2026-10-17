from mockazure.admin.routes import capabilities


async def test_security_capabilities_advertise_bounded_operational_surfaces():
    value = await capabilities()
    assert value["milestone"] == 3 and value["delivery"] == "C"
    assert {
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
