"""Real Azure Identity managed credential against an explicitly configured local host."""

import argparse
import json
import os
import ssl
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from azure.identity import ManagedIdentityCredential

from mockazure.endpoints import local_origin


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    graph = local_origin(config["endpoints"]["graph"])
    endpoint = config["managed_identity_endpoint"]
    parts = urlsplit(endpoint)
    # Only a host-local or explicitly named container service; never IMDS fallback.
    local_origin("https://" + parts.netloc)
    if (
        parts.scheme != "http"
        or parts.path != "/MSI/token"
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
        or parts.hostname == "169.254.169.254"
    ):
        raise ValueError("Managed identity requires an explicit local host endpoint")
    for name in (
        "MSI_ENDPOINT",
        "MSI_SECRET",
        "IDENTITY_SERVER_THUMBPRINT",
        "IMDS_ENDPOINT",
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_FEDERATED_TOKEN_FILE",
    ):
        os.environ.pop(name, None)
    os.environ["IDENTITY_ENDPOINT"] = endpoint
    os.environ["IDENTITY_HEADER"] = Path(config["managed_identity_header_file"]).read_text().strip()
    os.environ["NO_PROXY"] = "*"
    selectors = config.get("identity_selector", {})
    if set(selectors) - {"client_id", "object_id", "resource_id"} or len(selectors) > 1:
        raise ValueError("Exactly zero or one supported identity selector is required")
    kwargs = selectors if "client_id" in selectors else {"identity_config": selectors}
    with ManagedIdentityCredential(**kwargs) as credential:
        token = credential.get_token("https://graph.microsoft.com/.default")
    with httpx.Client(
        verify=ssl.create_default_context(cafile=config["ca_file"]),
        trust_env=False,
        follow_redirects=False,
    ) as client:
        response = client.get(
            graph + "/v1.0/organization", headers={"Authorization": "Bearer " + token.token}
        )
        response.raise_for_status()
        identifiers = [item["id"] for item in response.json()["value"]]
        if identifiers != [config["tenant_id"]]:
            raise RuntimeError("Graph returned unexpected tenant identity")
    report = {
        "client": "Azure Identity ManagedIdentityCredential",
        "authentication": "managedIdentity",
        "organization_ids": identifiers,
        "passed": True,
    }
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
