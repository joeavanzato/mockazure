import httpx
import pytest

from mockazure.admin.client import AdminClient, AdminClientError
from mockazure.bootstrap import initialize_instance


async def test_redirect_never_forwards_credentials(tmp_path):
    files = initialize_instance(
        tmp_path,
        database_url="postgresql://u:p@localhost/db",
        advertised_host="localhost",
        port=8443,
    )
    seen = []

    def redirect(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://external.invalid/steal"})

    async with AdminClient(
        "https://localhost:8443", ca_file=files.ca_cert_file, token_file=files.admin_token_file
    ) as client:
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            transport=httpx.MockTransport(redirect), base_url="https://localhost:8443"
        )
        with pytest.raises(AdminClientError):
            await client.list_tenants()
    assert seen == ["https://localhost:8443/_mock/tenants?limit=100"]
