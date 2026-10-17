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
        command = {"CmdletInput": {"CmdletName": "Get-OrganizationConfig", "Parameters": {}}}
        invoke_url = f"/adminapi/v2.0/{tenant}/InvokeCommand"
        # API consent alone must not grant operational Exchange access.
        assert (await client.get(endpoint, headers=headers)).status_code == 403
        assert (await client.post(invoke_url, headers=headers, json=command)).status_code == 403
        role_url = base + f"/applications/{app['client_id']}/roles"
        assert (await client.put(role_url + "/Directory Readers")).status_code == 200
        assert (await client.get(endpoint, headers=headers)).status_code == 403
        assert (await client.put(role_url + "/Global Reader")).status_code == 200
        assert (await client.put(role_url + "/Global Reader")).status_code == 200
        response = await client.get(endpoint, headers=headers)
        assert response.status_code == 200, response.text
        wildcard = await client.get(endpoint, headers={**headers, "CommandName": "*"})
        assert wildcard.status_code == 200, wildcard.text
        assert wildcard.json()["value"][0]["exportedCmdlets"] == "Get-OrganizationConfig"
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
            await client.get(
                endpoint, headers={**headers, "CommandName": "Get-UnimplementedCommand"}
            )
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

        assert (await client.delete(role_url + "/Global Reader")).status_code == 200
        assert (await client.get(endpoint, headers=headers)).status_code == 403
        assert (await client.post(invoke_url, headers=headers, json=command)).status_code == 403
        other = (
            await client.post(
                "/_mock/tenants",
                json={
                    "slug": "other",
                    "display_name": "Other",
                    "domain": "other.test",
                    "licences": [],
                },
            )
        ).json()["id"]
        assert (
            await client.put(role_url.replace(tenant, other) + "/Global Reader")
        ).status_code == 404
