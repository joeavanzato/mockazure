import pytest

from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.schema_reference import validate_payload


def test_authentication_policy_structural_reference():
    validate_payload(
        "v1.0",
        "smsAuthenticationMethodConfiguration",
        {"id": "Sms", "state": "enabled", "excludeTargets": []},
    )
    validate_payload(
        "v1.0",
        "microsoftAuthenticatorAuthenticationMethodTarget",
        {
            "id": "all_users",
            "targetType": "group",
            "authenticationMode": "push",
            "isRegistrationRequired": False,
        },
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "unknownFutureValue"},
        {"featureSettings": {}},
        {"includeTargets": [{"id": "all_users", "targetType": "user"}]},
        {
            "includeTargets": [
                {"id": "all_users", "targetType": "group", "isRegistrationRequired": True}
            ]
        },
        {"id": "Fido2"},
        {"unexpected": True},
    ],
)
def test_configuration_rejects_unsupported_semantics(changes):
    from mockazure.security.method_policies import validate_configuration

    with pytest.raises(GraphError):
        validate_configuration(
            "Sms",
            {"@odata.type": "#microsoft.graph.smsAuthenticationMethodConfiguration", **changes},
        )
