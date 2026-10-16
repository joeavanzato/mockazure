from uuid import uuid4

from tests.integration.identity_support import identity_client


async def test_exchange_bootstrap_and_command_permissions(database, settings):
    async with identity_client(database, settings) as (client, tenant, graph_client, secret, *_):
        base = f"/_mock/tenants/{tenant}/identity"
        app = (
            await client.post(
                base + "/applications",
                json={
                    "display_name": "Exchange probe",
                    "client_mode": "confidential",
                    "required_permissions": [
                        {
                            "resource": "exchange",
                            "kind": "application",
                            "permissions": ["Exchange.ManageAsApp"],
                        }
                    ],
                },
            )
        ).json()
        credential = (
            await client.post(
                base + f"/applications/{app['client_id']}/credentials", json={"kind": "secret"}
            )
        ).json()
        fields = {
            "grant_type": "client_credentials",
            "client_id": app["client_id"],
            "client_secret": credential["secret"],
            "scope": "https://outlook.office365.com/.default",
        }

        async def token():
            return (await client.post(f"/{tenant}/oauth2/v2.0/token", data=fields)).json()[
                "access_token"
            ]

        endpoint = f"/AdminApi/v1.0/{tenant}/EXOModuleFile?Version=3.11.0"
        headers = {
            "Authorization": "Bearer " + await token(),
            "CommandName": "Get-OrganizationConfig",
        }
        assert (await client.get(endpoint, headers=headers)).status_code == 403
        await client.put(
            base + f"/grants/{uuid4()}",
            json={
                "client_id": app["client_id"],
                "resource": "exchange",
                "kind": "application",
                "permissions": ["Exchange.ManageAsApp"],
            },
        )
        headers["Authorization"] = "Bearer " + await token()
        response = await client.get(endpoint, headers=headers)
        assert response.status_code == 200, response.text
        module = response.json()["value"][0]
        assert module["exportedCmdlets"] == "Get-OrganizationConfig"
        assert "Invoke-RestMethod" in module["fileContent"]
        assert credential["secret"] not in response.text
        assert headers["Authorization"] not in response.text
        command = {"CmdletInput": {"CmdletName": "Get-OrganizationConfig", "Parameters": {}}}
        result = await client.post(
            f"/adminapi/v2.0/{tenant}/InvokeCommand", headers=headers, json=command
        )
        assert result.status_code == 200 and result.json()["value"][0]["Identity"] == tenant
        assert (
            await client.get(endpoint, headers={**headers, "CommandName": "Get-Mailbox"})
        ).status_code == 400
        assert (
            await client.get(endpoint.replace(tenant, str(uuid4())), headers=headers)
        ).status_code == 403
        graph_token = (
            await client.post(
                f"/{tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": graph_client,
                    "client_secret": secret["secret"],
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
        ).json()
        assert (
            await client.get(
                endpoint,
                headers={**headers, "Authorization": "Bearer " + graph_token["access_token"]},
            )
        ).status_code == 401
