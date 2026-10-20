import httpx
import pytest

from tools.living_evidence import checked, continuation


def test_local_continuation_accepts_paths_and_queries_but_never_another_origin():
    endpoint = "https://localhost:28461"
    parsed = continuation(endpoint + "/v1.0/users?$skiptoken=opaque", endpoint)
    assert parsed.path == "/v1.0/users" and parsed.query == "$skiptoken=opaque"
    for url in [
        "https://graph.microsoft.com/v1.0/users",
        "https://localhost:8443/v1.0/users",
        "https://other.internal/v1.0/users",
        "https://username@localhost:28461/v1.0/users",
        endpoint + "/v1.0/users#fragment",
        "/v1.0/users",
    ]:
        with pytest.raises(ValueError):
            continuation(url, endpoint)


def test_expected_storage_xml_error_keeps_status_check_without_json_parsing():
    response = httpx.Response(
        403,
        content=b"<Error><Code>AuthorizationPermissionMismatch</Code></Error>",
        request=httpx.Request("GET", "https://localhost:28462/account/container/blob"),
    )
    assert checked(response, 403) is None
    with pytest.raises(AssertionError, match="403, expected 200"):
        checked(response)
