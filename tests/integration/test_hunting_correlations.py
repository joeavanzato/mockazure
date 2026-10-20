from datetime import UTC, datetime

from mockazure.evidence.clock import update_settings
from mockazure.evidence.schemas import EvidenceSettings
from mockazure.hunting.contracts import catalog
from mockazure.population.catalog import get_adapter
from mockazure.population.generators import generate, inventory
from mockazure.population.types import AdapterContext
from tests.integration.graph_support import graph_client, headers_for


async def test_populated_hunting_joins_resolve_directory_mail_device_and_alert(database, settings):
    async with graph_client(database, settings) as (client, tid, app, user):
        params = {
            "tenant": str(tid),
            "namespace": "correlations",
            "seed": 42,
            "users": 2,
            "count": 2,
            "days": 7,
            "referenceTime": "2026-10-07T12:00:00Z",
            "projects": 1,
        }
        refs = {"domain": "contoso.test"}
        ctx = AdapterContext(database, settings, tid)
        for name, count in (
            ("business.users", 2),
            ("business.directory-devices", 2),
            ("hunting.settings", 1),
            ("hunting.alerts", 2),
            *((f"hunting.{table}", 2) for table in catalog()),
        ):
            adapter = get_adapter(inventory()[name]["family"])
            mutations = generate(name, params | {"count": count}, refs, offset=0, limit=20)
            result = await adapter.apply(ctx, mutations)
            assert (await adapter.verify(ctx, result)).status == "succeeded", name
            assert (await adapter.apply(ctx, mutations)).effects["unchanged"] == count
        async with database.sessions.begin() as session:
            await update_settings(
                session,
                tid,
                EvidenceSettings(
                    clock_mode="fixed",
                    fixed_time=datetime(2026, 10, 7, 12, tzinfo=UTC),
                ),
            )
        headers = await headers_for(
            database,
            settings,
            tid,
            app,
            None,
            {
                "ThreatHunting.Read.All",
                "User.Read.All",
                "SecurityAlert.Read.All",
                "Device.Read.All",
            },
        )
        queries = (
            (
                "IdentityInfo | join kind=inner (DeviceProcessEvents) on AccountObjectId"
                " | project AccountObjectId, DeviceId",
                "AccountObjectId",
            ),
            (
                "EmailEvents | join kind=inner (EmailAttachmentInfo) on NetworkMessageId"
                " | project NetworkMessageId, SHA256",
                "NetworkMessageId",
            ),
            (
                "AlertInfo | join kind=inner (AlertEvidence) on AlertId"
                " | project AlertId, AccountObjectId",
                "AlertId",
            ),
            (
                "IdentityInfo | join kind=inner (CloudAppEvents) on AccountObjectId"
                " | project AccountObjectId, ActionType",
                "AccountObjectId",
            ),
        )
        for query, key in queries:
            result = await client.post(
                "/v1.0/security/runHuntingQuery", headers=headers, json={"Query": query}
            )
            assert result.status_code == 200, result.text
            rows = result.json()["results"]
            assert len(rows) == 2 and all(row[key] for row in rows)
            if key == "AccountObjectId":
                for row in rows:
                    assert (
                        await client.get(
                            "/v1.0/users/" + row[key],
                            headers=headers,
                        )
                    ).status_code == 200
            elif key == "AlertId":
                for row in rows:
                    alert = await client.get(
                        "/v1.0/security/alerts_v2/" + row[key], headers=headers
                    )
                    assert alert.status_code == 200, alert.text
                    assert (
                        alert.json()["evidence"][0]["userAccount"]["azureAdUserId"]
                        == row["AccountObjectId"]
                    )
