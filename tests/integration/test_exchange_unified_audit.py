"""Real Exchange HTTP requests over persisted audit evidence and signed local tokens."""

import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete

from mockazure.admin.app import create_app
from mockazure.identity.models import ServicePrincipalRole
from tests.integration.graph_support import graph_client
from tests.integration.test_audit_queries import entitlement, seed
from tests.integration.test_exchange_mail import command, exchange_headers

NAME = "Search-UnifiedAuditLog"


async def setup(client, database, settings, tenant, app):
    await entitlement(client, tenant)
    response = await client.put(
        f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Exchange Administrator"
    )
    assert response.status_code == 200, response.text
    return await exchange_headers(database, settings, tenant, app)


def query(**changes):
    now = datetime.now(UTC)
    return {
        "StartDate": (now - timedelta(days=1)).isoformat(),
        "EndDate": (now + timedelta(minutes=1)).isoformat(),
        **changes,
    }


async def test_search_session_resume(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app)
        records = await seed(database, tenant, count=1101)
        other = await client.post(
            "/_mock/tenants",
            json={"slug": "other", "display_name": "Other", "domain": "other.test"},
        )
        assert other.status_code == 201, other.text
        from uuid import UUID

        await seed(database, UUID(other.json()["id"]), count=2)
        parameters = query(
            SessionId="resume-audit", SessionCommand="ReturnLargeSet", ResultSize=500
        )
        first = await command(client, tenant, headers, NAME, parameters)
        assert first.status_code == 200, first.text
        pages = [first.json()["value"]]
        assert len(pages[0]) == 500
        # A newly built app has no in-process session state.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url="https://localhost:8443",
        ) as restarted:
            for _ in range(3):
                result = await command(restarted, tenant, headers, NAME, parameters)
                assert result.status_code == 200, result.text
                pages.append(result.json()["value"])
        assert [len(page) for page in pages] == [500, 500, 101, 0]
        values = [row for page in pages for row in page]
        ids = [row["Identity"] for row in values]
        assert len(ids) == len(set(ids)) == 1101
        assert set(ids) == {r["id"] for r in records}
        assert all(row["ResultCount"] == 1101 for row in values)
        assert [row["ResultIndex"] for row in values] == list(range(1, 1102))
        for row in values:
            audit = json.loads(row["AuditData"])
            assert audit["Id"] == row["Identity"]
            assert audit["OrganizationId"] == str(tenant)
            assert audit["RecordType"] == 1
            assert audit["Operation"] == "Set-Mailbox"


async def test_session_scope_and_filters(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app)
        await seed(database, tenant, count=5)
        parameters = query(
            SessionId="private-search", SessionCommand="ReturnLargeSet", ResultSize=2
        )
        first = await command(client, tenant, headers, NAME, parameters)
        assert first.status_code == 200, first.text
        original_ids = {r["Identity"] for r in first.json()["value"]}
        changed = await command(client, tenant, headers, NAME, {**parameters, "Operations": ["X"]})
        assert changed.status_code == 400, changed.text
        async with database.sessions.begin() as session:
            await session.execute(
                delete(ServicePrincipalRole).where(
                    ServicePrincipalRole.tenant_id == tenant,
                    ServicePrincipalRole.client_id == app,
                )
            )
        denied = await command(client, tenant, headers, NAME, parameters)
        assert denied.status_code == 403, denied.text
        await setup(client, database, settings, tenant, app)
        resumed = await command(client, tenant, headers, NAME, parameters)
        assert resumed.status_code == 200, resumed.text
        rows = resumed.json()["value"]
        assert len(rows) == 2 and rows[0]["ResultIndex"] == 3
        assert original_ids.isdisjoint(r["Identity"] for r in rows)


async def test_audit_search_filters_and_parameter_errors(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app)
        records = await seed(database, tenant, count=3)
        await seed(database, tenant, count=2, service="OneDrive")
        parameters = query(
            RecordType="exchangeadmin",
            Operations=["set-mailbox"],
            UserIds=["ACTOR@example.test"],
            ObjectIds=["mailbox-1"],
            IPAddresses=["192.0.2.42"],
            FreeText="needle-1",
            Formatted=True,
        )
        found = await command(client, tenant, headers, NAME, parameters)
        assert found.status_code == 200, found.text
        rows = found.json()["value"]
        assert [r["Identity"] for r in rows] == [records[1]["id"]]
        assert rows[0]["RecordType"] == "ExchangeAdmin"
        assert isinstance(rows[0]["AuditData"], str)
        assert json.loads(rows[0]["AuditData"])["Marker"] == "needle-1"
        for changes in [
            {"ResultSize": 5001},
            {"ResultSize": 0},
            {"ResultSize": True},
            {"SessionCommand": "nonsense"},
            {"RecordType": "invented"},
            {"HighCompleteness": True},
            {"StartDate": "not-a-date"},
            {"EndDate": "2000-01-01"},
            {"IPAddresses": ["not-an-ip"]},
        ]:
            invalid = await command(client, tenant, headers, NAME, query(**changes))
            assert invalid.status_code == 400, (changes, invalid.text)
        missing = await command(client, tenant, headers, NAME, {"StartDate": "2026-01-01"})
        assert missing.status_code == 400
        equal = await command(
            client,
            tenant,
            headers,
            NAME,
            {"StartDate": "2026-01-01", "EndDate": "2026-01-01"},
        )
        assert equal.status_code == 200 and equal.json()["value"] == []


