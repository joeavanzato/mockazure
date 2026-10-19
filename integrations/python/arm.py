"""Unmodified Azure management SDKs over authenticated local HTTPS."""

import argparse
import html
import json
import re
import time
from importlib.metadata import version
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

import msal
import requests
from azure.core.credentials import AccessToken
from azure.core.exceptions import HttpResponseError
from azure.core.pipeline.transport import RequestsTransport
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.monitor import MonitorManagementClient
from azure.mgmt.resource import ResourceManagementClient, SubscriptionClient

try:
    from .mfa_form import mfa_form_values
except ImportError:  # Direct script execution in the isolated client container.
    from mfa_form import mfa_form_values


class LocalSession(requests.Session):
    def __init__(self, origin, ca_file):
        super().__init__()
        parsed = urlsplit(origin)
        assert parsed.scheme == "https" and not parsed.username and not parsed.password
        assert parsed.hostname in {"localhost", "127.0.0.1"} or parsed.hostname.endswith(
            ".mockazure.test"
        )
        self.origin = origin.rstrip("/")
        self.verify, self.trust_env = ca_file, False

    def send(self, request, **kwargs):
        # requests follows redirects by sending prepared requests directly.
        # Recheck here as well as at the high-level request entry point.
        parsed = urlsplit(request.url)
        if parsed.scheme + "://" + parsed.netloc != self.origin or parsed.fragment:
            raise ValueError("Azure client request escaped the configured local origin")
        return super().send(request, **kwargs)

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        if parsed.scheme + "://" + parsed.netloc != self.origin or parsed.fragment:
            raise ValueError("Azure client request escaped the configured local origin")
        return super().request(method, url, **kwargs)


def acquire(config):
    result = {}
    with LocalSession(config["endpoints"]["login"], config["ca_file"]) as session:
        options = dict(
            authority=config["endpoints"]["login"] + "/" + config["tenant_id"],
            instance_discovery=False,
            validate_authority=False,
            http_client=session,
        )
        for label, identifier, secret_file in (
            ("application", config["client_id"], config["secret_file"]),
            ("denied_application", config["negative_client_id"], config["negative_secret_file"]),
        ):
            app = msal.ConfidentialClientApplication(
                identifier,
                client_credential=json.loads(Path(secret_file).read_text(encoding="utf-8"))[
                    "secret"
                ],
                **options,
            )
            tokens = app.acquire_token_for_client(["https://management.azure.com/.default"])
            assert "access_token" in tokens, tokens.get("error")
            result[label] = tokens["access_token"]
        for label, user_file in (
            ("delegated", config["user_file"]),
            ("denied_delegated", config["negative_user_file"]),
        ):
            session.cookies.clear()
            app = msal.PublicClientApplication(config["public_client_id"], **options)
            flow = app.initiate_auth_code_flow(
                ["https://management.azure.com/user_impersonation"],
                redirect_uri="http://localhost:8123/callback",
            )
            response = session.get(flow["auth_uri"], timeout=15)
            response.raise_for_status()
            fields = {
                name: html.unescape(
                    re.search(r'name="' + name + r'" value="([^"]*)"', response.text)[1]
                )
                for name in ("transaction", "csrf")
            }
            user = json.loads(Path(user_file).read_text(encoding="utf-8"))
            for path, values in (
                ("/login", user),
                ("/consent", {"decision": "approve"}),
                ("/mfa", {"decision": "complete"}),
            ):
                if path == "/mfa":
                    values = mfa_form_values(response.text, values)
                response = session.post(
                    config["endpoints"]["login"] + path,
                    data=fields | values,
                    allow_redirects=False,
                    timeout=15,
                )
                assert response.status_code in (200, 303), "Local ARM browser approval failed"
            callback = dict(parse_qsl(urlsplit(response.headers["location"]).query))
            tokens = app.acquire_token_by_auth_code_flow(flow, callback)
            assert "access_token" in tokens, tokens.get("error")
            result[label] = tokens["access_token"]
    for label, raw in result.items():
        (Path(config["token_output_dir"]) / (label + "-token.json")).write_text(
            json.dumps({"access_token": raw}), encoding="utf-8"
        )
    return result


class Credential:
    def __init__(self, raw):
        self.raw = raw

    def get_token(self, *args, **kwargs):
        return AccessToken(self.raw, int(time.time()) + 3600)


