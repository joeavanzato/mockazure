"""Actual MSAL, generated Graph v1.0 SDK and Azure Identity against local endpoints."""

import argparse
import asyncio
import json
import os
import ssl
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from azure.identity import ManagedIdentityCredential
from graph_directory import acquire, graph_client
from kiota_abstractions.api_error import APIError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.audit_logs.directory_audits.directory_audits_request_builder import (
    DirectoryAuditsRequestBuilder,
)
from msgraph.generated.audit_logs.sign_ins.sign_ins_request_builder import SignInsRequestBuilder
from msgraph.generated.identity_protection.risky_users.confirm_compromised.confirm_compromised_post_request_body import (  # noqa: E501
    ConfirmCompromisedPostRequestBody,
)
from msgraph.generated.identity_protection.risky_users.dismiss.dismiss_post_request_body import (
    DismissPostRequestBody,
)
from msgraph.generated.models.assigned_license import AssignedLicense
from msgraph.generated.models.user import User
from msgraph.generated.users.item.assign_license.assign_license_post_request_body import (
    AssignLicensePostRequestBody,
)


async def read_all(builder, config=None):
    page = await builder.get(request_configuration=config)
    rows = list(page.value or [])
    pages = 1
    while page.odata_next_link:
        assert pages < 200, "SDK paging exceeded the qualification bound"
        page = await builder.with_url(page.odata_next_link).get()
        rows.extend(page.value or [])
        pages += 1
    return rows


def local_endpoint(value, *, http=False):
    parts = urlsplit(value)
    assert parts.scheme == ("http" if http else "https")
    assert parts.hostname in {"localhost", "127.0.0.1"} or parts.hostname.endswith(
        ".mockazure.test"
    )
    assert not parts.username and not parts.password and not parts.query and not parts.fragment


async def run(config):
    for endpoint in config["endpoints"].values():
        local_endpoint(endpoint)
    tokens = await asyncio.to_thread(acquire, config)
    output = {"passed": False, "client": "msgraph-sdk 1.64.0 / MSAL 1.39.0", "modes": {}}
    context = ssl.create_default_context(cafile=config["ca_file"])
    async with httpx.AsyncClient(verify=context, trust_env=False, timeout=60) as http:
        for mode in ("application", "delegated"):
            client = graph_client(config, tokens[mode], http)
            signins = await read_all(
                client.audit_logs.sign_ins,
                RequestConfiguration(
                    query_parameters=SignInsRequestBuilder.SignInsRequestBuilderGetQueryParameters(
                        filter="createdDateTime ge 2026-10-07T00:00:00Z and "
                        "createdDateTime lt 2026-10-08T00:00:00Z",
                        top=333,
                    )
                ),
            )
            audits = await read_all(
                client.audit_logs.directory_audits,
                RequestConfiguration(
                    query_parameters=DirectoryAuditsRequestBuilder.DirectoryAuditsRequestBuilderGetQueryParameters(
                        filter="activityDateTime ge 2026-10-07T00:00:00Z and "
                        "activityDateTime lt 2026-10-08T00:00:00Z",
                        top=333,
                    )
                ),
            )
            assert sorted(r.id for r in signins) == config["evidence_ids"]["signIn"]
            assert sorted(r.id for r in audits) == config["evidence_ids"]["directoryAudit"]
            risks = await read_all(client.identity_protection.risk_detections)
            users = await read_all(client.identity_protection.risky_users)
            assert risks and any(r.id == config["user_id"] for r in users)
            await client.identity_protection.risky_users.confirm_compromised.post(
                ConfirmCompromisedPostRequestBody(user_ids=[config["user_id"]])
            )
            risky = await client.identity_protection.risky_users.by_risky_user_id(
                config["user_id"]
            ).get()
            assert risky.risk_state.value == "confirmedCompromised"
            await client.identity_protection.risky_users.dismiss.post(
                DismissPostRequestBody(user_ids=[config["user_id"]])
            )
            risky = await client.identity_protection.risky_users.by_risky_user_id(
                config["user_id"]
            ).get()
            assert risky.risk_state.value == "dismissed"
            target = client.users.by_user_id(config["seed_first_id"])
            await target.patch(User(usage_location="US"))
            assigned = await target.assign_license.post(
                AssignLicensePostRequestBody(
                    add_licenses=[
                        AssignedLicense(sku_id=UUID(config["sku_id"]), disabled_plans=[])
                    ],
                    remove_licenses=[],
                )
            )
            assert any(str(item.sku_id) == config["sku_id"] for item in assigned.assigned_licenses)
            details_denied = False
            try:
                details = await target.license_details.get()
                assert mode == "delegated" and details.value
            except APIError as error:
                assert mode == "application" and error.response_status_code == 403
                details_denied = True
            await target.assign_license.post(
                AssignLicensePostRequestBody(
                    add_licenses=[], remove_licenses=[UUID(config["sku_id"])]
                )
            )
            denied = graph_client(config, tokens["denied_" + mode], http)
            # Delegated users may read their own sign-ins; audit reads still require scope.
            try:
                await denied.audit_logs.directory_audits.get()
            except APIError as error:
                assert error.response_status_code == 403
            else:
                raise AssertionError("Missing audit permission accepted")
            output["modes"][mode] = {
                "signIn_count": len(signins),
                "directoryAudit_count": len(audits),
                "exact_ids": True,
                "risk_reads": True,
                "confirm": True,
                "dismiss": True,
                "license_add_remove": True,
                "license_details": not details_denied,
                "license_details_app_denied": details_denied,
                "permission_denied": True,
            }
        local_endpoint(config["managed_identity_endpoint"], http=True)
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
        os.environ["IDENTITY_ENDPOINT"] = config["managed_identity_endpoint"]
        os.environ["IDENTITY_HEADER"] = (
            Path(config["managed_identity_header_file"]).read_text().strip()
        )
        os.environ["NO_PROXY"] = "*"
        with ManagedIdentityCredential() as credential:
            token = await asyncio.to_thread(
                credential.get_token, "https://graph.microsoft.com/.default"
            )
        managed = graph_client(config, token.token, http)
        organization = await managed.organization.get()
        assert [row.id for row in organization.value] == [config["tenant_id"]]
        # Beta exposes managed identity events; it is kept separate from generated v1.0 calls.
        for attempt in range(120):
            response = await http.get(
                config["endpoints"]["graph"] + "/beta/auditLogs/signIns",
                params={
                    "$filter": "signInEventTypes/any(t: t eq 'managedIdentity') and appId eq '"
                    + config["managed_identity_client_id"]
                    + "'"
                },
                headers={"Authorization": "Bearer " + tokens["application"]},
            )
            assert response.status_code == 200
            if response.json()["value"]:
                break
            await asyncio.sleep(0.5)
        else:
            raise AssertionError("Managed identity sign-in was not published")
        output["managed_identity"] = {
            "passed": True,
            "organization_ids": [row.id for row in organization.value],
            "event_count": len(response.json()["value"]),
        }
    output["passed"] = True
    Path(config["output_file"]).write_text(json.dumps(output, indent=2))
    print(json.dumps({"passed": True, "client": output["client"], "modes": list(output["modes"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("/state/client.json"))
    args = parser.parse_args()
    asyncio.run(run(json.loads(args.config.read_text())))