async def test_default_limit_and_numeric_record_type(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app)
        await seed(database, tenant, count=105)
        default = await command(client, tenant, headers, NAME, query())
        assert default.status_code == 200, default.text
        assert len(default.json()["value"]) == 100
        numeric = await command(
            client, tenant, headers, NAME, query(RecordType="1", ResultSize=500)
        )
        assert numeric.status_code == 200, numeric.text
        assert len(numeric.json()["value"]) == 105


async def test_concurrent_pages_and_snapshot_membership(database, settings):
    import asyncio

    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app)
        records = await seed(database, tenant, count=9)
        parameters = query(SessionId="concurrent", SessionCommand="ReturnLargeSet", ResultSize=2)
        first = await command(client, tenant, headers, NAME, parameters)
        assert first.status_code == 200, first.text
        await seed(database, tenant, count=2)
        pages = await asyncio.gather(
            *[command(client, tenant, headers, NAME, parameters) for _ in range(4)]
        )
        assert all(page.status_code == 200 for page in pages)
        rows = first.json()["value"] + [row for page in pages for row in page.json()["value"]]
        assert len(rows) == 9
        assert {row["Identity"] for row in rows} == {r["id"] for r in records}
        assert {row["ResultIndex"] for row in rows} == set(range(1, 10))
        fresh = await command(client, tenant, headers, NAME, query(ResultSize=50))
        assert len(fresh.json()["value"]) == 11


async def test_expiry_reset_and_bounded_cleanup(database, settings):
    from uuid import uuid4

    from sqlalchemy import select

    from mockazure.evidence.models import EvidenceState
    from mockazure.mail.unified_audit import cleanup_unified_audit
    from mockazure.mail.unified_audit_models import UnifiedAuditSession

    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app)
        await seed(database, tenant, count=3)
        parameters = query(SessionId="expiry", ResultSize=1)
        assert (await command(client, tenant, headers, NAME, parameters)).status_code == 200
        async with database.sessions.begin() as session:
            row = await session.scalar(
                select(UnifiedAuditSession).where(UnifiedAuditSession.tenant_id == tenant)
            )
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        expired = await command(client, tenant, headers, NAME, parameters)
        assert expired.status_code == 410
        async with database.sessions.begin() as session:
            result = await cleanup_unified_audit(session, tenant, datetime.now(UTC), 2)
            assert result == {"deleted": 2, "remaining": True}
        async with database.sessions.begin() as session:
            result = await cleanup_unified_audit(session, tenant, datetime.now(UTC), 2)
            assert result == {"deleted": 1, "remaining": False}
        assert (await command(client, tenant, headers, NAME, parameters)).status_code == 410
        reset_parameters = {**parameters, "SessionId": "reset"}
        assert (await command(client, tenant, headers, NAME, reset_parameters)).status_code == 200
        async with database.sessions.begin() as session:
            state = await session.get(EvidenceState, tenant)
            state.generation = uuid4()
        assert (await command(client, tenant, headers, NAME, reset_parameters)).status_code == 410


async def test_delegated_authority_and_session_owner(database, settings):
    from tests.integration.graph_support import headers_for

    async with graph_client(database, settings) as (client, tenant, app, user):
        app_headers = await setup(client, database, settings, tenant, app)
        await seed(database, tenant, count=3)
        parameters = query(SessionId="owned-by-app", ResultSize=1)
        assert (await command(client, tenant, app_headers, NAME, parameters)).status_code == 200
        role_path = f"/_mock/tenants/{tenant}/identity/users/{user}/roles/Exchange Administrator"
        assert (await client.put(role_path)).status_code == 200
        delegated = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"Exchange.Manage"},
            "delegated",
            audience="https://outlook.office365.com",
        )
        assert (await command(client, tenant, delegated, NAME, query())).status_code == 403
        audit_role = f"/_mock/tenants/{tenant}/mail/audit-roles/{user}/View-Only Audit Logs"
        assert (await client.put(audit_role)).status_code == 200
        assert (await command(client, tenant, delegated, NAME, query())).status_code == 200
        assert (await command(client, tenant, delegated, NAME, parameters)).status_code == 403
        own = {**parameters, "SessionId": "owned-by-user"}
        assert (await command(client, tenant, delegated, NAME, own)).status_code == 200
        assert (await client.delete(audit_role)).status_code == 204
        assert (await command(client, tenant, delegated, NAME, own)).status_code == 403
        await client.put(
            f"/_mock/tenants/{tenant}/mail/settings",
            json={"exchange_enabled": True, "audit_tier": "none"},
        )
        assert (await command(client, tenant, app_headers, NAME, parameters)).status_code == 403