def workflow(config, token, negative, mode):
    checks = {}
    sub, origin = config["subscription_id"], config["endpoints"]["arm"]
    with LocalSession(origin, config["ca_file"]) as session:
        transport = RequestsTransport(
            session=session,
            session_owner=False,
            connection_verify=config["ca_file"],
            use_env_settings=False,
        )
        options = {
            "base_url": origin,
            "transport": transport,
            "credential_scopes": ["https://management.azure.com/.default"],
            "connection_verify": config["ca_file"],
            "polling_interval": 0.2,
        }
        credential = Credential(token)
        resources = ResourceManagementClient(credential, sub, api_version="2021-04-01", **options)
        discovery = SubscriptionClient(credential, api_version="2021-01-01", **options)
        roles = AuthorizationManagementClient(credential, sub, api_version="2022-04-01", **options)
        monitor = MonitorManagementClient(credential, sub, api_version="2015-04-01", **options)
        assert {s.subscription_id for s in discovery.subscriptions.list()} == {sub}
        assert discovery.subscriptions.get(sub).tenant_id == config["tenant_id"]
        assert config["tenant_id"] in {t.tenant_id for t in discovery.tenants.list()}
        checks["discovery"] = True
        inventory = list(resources.resources.list())
        assert {r.id for r in inventory} == set(config["resource_ids"])
        assert (
            len(
                list(
                    resources.resources.list(
                        filter="tagName eq 'Purpose' and tagValue eq 'Evidence'"
                    )
                )
            )
            == 103
        )
        checks["inventory"] = True
        group = "sdk-" + mode + "-" + uuid4().hex[:8]
        created = resources.resource_groups.create_or_update(
            group, {"location": "eastus", "tags": {"Created": mode}}
        )
        assert created.name == group
        resources.resource_groups.update(group, {"tags": {"Updated": mode}})
        assert resources.resource_groups.get(group).tags == {"Updated": mode}
        scope = f"/subscriptions/{sub}/resourceGroups/{group}"
        resources.tags.create_or_update_at_scope(
            scope, {"properties": {"tags": {"Evidence": mode}}}
        )
        resources.tags.update_at_scope(
            scope, {"operation": "Merge", "properties": {"tags": {"More": "yes"}}}
        )
        assert resources.tags.get_at_scope(scope).properties.tags == {
            "Evidence": mode,
            "More": "yes",
        }
        resources.tags.delete_at_scope(scope)
        assert resources.tags.get_at_scope(scope).properties.tags == {}
        checks["tag_crud"] = True
        definitions = list(roles.role_definitions.list(f"/subscriptions/{sub}"))
        assert {r.role_name for r in definitions} == {
            "Contributor",
            "Key Vault Administrator",
            "Key Vault Contributor",
            "Key Vault Reader",
            "Key Vault Secrets Officer",
            "Key Vault Secrets User",
            "Owner",
            "Reader",
            "Storage Blob Data Contributor",
            "Storage Blob Data Owner",
            "Storage Blob Data Reader",
            "Storage File Data Privileged Contributor",
            "Storage File Data Privileged Reader",
            "Storage Queue Data Contributor",
            "Storage Queue Data Message Processor",
            "Storage Queue Data Message Sender",
            "Storage Queue Data Reader",
            "Storage Table Data Contributor",
            "Storage Table Data Reader",
            "User Access Administrator",
        }
        checks["role_definitions"] = True
        reader = next(r for r in definitions if r.role_name == "Reader")
        identifier = str(uuid4())
        assignment = roles.role_assignments.create(
            scope,
            identifier,
            {
                "role_definition_id": reader.id,
                "principal_id": config["negative_user_id"],
                "principal_type": "User",
            },
        )
        assert assignment.principal_id == config["negative_user_id"]
        assert identifier in {r.name for r in roles.role_assignments.list_for_scope(scope)}
        roles.role_assignments.delete(scope, identifier)
        checks["role_assignments"] = True
        resources.resource_groups.begin_delete(group).result(timeout=60)
        try:
            resources.resource_groups.get(group)
        except HttpResponseError as error:
            assert error.status_code == 404
        else:
            raise AssertionError("Deleted resource group remains")
        checks["group_crud"] = True
        fixture = config["arm_fixture"]
        query = f"eventTimestamp ge '{fixture['start']}' and eventTimestamp le '{fixture['end']}'"
        wire = []

        def capture(response):
            payload = json.loads(response.http_response.text())
            wire.extend(payload.get("value", []))

        events = list(monitor.activity_logs.list(filter=query, raw_response_hook=capture))
        assert len(events) == 1003 and {r.event_data_id for r in events} == set(
            fixture["record_ids"]
        )
        # Generated Python models use datetime (microsecond precision). Verify exact strings
        # independently at the SDK's public raw-response hook, without replacing deserialization.
        assert len(wire) == 1003
        expected = json.loads(
            (Path(config["token_output_dir"]) / "arm-expected.json").read_text(encoding="utf-8")
        )
        assert {r["eventDataId"]: r for r in wire} == {r["eventDataId"]: r for r in expected}
        checks["activity_paging"] = True
        selected = list(
            monitor.activity_logs.list(
                filter=query + " and resourceGroupName eq 'evidence'",
                select="eventDataId,id,eventTimestamp",
            )
        )
        assert len(selected) == 1003
        assert (
            len(
                list(
                    monitor.activity_logs.list(
                        filter="eventTimestamp ge '2026-10-07T00:00:00.1234568Z' "
                        "and eventTimestamp le '2026-10-07T23:59:59Z'"
                    )
                )
            )
            == 1
        )
        checks["activity_filters"] = True
        denied = MonitorManagementClient(
            Credential(negative), sub, api_version="2015-04-01", **options
        )
        try:
            list(denied.activity_logs.list(filter=query))
        except HttpResponseError as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Denied Activity Logs were disclosed")
        checks["permission_denied"] = True
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/state/client.json"))
    parser.add_argument("--tokens-only", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    report = {
        "passed": False,
        "modes": {},
        "versions": {
            name: version(name)
            for name in (
                "msal",
                "azure-mgmt-resource",
                "azure-mgmt-authorization",
                "azure-mgmt-monitor",
            )
        },
    }
    try:
        tokens = acquire(config)
        if args.tokens_only:
            return
        for mode in ("application", "delegated"):
            report["modes"][mode] = workflow(config, tokens[mode], tokens["denied_" + mode], mode)
        report["passed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        if not args.tokens_only:
            Path(config["output_file"]).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
