import pytest
from pydantic import ValidationError

from mockazure.tenants.schemas import TenantCreate


def test_normalizes_display_domain_and_licences(tenant_payload):
    data = TenantCreate(
        **{
            **tenant_payload,
            "display_name": " 東京 ",
            "domain": "CONTOSO.TEST",
            "licences": ["E5", "E5"],
        }
    )
    assert (data.display_name, data.domain, data.licences) == ("東京", "contoso.test", ["E5"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("slug", "-a"),
        ("slug", "A"),
        ("slug", "a" * 64),
        ("domain", "https://tenant.test"),
        ("domain", "a..test"),
        ("domain", "é.test"),
        ("display_name", " "),
        ("display_name", "a" * 201),
        ("licences", [""]),
        ("unknown", True),
    ],
)
def test_rejects_invalid_tenant_fields(tenant_payload, field, value):
    with pytest.raises(ValidationError):
        TenantCreate(**{**tenant_payload, field: value})
