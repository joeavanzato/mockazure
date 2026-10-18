"""Real MSAL authorization-code acquisition for the local Exchange audience."""

import argparse
import html
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import msal
import requests

try:
    from .mfa_form import mfa_form_values
except ImportError:  # Direct script execution in the isolated client container.
    from mfa_form import mfa_form_values


def acquire(config):
    with requests.Session() as transport:
        transport.verify, transport.trust_env = config["ca_file"], False
        client = msal.PublicClientApplication(
            config["exchange_public_client_id"],
            authority=config["endpoints"]["login"] + "/" + config["tenant_id"],
            instance_discovery=False,
            validate_authority=False,
            http_client=transport,
        )
        flow = client.initiate_auth_code_flow(
            ["https://outlook.office365.com/Exchange.Manage"],
            redirect_uri="http://localhost:8123/callback",
        )
        response = transport.get(flow["auth_uri"], timeout=15)
        response.raise_for_status()
        fields = {
            name: html.unescape(
                re.search(r'name="' + name + r'" value="([^"]*)"', response.text)[1]
            )
            for name in ("transaction", "csrf")
        }
        user = json.loads(Path(config["user_file"]).read_text())
        for path, values in (
            ("/login", user),
            ("/consent", {"decision": "approve"}),
            ("/mfa", {"decision": "complete"}),
        ):
            if path == "/mfa":
                values = mfa_form_values(response.text, values)
            response = transport.post(
                config["endpoints"]["login"] + path,
                data=fields | values,
                allow_redirects=False,
                timeout=15,
            )
            assert response.status_code in (200, 303), "Local Exchange authorization failed"
        callback = dict(parse_qsl(urlsplit(response.headers["location"]).query))
        token = client.acquire_token_by_auth_code_flow(flow, callback)
        assert "access_token" in token, token.get("error")
        (Path(config["token_output_dir"]) / "exchange-delegated-token.json").write_text(
            json.dumps({"access_token": token["access_token"], "flow": "AuthorizationCode"})
        )
    print("Local Exchange delegated MSAL token acquired")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    acquire(json.loads(parser.parse_args().config.read_text()))
