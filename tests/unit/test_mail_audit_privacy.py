from uuid import uuid4

import pytest

from mockazure.mail.audit_schemas import validate_audit_record


@pytest.mark.parametrize(
    "data",
    [
        {"Authorization": "Bearer private"},
        {"secret": "private"},
        {"Parameters": [{"Name": "ClientSecret", "Value": "private"}]},
        {"ModifiedProperties": [{"Name": "password", "NewValue": "private"}]},
    ],
)
def test_ual_rejects_credential_fields_and_name_value_pairs(data):
    with pytest.raises(ValueError, match="Credential"):
        validate_audit_record({"id": str(uuid4()), "auditData": {"dynamicProperties": data}})
