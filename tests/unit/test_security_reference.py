"""Security contracts reject invented fields and distinguish scope/role identifiers."""

from uuid import UUID

import pytest

from mockazure.providers.graph.schema_reference import schema, validate_payload


@pytest.mark.parametrize(
    ("resource", "payload"),
    [
        ("application", {"displayName": "Collector", "appId": "local-app"}),
        ("servicePrincipal", {"displayName": "Collector", "accountEnabled": True}),
        ("device", {"displayName": "Device", "accountEnabled": True}),
        ("oAuth2PermissionGrant", {"consentType": "AllPrincipals", "scope": "User.Read"}),
        ("appRoleAssignment", {"appRoleId": "df021288-bdef-4463-88db-98f22de89214"}),
        ("unifiedRoleDefinition", {"displayName": "Reader", "isBuiltIn": True}),
        ("unifiedRoleAssignment", {"directoryScopeId": "/"}),
        ("directoryRole", {"displayName": "Directory Readers"}),
        ("passwordAuthenticationMethod", {"createdDateTime": "2026-10-16T00:00:00Z"}),
        ("phoneAuthenticationMethod", {"phoneType": "mobile", "phoneNumber": "+15550100000"}),
        ("emailAuthenticationMethod", {"emailAddress": "user@example.test"}),
        ("microsoftAuthenticatorAuthenticationMethod", {"displayName": "Phone"}),
        ("fido2AuthenticationMethod", {"model": "Local inventory"}),
        ("conditionalAccessPolicy", {"state": "enabledForReportingButNotEnforced"}),
        ("ipNamedLocation", {"isTrusted": True, "ipRanges": []}),
        ("keyCredential", {"key": "AQID", "customKeyIdentifier": "AQI="}),
    ],
)
@pytest.mark.parametrize("version", ["v1.0", "beta"])
def test_security_reference_types(version, resource, payload):
    validate_payload(version, resource, payload, partial=True)
    with pytest.raises(ValueError, match="Undeclared"):
        validate_payload(version, resource, payload | {"invented": True}, partial=True)


@pytest.mark.parametrize(
    ("resource", "payload"),
    [
        ("application", {"appId": 7}),
        ("device", {"accountEnabled": "true"}),
        ("conditionalAccessPolicy", {"state": "madeUp"}),
        ("conditionalAccessPolicy", {"state": None}),
        ("phoneAuthenticationMethod", {"phoneType": "pager"}),
        ("keyCredential", {"key": "not base64!"}),
        ("keyCredential", {"key": 123}),
    ],
)
def test_security_reference_rejects_invalid_types(resource, payload):
    with pytest.raises(ValueError):
        validate_payload("v1.0", resource, payload, partial=True)


def test_permission_ids():
    from mockazure.security.catalog import permission_id, permission_name, resource_app_id

    application = UUID("df021288-bdef-4463-88db-98f22de89214")
    delegated = UUID("a154be20-db9c-4678-8ab7-66f6cc099a59")
    assert permission_id("User.Read.All", "application") == application
    assert permission_id("User.Read.All", "delegated") == delegated
    assert permission_name(application, "application") == "User.Read.All"
    assert resource_app_id("graph") == UUID("00000003-0000-0000-c000-000000000000")
    for name, kind in [("Invented.Permission", "application"), ("User.Read", "application")]:
        with pytest.raises(ValueError):
            permission_id(name, kind)
    with pytest.raises(ValueError):
        permission_name(application, "delegated")
    with pytest.raises(ValueError):
        permission_name(UUID(int=0), "application")
    with pytest.raises(ValueError):
        resource_app_id("invented")


def test_reference_provenance():
    from mockazure.security.catalog import permission_catalog

    data = permission_catalog()
    assert data["repository"] == "https://github.com/microsoftgraph/microsoft-graph-docs-contrib"
    assert len(data["revision"]) == 40
    assert len(data["sha256"]) == 64
    for version in ("v1.0", "beta"):
        assert "conditionalAccessPolicy" in schema(version)["types"]
        assert len(schema(version)["sha256"]) == 64


def test_named_location_accepts_only_derived_ip_range_types():
    valid = {
        "isTrusted": True,
        "ipRanges": [
            {"@odata.type": "#microsoft.graph.iPv4CidrRange", "cidrAddress": "10.0.0.0/8"},
            {"@odata.type": "#microsoft.graph.iPv6CidrRange", "cidrAddress": "2001:db8::/32"},
        ],
    }
    validate_payload("v1.0", "ipNamedLocation", valid, partial=True)
    with pytest.raises(ValueError):
        validate_payload(
            "v1.0",
            "ipNamedLocation",
            {
                "ipRanges": [
                    {
                        "@odata.type": "#microsoft.graph.application",
                        "displayName": "not an IP range",
                    },
                ]
            },
            partial=True,
        )
