import pytest

from mockazure.security.audit import validate_security_facts
from tests.unit.test_security_audit_payloads import audit


def test_source_rejects_invalid_differences_before_queueing():
    with pytest.raises(ValueError):
        validate_security_facts(
            {"audit": audit({"displayName": "invented", "oldValue": "null", "newValue": "null"})}
        )
