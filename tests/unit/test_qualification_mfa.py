import httpx
import pytest

from integrations.python.mfa_form import mfa_form_values
from tools.qualification_mfa import ensure_mobile_method


def test_selects_only_the_offered_method_and_preserves_skip():
    page = '<select name="method_id"><option value="registered&amp;1">SMS</option></select>'
    assert mfa_form_values(page, {"decision": "complete"}) == {
        "decision": "complete",
        "method_id": "registered&1",
    }
    assert mfa_form_values(page, {"decision": "skip"}) == {"decision": "skip"}
    assert mfa_form_values("legacy form", {"decision": "complete"}) == {"decision": "complete"}
    assert mfa_form_values('<select name="method_id"></select>', {"decision": "complete"}) == {
        "decision": "complete"
    }


def test_fixture_enrollment_is_idempotent_and_does_not_change_existing_methods():
    rows = [{"@odata.type": "#microsoft.graph.phoneAuthenticationMethod", "phoneType": "office"}]
    writes = []

    def respond(request):
        assert request.url.path == "/_mock/tenants/tenant/security/users/user/authenticationMethods"
        if request.method == "GET":
            return httpx.Response(200, json={"value": rows})
        import json

        body = json.loads(request.content)
        rows.append(body)
        writes.append(body)
        return httpx.Response(201, json={"id": "registered"})

    with httpx.Client(
        base_url="https://local.test", transport=httpx.MockTransport(respond)
    ) as client:
        ensure_mobile_method(client, "tenant", "user")
        ensure_mobile_method(client, "tenant", "user")
    assert len(writes) == 1
    assert writes[0]["phoneType"] == "mobile"
    assert rows[0]["phoneType"] == "office"


def test_fixture_enrollment_propagates_authorization_failure():
    with (
        httpx.Client(
            base_url="https://local.test",
            transport=httpx.MockTransport(lambda request: httpx.Response(403)),
        ) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        ensure_mobile_method(client, "tenant", "user")


def test_identity_fixture_direct_script_can_load_shared_helper(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "tools/prepare_identity.py"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import runpy; runpy.run_path(" + repr(str(script)) + "); "
            "from tools.qualification_mfa import ensure_mobile_method",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
