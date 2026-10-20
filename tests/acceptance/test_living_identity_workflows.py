import json
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "case",
    [
        "joiner_mover_leaver",
        "guest_invite_accept_collect",
        "delete_restore_collect",
        "credential_rotation",
        "pim_api_access",
        "azure_rbac_removal",
    ],
)
def test_live_identity_workflows(case):
    from tools.living_identity import validate

    value = json.loads(Path("work/living-identity.json").read_text("utf-8"))
    validate(value)
    assert value["cases"][case]["state"] == "passed"
    assert value["failed_cases"] == value["unexpected_authorizations"] == []
