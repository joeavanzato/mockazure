import pytest

from mockazure.authorization.catalog import permission


@pytest.mark.parametrize(
    "name",
    [
        "AppRoleAssignment.ReadWrite.All",
        "DelegatedPermissionGrant.ReadWrite.All",
        "RoleManagement.Read.Directory",
        "RoleManagement.ReadWrite.Directory",
        "UserAuthenticationMethod.Read.All",
        "UserAuthenticationMethod.ReadWrite.All",
        "Policy.Read.All",
        "Policy.ReadWrite.ConditionalAccess",
        "Device.Read.All",
    ],
)
@pytest.mark.parametrize("kind", ["application", "delegated"])
def test_security_permissions_are_accepted_by_oauth_catalog(name, kind):
    from mockazure.security.catalog import permission_id

    assert permission("graph", kind, name).name == name
    assert permission_id(name, kind)


def test_application_only_security_permissions_are_not_delegated_scopes():
    for name in ["Application.ReadWrite.OwnedBy", "Device.ReadWrite.All"]:
        assert permission("graph", "application", name).admin_consent_required
        with pytest.raises(ValueError):
            permission("graph", "delegated", name)
