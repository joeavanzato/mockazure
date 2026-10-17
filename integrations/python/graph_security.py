"""Real pinned MSAL and generated Graph SDK security workflows over local HTTPS."""

import argparse
import asyncio
import json
import ssl
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import msal
import requests
from graph_directory import acquire, graph_client
from kiota_abstractions.api_error import APIError
from msgraph.generated.applications.item.add_password.add_password_post_request_body import (
    AddPasswordPostRequestBody,
)
from msgraph.generated.applications.item.remove_password.remove_password_post_request_body import (
    RemovePasswordPostRequestBody,
)
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
from msgraph.generated.models.i_pv4_cidr_range import IPv4CidrRange
from msgraph.generated.models.ip_named_location import IpNamedLocation
from msgraph.generated.models.o_auth2_permission_grant import OAuth2PermissionGrant
from msgraph.generated.models.password_credential import PasswordCredential
from msgraph.generated.models.reference_create import ReferenceCreate
from msgraph.generated.models.required_resource_access import RequiredResourceAccess
from msgraph.generated.models.resource_access import ResourceAccess
from msgraph.generated.models.service_principal import ServicePrincipal
from msgraph.generated.models.unified_role_assignment import UnifiedRoleAssignment

GRAPH = "00000003-0000-0000-c000-000000000000"


def client_token(config, client_id, secret):
    with requests.Session() as transport:
        transport.verify, transport.trust_env = config["ca_file"], False
        app = msal.ConfidentialClientApplication(
            client_id,
            client_credential=secret,
            authority=config["endpoints"]["login"] + "/" + config["tenant_id"],
            instance_discovery=False,
            validate_authority=False,
            http_client=transport,
        )
        return app.acquire_token_for_client(["https://graph.microsoft.com/.default"])


async def expect_error(awaitable, code):
    try:
        await awaitable
    except APIError as error:
        assert error.response_status_code == code, "Unexpected Graph error status"
    else:
        raise AssertionError("Expected Graph denial was not returned")


async def all_rows(builder):
    page = await builder.get()
    values = list(page.value or [])
    while page.odata_next_link:
        page = await builder.with_url(page.odata_next_link).get()
        values.extend(page.value or [])
        assert len(values) < 20000
    return values


