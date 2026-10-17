from uuid import uuid4

import pytest

from mockazure.mail.audit_schemas import validate_audit_record


@pytest.mark.parametrize("field,maximum", [("service", 128), ("operation", 256)])
def test_indexed_ual_dimensions_are_bounded(field, maximum):
    with pytest.raises(ValueError, match="bounded"):
        validate_audit_record({"id": str(uuid4()), field: "x" * (maximum + 1)})


def test_audit_organization_uuid_is_case_insensitive():
    tenant = uuid4()
    validate_audit_record(
        {"id": str(uuid4()), "organizationId": str(tenant).upper()}, tenant=tenant
    )
