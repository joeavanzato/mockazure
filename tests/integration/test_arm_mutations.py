import asyncio
from uuid import UUID

from sqlalchemy import select

from mockazure.evidence.models import EvidenceSource
from mockazure.identity.models import ServicePrincipal
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_arm_inventory import access
from tests.integration.test_arm_state import seed_arm


async def test_arm_group_tag_mutations_atomic_and_noop(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        grant = await assign(client, base, sp, f"/subscriptions/{sub}", "Reader")
        args = {"api-version": "2021-04-01"}
        path = group + "/providers/Microsoft.Resources/tags/default"
        assert (
            await client.put(
                path, params=args, headers=headers, json={"properties": {"tags": {"a": "b"}}}
            )
        ).status_code == 403
        await client.delete(base + "/role-assignments/" + grant["name"])
        await assign(client, base, sp, f"/subscriptions/{sub}", "Contributor")
        target = f"/subscriptions/{sub}/resourceGroups/new"
        created = await client.put(
            target,
            params=args,
            headers=headers,
            json={"location": "eastus", "tags": {"Purpose": "Evidence"}},
        )
        assert created.status_code == 201, created.text
        assert (
            await client.put(target, params=args, headers=headers, json={"location": "westus"})
        ).status_code == 409
        changed = await client.patch(
            target, params=args, headers=headers, json={"tags": {"Team": "Blue"}}
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["tags"] == {"Team": "Blue"}

        async def facts():
            async with database.sessions() as session:
                return list(
                    await session.scalars(
                        select(EvidenceSource)
                        .where(EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "arm")
                        .order_by(EvidenceSource.source_revision)
                    )
                )

        before = await facts()
        assert len(before) == 2
        assert before[0].facts["scope"].lower() == target.lower()
        assert before[0].facts["after"]["tags"] == {"Purpose": "Evidence"}
        assert before[0].facts["provenance"] == "native"
        UUID(before[0].facts["correlation_id"])
        assert (
            await client.patch(
                target, params=args, headers=headers, json={"tags": {"Team": "Blue"}}
            )
        ).status_code == 200
        assert len(await facts()) == 2
        for scope in (f"/subscriptions/{sub}", group, resource["id"]):
            path = scope + "/providers/Microsoft.Resources/tags/default"
            replaced = await client.put(
                path, params=args, headers=headers, json={"properties": {"tags": {"Owner": "One"}}}
            )
            assert replaced.status_code == 200, replaced.text

            async def merge(tag):
                return await client.patch(
                    path,
                    params=args,
                    headers=headers,
                    json={"operation": "Merge", "properties": {"tags": {tag: tag}}},
                )

            results = await asyncio.gather(merge("A"), merge("B"))
            assert all(r.status_code == 200 for r in results), [r.text for r in results]
            response = await client.get(path, params=args, headers=headers)
            assert response.json()["properties"]["tags"] == {"Owner": "One", "A": "A", "B": "B"}
            assert (
                await client.patch(
                    path,
                    params=args,
                    headers=headers,
                    json={"operation": "Merge", "properties": {"tags": {"owner": "Two"}}},
                )
            ).status_code == 200
            response = await client.patch(
                path,
                params=args,
                headers=headers,
                json={"operation": "Delete", "properties": {"tags": {"OWNER": "two"}}},
            )
            assert response.json()["properties"]["tags"]["Owner"] == "Two"
            response = await client.patch(
                path,
                params=args,
                headers=headers,
                json={"operation": "Delete", "properties": {"tags": {"OWNER": "Two"}}},
            )
            assert "Owner" not in response.json()["properties"]["tags"]
            response = await client.patch(
                path,
                params=args,
                headers=headers,
                json={"operation": "Delete", "properties": {"tags": {"a": ""}}},
            )
            assert "A" not in response.json()["properties"]["tags"]
            count = len(await facts())
            failed = await client.put(
                path,
                params=args,
                headers=headers,
                json={"properties": {"tags": {str(i): "v" for i in range(51)}}},
            )
            assert failed.status_code == 400
            assert len(await facts()) == count
            assert (await client.delete(path, params=args, headers=headers)).status_code == 200
            assert (await client.get(path, params=args, headers=headers)).json()["properties"][
                "tags"
            ] == {}
        # An injected source-persistence failure must roll back the associated mutation.
        from unittest.mock import patch

        async def fail(*args, **kwargs):
            raise ValueError("test source failure")

        with patch("mockazure.providers.arm.facts.append_source", fail):
            response = await client.patch(
                target, params=args, headers=headers, json={"tags": {"Should": "Rollback"}}
            )
            assert response.status_code >= 400
        assert (await client.get(target, params=args, headers=headers)).json()["tags"] == {
            "Team": "Blue"
        }
