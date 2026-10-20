import base64
import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from sqlalchemy import select

from mockazure.evidence.models import EvidenceSource
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.identity_support import certificate
from tests.integration.test_identity_app_only import assertion
from tests.integration.test_security_mutations import create_operational_app
from tests.unit.test_key_rollover_proof import proof


def public_key(cert):
    return {
        "type": "AsymmetricX509Cert",
        "usage": "Verify",
        "key": base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode(),
    }


@pytest.mark.parametrize("kind", ["applications", "servicePrincipals"])
async def test_new_credential_acquires_token_and_removed_one_fails(database, settings, kind):
    async with graph_client(database, settings) as (client, tenant, caller, user):
        headers = await headers_for(
            database, settings, tenant, caller, None, {"Application.ReadWrite.All"}
        )
        app = await create_operational_app(client, tenant, headers)
        if kind == "servicePrincipals":
            row = await client.get(
                f"/v1.0/servicePrincipals(appId='{app['appId']}')", headers=headers
            )
            owner = row.json()["id"]
        else:
            owner = app["id"]
        path = f"/v1.0/{kind}/{owner}"
        old_key, old_cert = certificate()
        new_key, new_cert = certificate()
        body = {
            "keyCredential": public_key(new_cert),
            "passwordCredential": None,
            "proof": proof(new_key, owner, datetime.now(UTC)),
        }
        result = await client.post(path + "/addKey", headers=headers, json=body)
        assert result.status_code == 400, result.text
        bootstrap = await client.patch(
            path, headers=headers, json={"keyCredentials": [public_key(old_cert)]}
        )
        assert bootstrap.status_code == 204, bootstrap.text
        before = (await client.get(path, headers=headers)).json()["keyCredentials"]
        assert (await client.post(path + "/addKey", headers=headers, json=body)).status_code == 400
        body["proof"] = proof(old_key, owner, datetime.now(UTC))
        denied_headers = await headers_for(
            database, settings, tenant, caller, None, {"Organization.Read.All"}
        )
        denied = await client.post(path + "/addKey", headers=denied_headers, json=body)
        assert denied.status_code == 403, denied.text
        result = await client.post(path + "/addKey", headers=headers, json=body)
        assert result.status_code == 200, result.text
        new_id = result.json()["keyId"]
        current = (await client.get(path, headers=headers)).json()["keyCredentials"]
        assert {x["keyId"] for x in current} == {new_id, before[0]["keyId"]}

        async def acquire(key, cert):
            return await client.post(
                f"/{tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": app["appId"],
                    "scope": "https://graph.microsoft.com/.default",
                    "client_assertion_type": (
                        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
                    ),
                    "client_assertion": assertion(key, cert, tenant, app["appId"]),
                },
            )

        new_token = await acquire(new_key, new_cert)
        assert new_token.status_code == 200, new_token.text
        assert new_token.json()["token_type"] == "Bearer"
        # The application's own key action needs no Graph application permission.
        action_headers = (
            {"Authorization": "Bearer " + new_token.json()["access_token"]}
            if kind == "applications"
            else headers
        )
        result = await client.post(
            path + "/removeKey",
            headers=action_headers,
            json={"keyId": before[0]["keyId"], "proof": proof(new_key, owner, datetime.now(UTC))},
        )
        assert result.status_code == 204, result.text
        assert (await acquire(old_key, old_cert)).json()["error"] == "invalid_client"
        assert (await acquire(new_key, new_cert)).status_code == 200
        result = await client.post(
            path + "/removeKey",
            headers=headers,
            json={"keyId": new_id, "proof": proof(old_key, owner, datetime.now(UTC))},
        )
        assert result.status_code == 400, result.text
        async with database.sessions() as session:
            sources = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "security"
                    )
                )
            )
            facts = json.dumps([row.facts for row in sources])
        assert body["proof"] not in facts and public_key(new_cert)["key"] not in facts
        assert new_id in facts and "privateKey" not in facts
