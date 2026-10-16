"""Real MSAL acquisition followed by the generated Graph SDK organization read."""
import asyncio
import json
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import msal
import requests
from azure.core.credentials import AccessToken
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)
from msgraph import GraphServiceClient
from msgraph.graph_request_adapter import GraphRequestAdapter

config = json.loads(Path("/state/client.json").read_text())
secret = json.loads(Path(config["secret_file"]).read_text())["secret"]


async def main():
    with requests.Session() as transport:
        transport.verify = config["ca_file"]
        transport.trust_env = False
        app = msal.ConfidentialClientApplication(
            config["client_id"], client_credential=secret,
            authority=config["endpoints"]["login"]+"/"+config["tenant_id"],
            instance_discovery=False, validate_authority=False, http_client=transport,
        )

        class LocalCredential:
            async def get_token(self, *scopes, **kwargs):
                result = await asyncio.to_thread(app.acquire_token_for_client, scopes=list(scopes))
                if "access_token" not in result:
                    raise RuntimeError("Local token acquisition failed")
                return AccessToken(result["access_token"], int(time.time())+result["expires_in"])

        auth = AzureIdentityAuthenticationProvider(
            LocalCredential(), scopes=["https://graph.microsoft.com/.default"],
            allowed_hosts=[urlsplit(config["endpoints"]["graph"]).hostname],
        )
        async with httpx.AsyncClient(
            verify=ssl.create_default_context(cafile=config["ca_file"]), trust_env=False,
        ) as http:
            adapter = GraphRequestAdapter(auth, client=http)
            adapter.base_url = config["endpoints"]["graph"]+"/v1.0"
            graph = GraphServiceClient(request_adapter=adapter)
            result = await graph.organization.get()
            identifiers = [item.id for item in result.value]
            if identifiers != [config["tenant_id"]]:
                raise RuntimeError("Graph SDK returned unexpected tenant identity")
            report = {"client": "Python MSAL and Graph SDK", "authentication": "client_credentials",
                      "organization_ids": identifiers, "passed": True}
            Path("/state/app-only.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report))


asyncio.run(main())