async def test_cleanup_reports_remaining_tombstones(database, settings):
    from sqlalchemy import select

    from mockazure.mail.unified_audit import cleanup_unified_audit
    from mockazure.mail.unified_audit_models import UnifiedAuditSession

    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app)
        for index in range(3):
            result = await command(client, tenant, headers, NAME, query(SessionId=f"empty-{index}"))
            assert result.status_code == 200
        async with database.sessions.begin() as session:
            for row in await session.scalars(
                select(UnifiedAuditSession).where(UnifiedAuditSession.tenant_id == tenant)
            ):
                row.expires_at = datetime.now(UTC) - timedelta(days=8)
        async with database.sessions.begin() as session:
            result = await cleanup_unified_audit(session, tenant, datetime.now(UTC), 1)
            assert result == {"deleted": 1, "remaining": True}


async def test_audit_result_caps_and_mixed_modes(database, settings, monkeypatch):
    from uuid import uuid4

    from sqlalchemy import insert, select

    from mockazure.evidence.models import EvidenceObservation, EvidenceVersion
    from mockazure.mail import unified_audit
    from mockazure.mail.unified_audit_models import UnifiedAuditSession

    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await setup(client, database, settings, tenant, app)
        await seed(database, tenant, count=1)
        # Bulk fixture construction preserves the relational provenance constraints.
        async with database.sessions.begin() as session:
            version = (
                (
                    await session.execute(
                        select(EvidenceVersion.__table__).where(EvidenceVersion.tenant_id == tenant)
                    )
                )
                .mappings()
                .one()
            )
            observation = (
                (
                    await session.execute(
                        select(EvidenceObservation.__table__).where(
                            EvidenceObservation.tenant_id == tenant
                        )
                    )
                )
                .mappings()
                .one()
            )
            for _ in range(50):
                observations, versions = [], []
                for _ in range(1000):
                    identifier = uuid4()
                    payload = {
                        key: value
                        for key, value in version["payload"].items()
                        if key not in {"auditData", "administrativeUnits", "objectId", "clientIp"}
                    }
                    payload["id"] = str(identifier)
                    observations.append(
                        {**observation, "record_id": identifier, "payload": payload}
                    )
                    versions.append({**version, "record_id": identifier, "payload": payload})
                await session.execute(insert(EvidenceObservation), observations)
                await session.execute(insert(EvidenceVersion), versions)
        parameters = query(
            ResultSize=5000, SessionId="caps", SessionCommand="ReturnNextPreviewPage"
        )
        preview = await command(client, tenant, headers, NAME, parameters)
        assert preview.status_code == 200, preview.text[:500]
        assert len(preview.json()["value"]) == 5000
        assert preview.json()["value"][0]["ResultCount"] == 5000
        mixed = await command(
            client, tenant, headers, NAME, {**parameters, "SessionCommand": "ReturnLargeSet"}
        )
        assert mixed.status_code == 200, mixed.text[:500]
        assert len(mixed.json()["value"]) == 5000
        assert mixed.json()["value"][0]["ResultIndex"] == 5001
        assert mixed.json()["value"][0]["ResultCount"] == 10000
        assert (await command(client, tenant, headers, NAME, parameters)).json()["value"] == []
        large_parameters = {
            **parameters,
            "SessionId": "large",
            "SessionCommand": "ReturnLargeSet",
            "ResultSize": 1,
        }
        large = await command(client, tenant, headers, NAME, large_parameters)
        assert large.status_code == 200, large.text[:500]
        assert large.json()["value"][0]["ResultCount"] == 50000
        async with database.sessions.begin() as session:
            row = await session.scalar(
                select(UnifiedAuditSession).where(
                    UnifiedAuditSession.tenant_id == tenant,
                    UnifiedAuditSession.first_mode == "ReturnLargeSet",
                )
            )
            row.position = 49999
        last = await command(client, tenant, headers, NAME, large_parameters)
        assert last.json()["value"][0]["ResultIndex"] == 50000
        assert (await command(client, tenant, headers, NAME, large_parameters)).json()[
            "value"
        ] == []
        monkeypatch.setattr(unified_audit, "MAX_SNAPSHOT_BYTES", 1)
        bounded = await command(client, tenant, headers, NAME, query())
        assert bounded.status_code == 413
