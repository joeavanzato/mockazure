"""Pinned original generated SDK: policy configuration and extended CA wire contracts."""

import asyncio
import json
import ssl
from importlib.metadata import version
from pathlib import Path

import httpx
from graph_directory import graph_client
from graph_security import client_token
from method_management import delegated
from msgraph.generated.models.authentication_method_state import AuthenticationMethodState as State
from msgraph.generated.models.authentication_method_target_type import (
    AuthenticationMethodTargetType as Target,
)
from msgraph.generated.models.conditional_access_applications import (
    ConditionalAccessApplications as Applications,
)
from msgraph.generated.models.conditional_access_client_app import ConditionalAccessClientApp
from msgraph.generated.models.conditional_access_condition_set import (
    ConditionalAccessConditionSet as Conditions,
)
from msgraph.generated.models.conditional_access_device_platform import (
    ConditionalAccessDevicePlatform as Platform,
)
from msgraph.generated.models.conditional_access_grant_control import (
    ConditionalAccessGrantControl as Control,
)
from msgraph.generated.models.conditional_access_grant_controls import (
    ConditionalAccessGrantControls as Grants,
)
from msgraph.generated.models.conditional_access_platforms import (
    ConditionalAccessPlatforms as Platforms,
)
from msgraph.generated.models.conditional_access_policy import ConditionalAccessPolicy as Policy
from msgraph.generated.models.conditional_access_policy_state import (
    ConditionalAccessPolicyState as PolicyState,
)
from msgraph.generated.models.conditional_access_users import ConditionalAccessUsers as Users
from msgraph.generated.models.microsoft_authenticator_authentication_method_configuration import (
    MicrosoftAuthenticatorAuthenticationMethodConfiguration as Authenticator,
)
from msgraph.generated.models.microsoft_authenticator_authentication_method_target import (
    MicrosoftAuthenticatorAuthenticationMethodTarget as AuthTarget,
)
from msgraph.generated.models.microsoft_authenticator_authentication_mode import (
    MicrosoftAuthenticatorAuthenticationMode as Mode,
)
from msgraph.generated.models.sms_authentication_method_configuration import (
    SmsAuthenticationMethodConfiguration as Sms,
)
from msgraph.generated.models.sms_authentication_method_target import (
    SmsAuthenticationMethodTarget as SmsTarget,
)


async def main():
    config = json.loads(Path("/state/client.json").read_text(encoding="utf-8"))
    token = client_token(
        config, config["client_id"], json.loads(Path(config["secret_file"]).read_text())["secret"]
    )
    admin = delegated(config)
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=config["ca_file"]), trust_env=False, timeout=30
    ) as http:
        graph = graph_client(config, token["access_token"], http)
        root = graph.policies.authentication_methods_policy
        document = await root.get()
        assert document.id == "authenticationMethodsPolicy"
        configs = root.authentication_method_configurations
        assert {x.id for x in (await configs.get()).value} == {"Sms", "MicrosoftAuthenticator"}
        sms = configs.by_authentication_method_configuration_id("Sms")
        await sms.patch(Sms(state=State.Disabled))
        assert (await sms.get()).state == State.Disabled
        await sms.patch(
            Sms(
                state=State.Enabled,
                include_targets=[
                    SmsTarget(id="all_users", target_type=Target.Group, is_usable_for_sign_in=False)
                ],
            )
        )
        delegated_graph = graph_client(config, admin, http)
        delegated_root = delegated_graph.policies.authentication_methods_policy
        delegated_configs = delegated_root.authentication_method_configurations
        auth = delegated_configs.by_authentication_method_configuration_id("MicrosoftAuthenticator")
        await auth.patch(
            Authenticator(
                state=State.Enabled,
                include_targets=[
                    AuthTarget(
                        id="all_users", target_type=Target.Group, authentication_mode=Mode.Push
                    )
                ],
            )
        )
        assert (await auth.get()).state == State.Enabled
        policies = graph.identity.conditional_access.policies
        policy = await policies.post(
            Policy(
                display_name="Native extended CA",
                state=PolicyState.EnabledForReportingButNotEnforced,
                conditions=Conditions(
                    client_app_types=[ConditionalAccessClientApp.All],
                    users=Users(include_roles=["0526716b-113d-4c15-b2c8-68e3c22b9f80"]),
                    applications=Applications(include_applications=["All"]),
                    platforms=Platforms(include_platforms=[Platform.Windows]),
                ),
                grant_controls=Grants(
                    operator="OR", built_in_controls=[Control.Mfa, Control.CompliantDevice]
                ),
            )
        )
        item = policies.by_conditional_access_policy_id(policy.id)
        assert (await item.get()).conditions.platforms.include_platforms == [Platform.Windows]
        await item.patch(Policy(state=PolicyState.Disabled))
        await item.delete()
        Path("/state/python-report.json").write_text(
            json.dumps(
                {
                    "client": "msgraph-sdk",
                    "version": version("msgraph-sdk"),
                    "policy_reads": 5,
                    "method_policy_writes": 3,
                    "ca_mutations": 3,
                    "delegated_policy_write": True,
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    asyncio.run(main())
