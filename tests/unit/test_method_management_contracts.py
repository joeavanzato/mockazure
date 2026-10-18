import pytest

from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.security.password_reset import validate_password


def test_reset_operations_follow_v1_schema():
    validate_payload(
        "v1.0",
        "longRunningOperation",
        {
            "id": "operation",
            "status": "succeeded",
            "statusDetail": "Password reset completed",
            "createdDateTime": "2026-10-17T00:00:00Z",
            "lastActionDateTime": "2026-10-17T00:00:01Z",
            "resourceLocation": "https://localhost:8443/v1.0/users/test/authentication/methods/test",
        },
    )
    validate_payload("v1.0", "passwordResetResponse", {"newPassword": "a-generated-local-password"})


@pytest.mark.parametrize("value", [None, 7, [], "short", "x" * 4097, "nul\x00invalid-long"])
def test_local_password_profile_rejects_invalid_values(value):
    with pytest.raises(GraphError):
        validate_password(value)


def test_password_rejects_unpaired_unicode_without_server_error():
    with pytest.raises(GraphError):
        validate_password(chr(0xD800) * 12)