async def workflow(config, raw, denied, mode):
    checks = {}
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=config["ca_file"]),
        trust_env=False,
        timeout=60,
    ) as transport:
        graph = graph_client(config, raw, transport)
        resource = next(v for v in await all_rows(graph.service_principals) if v.app_id == GRAPH)
        org_role = next(v for v in resource.app_roles if v.value == "Organization.Read.All")
        user_role = next(v for v in resource.app_roles if v.value == "User.Read.All")
        application = await graph.applications.post(
            Application(
                display_name="Python qualification " + mode,
                required_resource_access=[
                    RequiredResourceAccess(
                        resource_app_id=GRAPH,
                        resource_access=[ResourceAccess(id=org_role.id, type="Role")],
                    )
                ],
            )
        )
        app = graph.applications.by_application_id(application.id)
        assert application.id != application.app_id
        assert (await app.get()).display_name == "Python qualification " + mode
        await app.patch(Application(display_name="Python updated " + mode))
        assert (await app.get()).display_name == "Python updated " + mode
        principal = await graph.service_principals.post(ServicePrincipal(app_id=application.app_id))
        sp = graph.service_principals.by_service_principal_id(principal.id)
        await sp.patch(ServicePrincipal(display_name="Python principal " + mode))
        assert (await sp.get()).display_name == "Python principal " + mode
        await app.owners.ref.post(
            ReferenceCreate(
                odata_id=config["endpoints"]["graph"]
                + "/v1.0/directoryObjects/"
                + config["seed_first_id"],
            )
        )
        assert config["seed_first_id"] in [v.id for v in (await app.owners.get()).value]
        await app.owners.by_directory_object_id(config["seed_first_id"]).ref.delete()
        assert config["seed_first_id"] not in [v.id for v in (await app.owners.get()).value]
        checks["owner_reference"] = True
        org_assignment = await sp.app_role_assignments.post(
            AppRoleAssignment(
                principal_id=UUID(principal.id),
                resource_id=UUID(resource.id),
                app_role_id=org_role.id,
            )
        )
        password = await app.add_password.post(
            AddPasswordPostRequestBody(
                password_credential=PasswordCredential(display_name="Python qualification secret"),
            )
        )
        assert password.secret_text and password.key_id
        first = await asyncio.to_thread(
            client_token, config, application.app_id, password.secret_text
        )
        assert "access_token" in first, first.get("error")
        limited = graph_client(config, first["access_token"], transport)
        assert (await limited.organization.get()).value[0].id == config["tenant_id"]
        await expect_error(limited.users.get(), 403)
        assignment = await sp.app_role_assignments.post(
            AppRoleAssignment(
                principal_id=UUID(principal.id),
                resource_id=UUID(resource.id),
                app_role_id=user_role.id,
            )
        )
        issued = await asyncio.to_thread(
            client_token, config, application.app_id, password.secret_text
        )
        granted = graph_client(config, issued["access_token"], transport)
        assert (await granted.users.get()).value
        await sp.app_role_assignments.by_app_role_assignment_id(assignment.id).delete()
        issued = await asyncio.to_thread(
            client_token, config, application.app_id, password.secret_text
        )
        await expect_error(graph_client(config, issued["access_token"], transport).users.get(), 403)
        checks["application_grant"] = True
        grant = await graph.oauth2_permission_grants.post(
            OAuth2PermissionGrant(
                client_id=principal.id,
                resource_id=resource.id,
                consent_type="AllPrincipals",
                scope="User.Read",
            )
        )
        grant_item = graph.oauth2_permission_grants.by_o_auth2_permission_grant_id(grant.id)
        assert (await grant_item.get()).scope == "User.Read"
        await grant_item.patch(OAuth2PermissionGrant(scope="User.Read Organization.Read.All"))
        assert "Organization.Read.All" in (await grant_item.get()).scope.split()
        await grant_item.delete()
        await expect_error(grant_item.get(), 404)
        checks["delegated_grant"] = True
        role = await graph.role_management.directory.role_assignments.post(
            UnifiedRoleAssignment(
                principal_id=principal.id,
                role_definition_id="f2ef992c-3afb-46b9-b7cf-a126ee74c451",
                directory_scope_id="/",
            )
        )
        role_item = graph.role_management.directory.role_assignments.by_unified_role_assignment_id(
            role.id
        )
        assert (await role_item.get()).principal_id == principal.id
        await role_item.delete()
        await expect_error(role_item.get(), 404)
        checks["role_assignment"] = True
        await app.remove_password.post(RemovePasswordPostRequestBody(key_id=password.key_id))
        revoked = await asyncio.to_thread(
            client_token, config, application.app_id, password.secret_text
        )
        assert "access_token" not in revoked and revoked.get("error") == "invalid_client"
        assert all(value.secret_text is None for value in (await app.get()).password_credentials)
        checks["credential_roundtrip"] = True
        await sp.app_role_assignments.by_app_role_assignment_id(org_assignment.id).delete()
        await sp.delete()
        await expect_error(sp.get(), 404)
        checks["service_principal_crud"] = True
        await app.delete()
        await expect_error(app.get(), 404)
        checks["application_crud"] = True

        devices = await graph.devices.get()
        assert devices.value is not None
        checks["device_read"] = True
        if mode == "application":
            await expect_error(
                graph.devices.post(Device(display_name="Denied device", account_enabled=True)), 403
            )
        else:
            device = await graph.devices.post(
                Device(display_name="Python device", account_enabled=True)
            )
            item = graph.devices.by_device_id(device.id)
            assert (await item.get()).display_name == "Python device"
            await item.patch(Device(display_name="Python updated device"))
            assert (await item.get()).display_name == "Python updated device"
            await item.delete()
            await expect_error(item.get(), 404)
        checks["device_write_profile"] = True
        methods = (
            await graph.users.by_user_id(config["user_id"]).authentication.methods.get()
        ).value
        assert {v.odata_type for v in methods} >= {
            "#microsoft.graph.passwordAuthenticationMethod",
            "#microsoft.graph.phoneAuthenticationMethod",
            "#microsoft.graph.emailAuthenticationMethod",
            "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod",
        }
        checks["method_inventory"] = True
        registrations = graph.reports.authentication_methods.user_registration_details
        registration = await registrations.by_user_registration_details_id(config["user_id"]).get()
        assert registration.id == config["user_id"] and registration.is_mfa_registered is True
        assert (
            registration.is_mfa_capable is False
            and "mobilePhone" in registration.methods_registered
        )
        checks["registration_report"] = True
        user = (
            await graph.users.by_user_id(config["user_id"])
            .with_url(
                config["endpoints"]["graph"]
                + "/v1.0/users/"
                + config["user_id"]
                + "?$select=id,signInActivity"
            )
            .get()
        )
        assert user.id == config["user_id"] and user.sign_in_activity is not None
        checks["signin_activity"] = True
        location = await graph.identity.conditional_access.named_locations.post(
            IpNamedLocation(
                display_name="Python location",
                is_trusted=True,
                ip_ranges=[IPv4CidrRange(cidr_address="192.0.2.0/24")],
            )
        )
        location_item = graph.identity.conditional_access.named_locations.by_named_location_id(
            location.id
        )
        assert (await location_item.get()).display_name == "Python location"
        await location_item.patch(IpNamedLocation(display_name="Python updated location"))
        assert (await location_item.get()).display_name == "Python updated location"
        policy = await graph.identity.conditional_access.policies.post(
            ConditionalAccessPolicy(
                display_name="Python policy",
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
        )
        policy_item = graph.identity.conditional_access.policies.by_conditional_access_policy_id(
            policy.id
        )
        assert (await policy_item.get()).state == ConditionalAccessPolicyState.Disabled
        await policy_item.patch(ConditionalAccessPolicy(display_name="Python updated policy"))
        assert (await policy_item.get()).display_name == "Python updated policy"
        await policy_item.delete()
        await expect_error(policy_item.get(), 404)
        checks["policy_crud"] = True
        await location_item.delete()
        await expect_error(location_item.get(), 404)
        checks["location_crud"] = True
        denied_graph = graph_client(config, denied, transport)
        await expect_error(denied_graph.applications.get(), 403)
        await expect_error(denied_graph.identity.conditional_access.policies.get(), 403)
        checks["permission_denied"] = True
    return checks


async def run(config):
    versions = {name: version(name) for name in ("msal", "msgraph-sdk", "azure-identity")}
    assert versions == {"msal": "1.39.0", "msgraph-sdk": "1.64.0", "azure-identity": "1.26.0"}
    for endpoint in config["endpoints"].values():
        parsed = urlsplit(endpoint)
        assert parsed.scheme == "https" and (
            parsed.hostname in {"localhost", "127.0.0.1"}
            or parsed.hostname.endswith(".mockazure.test")
        )
    tokens = await asyncio.to_thread(acquire, config)
    result = {"passed": False, "client": "Graph Python 1.64.0 / MSAL 1.39.0", "modes": {}}
    for mode in ("application", "delegated"):
        result["modes"][mode] = await workflow(config, tokens[mode], tokens["denied_" + mode], mode)
    result["versions"] = versions
    result["passed"] = True
    Path(config["output_file"]).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": True, "client": result["client"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/state/client.json"))
    asyncio.run(run(json.loads(parser.parse_args().config.read_text())))
