"""Cloud-only security configuration, persistence and real audit ingestion effects."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from mockazure.admin.app import create_app
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_evidence_directory import drain
from tests.integration.test_exchange_mail import command, exchange_headers
from tests.integration.test_mail_audit import observations
from tests.integration.test_mailbox_state import provision


async def setup(client, database, settings, tenant, app, user):
    await provision(client, tenant, user)
    assert (
        await client.put(
            f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Exchange Administrator"
        )
    ).status_code == 200
    assert (
        await client.patch(f"/_mock/tenants/{tenant}/evidence/settings", json={"delay_seconds": 0})
    ).status_code == 200
    return await exchange_headers(database, settings, tenant, app)


@pytest.mark.parametrize(
    "kind,create,field,value",
    [
        ("RemoteDomain", {"DomainName": "*.example.test"}, "AutoForwardEnabled", False),
        (
            "InboundConnector",
            {"SenderDomains": ["*.example.test"], "ConnectorType": "Partner"},
            "RequireTls",
            False,
        ),
        (
            "OutboundConnector",
            {"RecipientDomains": ["example.test"], "UseMXRecord": True, "ConnectorType": "Partner"},
            "Enabled",
            False,
        ),
        ("AuthenticationPolicy", {}, "AllowBasicAuthSmtp", True),
        (
            "OrganizationRelationship",
            {"DomainNames": ["example.test"]},
            "FreeBusyAccessEnabled",
            True,
        ),
        ("SharingPolicy", {"Domains": ["*:CalendarSharingFreeBusySimple"]}, "Enabled", False),
    ],
)
async def test_configuration_roundtrip(database, settings, kind, create, field, value):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app, user)
        default = await command(client, tenant, headers, "Get-RemoteDomain", {})
        assert default.status_code == 200, default.text
        assert any(
            v["DomainName"] == "*" and v["Name"] == "Default" for v in default.json()["value"]
        )
        created = await command(
            client, tenant, headers, "New-" + kind, {"Name": "Local test", **create}
        )
        assert created.status_code == 200, created.text
        row = created.json()["value"][0]
        duplicate = await command(
            client, tenant, headers, "New-" + kind, {"Name": "LOCAL TEST", **create}
        )
        assert duplicate.status_code == 409, duplicate.text
        updated = await command(
            client, tenant, headers, "Set-" + kind, {"Identity": row["Guid"], field: value}
        )
        assert updated.status_code == 200, updated.text
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url="https://localhost:8443",
        ) as restarted:
            read = await command(
                restarted, tenant, headers, "Get-" + kind, {"Identity": row["Identity"]}
            )
        assert read.status_code == 200, read.text
        reread = read.json()["value"][0]
        assert reread["Identity"] == row["Identity"] and reread[field] == value
        removed = await command(
            client, tenant, headers, "Remove-" + kind, {"Identity": row["Identity"]}
        )
        assert removed.status_code == 200, removed.text
        assert (
            await command(client, tenant, headers, "Get-" + kind, {"Identity": row["Identity"]})
        ).status_code == 404
        await drain(database)
        audit = [
            v.payload
            for v in await observations(database, tenant)
            if v.payload["operation"].endswith("-" + kind)
        ]
        assert {v["operation"] for v in audit} == {"New-" + kind, "Set-" + kind, "Remove-" + kind}
        assert {v["objectId"] for v in audit} == {row["Identity"]}
        assert all("MailboxOwnerUPN" not in v["auditData"]["dynamicProperties"] for v in audit)


async def test_cloud_parameter_contract(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app, user)
        for name, values in [
            ("Set-AdminAuditLogConfig", {"AdminAuditLogEnabled": False}),
            (
                "Set-AdminAuditLogConfig",
                {"Identity": str(tenant), "UnifiedAuditLogIngestionEnabled": False},
            ),
            ("New-RemoteDomain", {"Name": "Bad", "DomainName": "bad.*.test"}),
            (
                "New-RemoteDomain",
                {
                    "Name": "Bad",
                    "DomainName": "example.test",
                    "DomainController": "dc.example.test",
                },
            ),
            (
                "New-InboundConnector",
                {"Name": "Bad", "SenderDomains": ["*"], "RestrictDomainsToCertificate": True},
            ),
            (
                "New-OutboundConnector",
                {"Name": "Bad", "RecipientDomains": ["example.test"], "UseMXRecord": False},
            ),
            (
                "New-OutboundConnector",
                {
                    "Name": "Bad",
                    "RecipientDomains": ["example.test"],
                    "UseMXRecord": True,
                    "SmartHosts": ["relay.example.test"],
                },
            ),
            ("New-SharingPolicy", {"Name": "Bad", "Domains": ["*:InventedPermission"]}),
        ]:
            result = await command(client, tenant, headers, name, values)
            assert result.status_code == 400, (name, result.text)
        # A valid request proves these aren't passing merely because every command is unknown.
        valid = await command(
            client,
            tenant,
            headers,
            "New-RemoteDomain",
            {"Name": "Good", "DomainName": "example.test"},
        )
        assert valid.status_code == 200, valid.text


async def test_audit_ingestion_configuration(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app, user)
        before = await command(client, tenant, headers, "Get-AdminAuditLogConfig", {})
        assert before.status_code == 200, before.text
        assert before.json()["value"][0]["UnifiedAuditLogIngestionEnabled"] is True
        assert (
            await command(
                client,
                tenant,
                headers,
                "Set-Mailbox",
                {"Identity": str(user), "ForwardingSmtpAddress": "before@example.test"},
            )
        ).status_code == 200
        await drain(database)
        historical_ids = {str(v.record_id) for v in await observations(database, tenant)}
        assert historical_ids
        assert (
            await command(
                client,
                tenant,
                headers,
                "Set-AdminAuditLogConfig",
                {"UnifiedAuditLogIngestionEnabled": False},
            )
        ).status_code == 200
        assert (
            await command(
                client,
                tenant,
                headers,
                "Set-Mailbox",
                {"Identity": str(user), "ForwardingSmtpAddress": "suppressed@example.test"},
            )
        ).status_code == 200
        search = {
            "StartDate": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "EndDate": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        assert (
            await command(client, tenant, headers, "Search-UnifiedAuditLog", search)
        ).status_code == 403
        graph = await headers_for(
            database, settings, tenant, app, None, {"AuditLogsQuery.Read.All"}
        )
        assert (
            await client.get("/v1.0/security/auditLog/queries", headers=graph)
        ).status_code == 403
        # Re-enable before draining: event-time eligibility must not be rewritten by worker timing.
        assert (
            await command(
                client,
                tenant,
                headers,
                "Set-AdminAuditLogConfig",
                {"UnifiedAuditLogIngestionEnabled": True},
            )
        ).status_code == 200
        await drain(database)
        rows = await observations(database, tenant)
        assert historical_ids <= {str(v.record_id) for v in rows}
        assert len([v for v in rows if v.payload["operation"] == "Set-Mailbox"]) == 1
        assert (
            await command(client, tenant, headers, "Search-UnifiedAuditLog", search)
        ).status_code == 200


async def test_security_configuration_lifecycle_and_authority(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app, user)
        created = await command(
            client,
            tenant,
            headers,
            "New-RemoteDomain",
            {"Name": "Preserve", "DomainName": "example.test"},
        )
        assert created.status_code == 200, created.text
        route = f"/_mock/tenants/{tenant}/mail/securityConfiguration"
        export = await client.get(route)
        assert export.status_code == 200, export.text
        assert (
            await client.put(
                route, json={"records": [], "unified_audit_log_ingestion_enabled": True}
            )
        ).status_code == 200
        assert (
            await command(client, tenant, headers, "Get-RemoteDomain", {"Identity": "Preserve"})
        ).status_code == 404
        restored = await client.put(route, json=export.json())
        assert restored.status_code == 200 and restored.json() == export.json(), restored.text
        await client.delete(
            f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Exchange Administrator"
        )
        denied = await command(client, tenant, headers, "Get-RemoteDomain", {})
        assert denied.status_code == 403


async def test_configuration_restore_is_atomic_and_default_unique(database, settings):
    import copy
    from uuid import uuid4

    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app, user)
        route = f"/_mock/tenants/{tenant}/mail/securityConfiguration"
        first = await command(
            client,
            tenant,
            headers,
            "New-SharingPolicy",
            {"Name": "First", "Domains": ["*:CalendarSharingFreeBusySimple"], "Default": True},
        )
        second = await command(
            client,
            tenant,
            headers,
            "New-SharingPolicy",
            {
                "Name": "Second",
                "Domains": ["Anonymous:CalendarSharingFreeBusyDetail"],
                "Default": True,
            },
        )
        assert first.status_code == second.status_code == 200
        actual = await command(client, tenant, headers, "Get-SharingPolicy", {})
        assert [v["Name"] for v in actual.json()["value"] if v["Default"]] == ["Second"]
        before = (await client.get(route)).json()
        invalid = copy.deepcopy(before)
        invalid["records"].append(
            {
                "id": str(uuid4()),
                "kind": "RemoteDomain",
                "properties": {"Name": "Invalid", "DomainName": "bad.*.domain"},
            }
        )
        assert (await client.put(route, json=invalid)).status_code == 422
        assert (await client.get(route)).json() == before
        invalid = copy.deepcopy(before)
        invalid["records"][0]["properties"]["Unrecognized"] = True
        assert (await client.put(route, json=invalid)).status_code == 422
        assert (await client.get(route)).json() == before
        protected = await command(
            client, tenant, headers, "Remove-RemoteDomain", {"Identity": "Default"}
        )
        assert protected.status_code == 400
        assert (await client.get(route)).json() == before


async def test_configuration_other_tenant_identity_is_not_resolved(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app, user)
        foreign = await client.post(
            "/_mock/tenants",
            json={
                "slug": "foreign-config",
                "display_name": "Foreign",
                "domain": "foreign.test",
                "licences": ["E5"],
            },
        )
        assert foreign.status_code == 201, foreign.text
        foreign_id = foreign.json()["id"]
        from uuid import uuid4

        key = str(uuid4())
        imported = await client.put(
            f"/_mock/tenants/{foreign_id}/mail/securityConfiguration",
            json={
                "records": [
                    {
                        "id": key,
                        "kind": "AuthenticationPolicy",
                        "properties": {"Name": "Foreign policy"},
                    }
                ]
            },
        )
        assert imported.status_code == 200, imported.text
        for identity in (key, "Foreign policy"):
            result = await command(
                client, tenant, headers, "Get-AuthenticationPolicy", {"Identity": identity}
            )
            assert result.status_code == 404, result.text


async def test_documented_defaults_and_remote_domain_coupling(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app, user)
        remote = await command(
            client,
            tenant,
            headers,
            "New-RemoteDomain",
            {"Name": "Defaults", "DomainName": "defaults.test"},
        )
        assert remote.status_code == 200, remote.text
        assert remote.json()["value"][0]["MeetingForwardNotificationEnabled"] is True
        inbound = await command(
            client,
            tenant,
            headers,
            "New-InboundConnector",
            {"Name": "TLS default", "SenderDomains": ["*"], "ConnectorType": "Partner"},
        )
        assert inbound.status_code == 200, inbound.text
        assert inbound.json()["value"][0]["RequireTls"] is True
        assert (
            await command(
                client,
                tenant,
                headers,
                "Set-RemoteDomain",
                {"Identity": "Defaults", "AllowedOOFType": "InternalLegacy"},
            )
        ).status_code == 200
        actual = (
            await command(client, tenant, headers, "Get-RemoteDomain", {"Identity": "Defaults"})
        ).json()["value"][0]
        assert actual["IsInternal"] is True
        assert (
            await command(
                client,
                tenant,
                headers,
                "Set-RemoteDomain",
                {"Identity": "Defaults", "IsInternal": False},
            )
        ).status_code == 200
        actual = (
            await command(client, tenant, headers, "Get-RemoteDomain", {"Identity": "Defaults"})
        ).json()["value"][0]
        assert actual["AllowedOOFType"] == "ExternalLegacy"
        assert (
            await command(
                client,
                tenant,
                headers,
                "Set-RemoteDomain",
                {"Identity": "Defaults", "NDRDiagnosticInfoEnabled": False},
            )
        ).status_code == 400


async def test_documented_inbound_connector_constraints(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app, user)
        for extra in (
            {"SenderIPAddresses": ["2001:db8::1"]},
            {"SenderIPAddresses": ["192.0.2.0/16"]},
            {"TlsSenderCertificateName": "bad.*.example.test"},
            {"TreatMessagesAsInternal": True},
        ):
            response = await command(
                client,
                tenant,
                headers,
                "New-InboundConnector",
                {
                    "Name": "Invalid constraint",
                    "SenderDomains": ["*"],
                    "ConnectorType": "Partner",
                    **extra,
                },
            )
            assert response.status_code == 400, response.text
        response = await command(
            client,
            tenant,
            headers,
            "New-InboundConnector",
            {
                "Name": "Valid constraint",
                "SenderDomains": ["*"],
                "ConnectorType": "Partner",
                "SenderIPAddresses": ["192.0.2.1/24"],
                "RestrictDomainsToIPAddresses": True,
            },
        )
        assert response.status_code == 200, response.text
