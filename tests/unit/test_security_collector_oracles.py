import pytest

from tools.qualify_security_collectors import verify_denied_rows


def test_denied_mfa_placeholder_cannot_hide_disclosed_method_data():
    row = {
        "user": "local@graph.test",
        "MFAstatus": "Disabled",
        **dict.fromkeys(
            [
                "email",
                "fido2",
                "app",
                "password",
                "phone",
                "softwareoath",
                "hellobusiness",
                "temporaryAccessPass",
                "certificateBasedAuthConfiguration",
                "qrcodepin",
            ],
            "False",
        ),
    }
    verify_denied_rows("mes_methods", "delegated", [row], "local@graph.test")
    for changed in (
        {**row, "phone": "True"},
        {**row, "phoneNumber": "+1 5555550150"},
        {**row, "user": "other@graph.test"},
    ):
        with pytest.raises(AssertionError):
            verify_denied_rows("mes_methods", "delegated", [changed], "local@graph.test")
    with pytest.raises(AssertionError):
        verify_denied_rows("hawk_credentials", "delegated", [row], "local@graph.test")
