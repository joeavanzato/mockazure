from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from sqlalchemy import select, text

from mockazure.evidence.models import EvidenceCursor
from mockazure.identity.models import ServicePrincipal
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_activity import dataset, historical
from tests.integration.test_arm_authorization import assign
from tests.integration.test_arm_inventory import access
from tests.integration.test_arm_state import seed_arm


async def setup_activity(client, database, settings, tenant, app, count=1003):
    sub, base, group, resource = await seed_arm(client, tenant)
    evidence = f"/_mock/tenants/{tenant}/evidence"
    assert (
        await client.patch(
            evidence + "/settings",
            json={"delay_seconds": 0, "clock_mode": "fixed", "fixed_time": "2026-10-02T00:00:00Z"},
        )
    ).status_code == 200
    records = [historical(tenant, sub, group) for _ in range(count)]
    for record in records:
        identity = str(uuid4())
        old = record["payload"]["eventDataId"]
        record["payload"]["eventDataId"] = identity
        record["payload"]["id"] = record["payload"]["id"].replace(old, identity)
    # Import batches respect the shared dataset bound.
    for offset in range(0, count, 500):
        result = await client.post(
            evidence + "/import", json=dataset(records[offset : offset + 500])
        )
        assert result.status_code == 200, result.text[:500]
    drained = await client.post(evidence + "/drain", json={"max_jobs": 1000})
    assert drained.status_code == 200 and drained.json()["failed"] == 0, drained.text
    if count > 1000:
        assert (await client.post(evidence + "/drain", json={"max_jobs": 1000})).status_code == 200
    headers = await access(database, settings, tenant, app)
    async with database.sessions() as session:
        sp = await session.scalar(
            select(ServicePrincipal.id).where(
                ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
            )
        )
    assignment = await assign(client, base, sp, f"/subscriptions/{sub}", "Reader")
    path = f"/subscriptions/{sub}/providers/Microsoft.Insights/eventtypes/management/values"
    args = {"api-version": "2015-04-01", "$filter": "eventTimestamp ge '2026-10-01T00:00:00Z'"}
    return sub, base, group, evidence, records, headers, assignment, path, args


async def test_activity_exact_ties_snapshot_filters_selection_and_expiry(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, evidence, records, headers, grant, path, args = await setup_activity(
            client, database, settings, tenant, app
        )
        response = await client.get(path, params=args, headers=headers)
        assert response.status_code == 200, response.text
        assert len(response.json()["value"]) == 100
        first_link = response.json()["nextLink"]
        seen = list(response.json()["value"])
        link = first_link
        while link:
            response = await client.get(link, headers=headers)
            assert response.status_code == 200, response.text
            seen.extend(response.json()["value"])
            link = response.json().get("nextLink")
        assert len(seen) == 1003
        async with database.sessions() as session:
            plan = await session.scalar(
                text(
                    "EXPLAIN (FORMAT JSON) SELECT record_id FROM evidence_versions "
                    "WHERE tenant_id=:tenant AND arm_subscription_id=:sub AND family='activityLog' "
                    "AND arm_event_ticks>=:ticks ORDER BY arm_event_ticks,record_id LIMIT 101"
                ),
                {"tenant": tenant, "sub": sub, "ticks": 639264133231234567},
            )
            assert "ix_arm_activity_time" in str(plan), plan
        assert {r["eventDataId"] for r in seen} == {r["payload"]["eventDataId"] for r in records}
        assert all(r["eventTimestamp"].endswith("1234567Z") for r in seen)
        narrow = dict(
            args,
            **{
                "$filter": "eventTimestamp ge '2026-10-01T01:02:03.1234567Z' and "
                "eventTimestamp le '2026-10-01T01:02:03.1234567Z' and resourceGroupName eq 'IR'",
                "$select": "id,eventTimestamp",
            },
        )
        # Actual seeded group display name, case-insensitive filter.
        narrow["$filter"] = narrow["$filter"].replace("'IR'", repr(group.split("/")[4].upper()))
        selected = await client.get(path, params=narrow, headers=headers)
        assert selected.status_code == 200, selected.text
        assert len(selected.json()["value"]) == 100
        assert set(selected.json()["value"][0]) == {"id", "eventTimestamp"}
        empty_args = dict(args, **{"$filter": "eventTimestamp ge '2026-10-01T01:02:03.1234568Z'"})
        assert (await client.get(path, params=empty_args, headers=headers)).json()["value"] == []
        for name, value in [
            ("resourceUri", group.upper()),
            ("resourceProvider", records[0]["payload"]["resourceProviderName"]["value"].upper()),
            ("correlationId", records[0]["payload"]["correlationId"].upper()),
        ]:
            filtered = await client.get(
                path,
                params=dict(args, **{"$filter": args["$filter"] + f" and {name} eq '{value}'"}),
                headers=headers,
            )
            assert filtered.status_code == 200 and filtered.json()["value"], filtered.text
        token = parse_qs(urlparse(first_link).query)["$skiptoken"][0]
        async with database.sessions.begin() as session:
            cursors = list(
                await session.scalars(
                    select(EvidenceCursor).where(EvidenceCursor.tenant_id == tenant)
                )
            )
            for row in cursors:
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert len(token) == 43
        assert (await client.get(first_link, headers=headers)).status_code == 400


async def test_activity_current_scope_revocation_retention_and_generation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, evidence, records, headers, grant, path, args = await setup_activity(
            client, database, settings, tenant, app, count=101
        )
        first = await client.get(path, params=args, headers=headers)
        assert first.status_code == 200, first.text
        link = first.json()["nextLink"]
        # Later publication is excluded from an existing snapshot.
        fresh = historical(tenant, sub, group, when="2026-10-01T01:02:03.1234568Z")
        assert (await client.post(evidence + "/import", json=dataset([fresh]))).status_code == 200
        assert (await client.post(evidence + "/drain", json={"max_jobs": 10})).status_code == 200
        assert len((await client.get(link, headers=headers)).json()["value"]) == 1
        # Revoke subscription authority, replace it with a sibling group grant.
        removed = await client.delete(base + "/role-assignments/" + grant["id"].rsplit("/", 1)[-1])
        assert removed.status_code == 200, removed.text
        assert (await client.get(link, headers=headers)).status_code == 403
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        sibling = await client.put(base + "/resource-groups/other", json={"location": "eastus"})
        assert sibling.status_code == 200
        await assign(client, base, sp, f"/subscriptions/{sub}/resourceGroups/other", "Reader")
        assert (await client.get(path, params=args, headers=headers)).json()["value"] == []
        assert (await client.get(link, headers=headers)).json()["value"] == []
        # Group scope is sufficient for its own historical records.
        await assign(client, base, sp, group, "Reader")
        assert len((await client.get(link, headers=headers)).json()["value"]) == 1
        # Cleanup must honor the cursor's old cutoff even after the live clock advances.
        changed = await client.patch(
            evidence + "/settings", json={"fixed_time": "2027-10-02T00:00:00Z"}
        )
        assert changed.status_code == 200, changed.text
        assert (
            await client.post(evidence + "/cleanup", json={"max_rows": 1000})
        ).status_code == 200
        assert (await client.get(path, params=args, headers=headers)).json()["value"] == []
        assert len((await client.get(link, headers=headers)).json()["value"]) == 1
        state = (await client.get(evidence + "/state")).json()
        reset = await client.post(
            evidence + "/reset", json={"expected_generation": state["generation"]}
        )
        assert reset.status_code == 200, reset.text
        assert (await client.get(link, headers=headers)).status_code == 400
