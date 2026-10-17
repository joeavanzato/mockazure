import json
from uuid import UUID

import pytest
from kiota_serialization_json.json_serialization_writer import JsonSerializationWriter
from msgraph.generated.models.app_role_assignment import AppRoleAssignment
from msgraph.generated.models.application import Application
from msgraph.generated.models.conditional_access_applications import ConditionalAccessApplications
from msgraph.generated.models.conditional_access_client_app import ConditionalAccessClientApp
from msgraph.generated.models.conditional_access_condition_set import ConditionalAccessConditionSet
from msgraph.generated.models.conditional_access_grant_control import ConditionalAccessGrantControl
from msgraph.generated.models.conditional_access_grant_controls import (
    ConditionalAccessGrantControls,
)
from msgraph.generated.models.conditional_access_policy import ConditionalAccessPolicy
from msgraph.generated.models.conditional_access_policy_state import ConditionalAccessPolicyState
from msgraph.generated.models.conditional_access_users import ConditionalAccessUsers
from msgraph.generated.models.device import Device
from msgraph.generated.models.o_auth2_permission_grant import OAuth2PermissionGrant
from msgraph.generated.models.service_principal import ServicePrincipal
from msgraph.generated.models.unified_role_assignment import UnifiedRoleAssignment

from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_licensing import seed_sku, set_role


def wire(model):
    writer = JsonSerializationWriter()
    writer.write_object_value(None, model)
    return json.loads(writer.get_serialized_content())


@pytest.mark.parametrize(
    "kind",
    [
        "application",
        "servicePrincipal",
        "device",
        "conditionalAccessPolicy",
        "unifiedRoleAssignment",
        "oAuth2PermissionGrant",
        "appRoleAssignment",
    ],
)
async def test_generated_security_model_discriminators(database, settings, kind):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        await set_role(database, tenant, user, "Global Administrator")
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {
                "Application.ReadWrite.All",
                "Device.ReadWrite.All",
                "Directory.AccessAsUser.All",
                "Directory.Read.All",
                "AppRoleAssignment.ReadWrite.All",
                "DelegatedPermissionGrant.ReadWrite.All",
                "RoleManagement.ReadWrite.Directory",
                "Policy.Read.All",
                "Policy.ReadWrite.ConditionalAccess",
            },
            "delegated",
        )
        if kind == "application":
            path, model = "/applications", Application(display_name="SDK application")
        elif kind == "device":
            path, model = "/devices", Device(display_name="SDK device", account_enabled=True)
        elif kind == "servicePrincipal":
            created = await client.post(
                "/v1.0/applications", headers=headers, json={"displayName": "SDK SP parent"}
            )
            assert created.status_code == 201
            path, model = "/servicePrincipals", ServicePrincipal(app_id=created.json()["appId"])
        elif kind == "conditionalAccessPolicy":
            path = "/identity/conditionalAccess/policies"
            model = ConditionalAccessPolicy(
                display_name="SDK policy",
                state=ConditionalAccessPolicyState.Disabled,
                conditions=ConditionalAccessConditionSet(
                    applications=ConditionalAccessApplications(include_applications=["All"]),
                    users=ConditionalAccessUsers(include_users=["All"]),
                    client_app_types=[ConditionalAccessClientApp.All],
                ),
                grant_controls=ConditionalAccessGrantControls(
                    operator="OR", built_in_controls=[ConditionalAccessGrantControl.Mfa]
                ),
            )
        elif kind == "unifiedRoleAssignment":
            path = "/roleManagement/directory/roleAssignments"
            model = UnifiedRoleAssignment(
                principal_id=str(user),
                role_definition_id="f2ef992c-3afb-46b9-b7cf-a126ee74c451",
                directory_scope_id="/",
            )
        else:
            resource = (
                await client.get(
                    "/v1.0/servicePrincipals(appId='00000003-0000-0000-c000-000000000000')",
                    headers=headers,
                )
            ).json()
            principal = (
                await client.get(f"/v1.0/servicePrincipals(appId='{app}')", headers=headers)
            ).json()
            if kind == "oAuth2PermissionGrant":
                path = "/oauth2PermissionGrants"
                model = OAuth2PermissionGrant(
                    client_id=principal["id"],
                    resource_id=resource["id"],
                    consent_type="AllPrincipals",
                    scope="User.Read",
                )
            else:
                path = "/servicePrincipals/" + principal["id"] + "/appRoleAssignments"
                role = next(
                    value["id"]
                    for value in resource["appRoles"]
                    if value["value"] == "User.Read.All"
                )
                model = AppRoleAssignment(
                    principal_id=UUID(principal["id"]),
                    resource_id=UUID(resource["id"]),
                    app_role_id=UUID(role),
                )
        payload = wire(model)
        assert payload.get("@odata.type", "#microsoft.graph." + kind) == "#microsoft.graph." + kind
        result = await client.post("/v1.0" + path, headers=headers, json=payload)
        assert result.status_code == 201, result.text
        wrong = dict(payload, **{"@odata.type": "#microsoft.graph.user"})
        assert (await client.post("/v1.0" + path, headers=headers, json=wrong)).status_code == 400
