from mockazure.identity.types import RequiredPermission
from tools.prepare_security import DELEGATED_PERMISSIONS, PERMISSIONS


def test_security_fixture_requests_only_supported_permission_kinds():
    RequiredPermission(resource="graph", kind="application", permissions=PERMISSIONS)
    RequiredPermission(resource="graph", kind="delegated", permissions=DELEGATED_PERMISSIONS)
    assert "Directory.AccessAsUser.All" in DELEGATED_PERMISSIONS
    assert "Device.ReadWrite.All" not in DELEGATED_PERMISSIONS
