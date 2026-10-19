"""Provider operations must survive retries and preserve a single resource authority."""

import asyncio
import copy
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select

from mockazure.cloud.models import CloudEvent
from mockazure.evidence.models import EvidenceSource
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from mockazure.providers.arm.models import ArmResource
from tests.integration.graph_support import graph_client
from tests.integration.test_cloud_resource_inventory import VERSIONS, create, prepare

VM = {
    "location": "eastus",
    "properties": {
        "hardwareProfile": {"vmSize": "Standard_B1s"},
        "storageProfile": {
            "osDisk": {"createOption": "FromImage"},
            "imageReference": {
                "publisher": "Canonical",
                "offer": "ubuntu-24_04-lts",
                "sku": "server",
                "version": "latest",
            },
        },
    },
}
ARGS = {"api-version": VERSIONS["Microsoft.Compute"]}


async def claimed(database, tenant, now, lease=30, kind="arm.provider.operation"):
    async with database.sessions.begin() as session:
        result = await claim_job(
            session, owner=uuid4(), now=now, lease_seconds=lease, tenant_id=tenant, kind=kind
        )
        assert result is not None
        return result


async def test_provider_job_fencing(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        path = group + "/providers/Microsoft.Compute/virtualMachines/lifecycle"
        await create(client, path, VM, headers, ARGS["api-version"])
        for action, power in [
            ("deallocate", "deallocated"),
            ("start", "running"),
            ("powerOff", "stopped"),
            ("restart", "running"),
        ]:
            response = await client.post(path + "/" + action, params=ARGS, headers=headers)
            assert response.status_code == 202, response.text
            assert response.headers["Retry-After"] == "1"
            link = response.headers["Azure-AsyncOperation"]
            assert (await client.get(link, headers=headers)).json()["status"] == "InProgress"
            again = await client.post(path + "/" + action, params=ARGS, headers=headers)
            assert again.headers["Azure-AsyncOperation"] == link
            assert (
                await client.patch(
                    path, params=ARGS, headers=headers, json={"tags": {"during": "job"}}
                )
            ).status_code == 409
            now = datetime.now(UTC)
            stale = await claimed(database, tenant, now, 1)
            assert (
                await dispatch_job(database, stale, now + timedelta(seconds=2))
            ).state == "stale"
            current = await claimed(database, tenant, now + timedelta(seconds=2))
            assert current.epoch > stale.epoch
            from mockazure.providers.arm import provider_jobs

            original = provider_jobs.append_source
            expired = False

            async def expire(*args, **kwargs):
                nonlocal expired
                result = await original(*args, **kwargs)
                expired = True
                return result

            with (
                patch.object(provider_jobs, "append_source", expire),
                patch.object(provider_jobs, "monotonic", lambda: 100.0 if expired else 0.0),
            ):
                assert (
                    await dispatch_job(database, current, now + timedelta(seconds=2))
                ).state == "stale"
            assert (await client.get(link, headers=headers)).json()["status"] == "InProgress"
            assert (
                await dispatch_job(database, current, now + timedelta(seconds=2))
            ).state == "succeeded"
            assert (
                await dispatch_job(database, current, now + timedelta(seconds=2))
            ).state == "stale"
            assert (await client.get(link, headers=headers)).json()["status"] == "Succeeded"
            view = await client.get(path + "/instanceView", params=ARGS, headers=headers)
            assert view.status_code == 200, view.text
            assert "PowerState/" + power in {v["code"] for v in view.json()["statuses"]}
            async with database.sessions() as session:
                facts = list(
                    await session.scalars(
                        select(EvidenceSource).where(
                            EvidenceSource.tenant_id == tenant, EvidenceSource.id == current.id
                        )
                    )
                )
                assert len(facts) == 1 and facts[0].facts["status"] == "Succeeded"
                assert await session.get(CloudEvent, (tenant, current.id)) is not None
        response = await client.delete(path, params=ARGS, headers=headers)
        assert response.status_code == 202, response.text
        now = datetime.now(UTC)
        claim = await claimed(database, tenant, now)
        assert (await dispatch_job(database, claim, now)).state == "succeeded"
        assert (await client.get(path, params=ARGS, headers=headers)).status_code == 404
        assert (await client.get(response.headers["Location"], headers=headers)).status_code == 204


async def test_delete_races_create(database, settings):
    from mockazure.providers.arm import operations

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        response = await client.delete(group, params={"api-version": "2021-04-01"}, headers=headers)
        assert response.status_code == 202, response.text
        now = datetime.now(UTC)
        claim = await claimed(database, tenant, now, kind="arm.group.delete")
        entered, release = asyncio.Event(), asyncio.Event()
        original = operations.append_source

        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        path = group + "/providers/Microsoft.Compute/virtualMachines/race"
        with patch.object(operations, "append_source", held):
            task = asyncio.create_task(dispatch_job(database, claim, now))
            await asyncio.wait_for(entered.wait(), 5)
            creating = asyncio.create_task(client.put(path, params=ARGS, headers=headers, json=VM))
            try:
                await asyncio.sleep(0.05)
                assert not creating.done()
            finally:
                release.set()
            assert (await asyncio.wait_for(task, 5)).state == "succeeded"
            assert (await creating).status_code == 404
        async with database.sessions() as session:
            assert await session.get(ArmResource, (tenant, path.lower())) is None


async def test_cloud_export_restore(database, settings):
    from tests.integration.test_storage_account_management import CREATE

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        await client.post(
            f"/subscriptions/{sub}/providers/Microsoft.Storage/register",
            params={"api-version": "2021-04-01"},
            headers=headers,
        )
        account = group + "/providers/Microsoft.Storage/storageAccounts/lifecycleaccount"
        args = {"api-version": "2025-06-01"}
        assert (
            await client.put(account, params=args, headers=headers, json=CREATE)
        ).status_code == 200
        old = (await client.post(account + "/listKeys", params=args, headers=headers)).json()
        path = group + "/providers/Microsoft.Compute/virtualMachines/exported"
        await create(client, path, VM, headers, ARGS["api-version"])
        statepath = f"/_mock/tenants/{tenant}/cloud/state"
        response = await client.get(statepath)
        assert response.status_code == 200, response.text
        exported = response.json()
        assert not any(k["value"] in response.text for k in old["keys"])
        broken = copy.deepcopy(exported)
        broken["resources"]["resources"][0]["id"] = broken["resources"]["resources"][0][
            "id"
        ].replace(str(sub), str(uuid4()))
        assert (await client.put(statepath, json=broken)).status_code == 422
        assert (await client.get(statepath)).json() == exported
        operation = await client.post(path + "/deallocate", params=ARGS, headers=headers)
        now = datetime.now(UTC)
        claim = await claimed(database, tenant, now)
        reset = await client.post(
            f"/_mock/tenants/{tenant}/cloud/reset",
            json={"expected_generation": exported["generation"]},
        )
        assert reset.status_code == 200, reset.text
        assert (await dispatch_job(database, claim, now)).state == "stale"
        restored = await client.put(statepath, json=exported)
        assert restored.status_code == 200, restored.text
        current = (await client.post(account + "/listKeys", params=args, headers=headers)).json()
        assert all(a["value"] != b["value"] for a, b in zip(old["keys"], current["keys"]))
        assert (await client.get(path, params=ARGS, headers=headers)).status_code == 200
        assert (
            await client.get(operation.headers["Azure-AsyncOperation"], headers=headers)
        ).json()["status"] == "Failed"


async def test_cloud_existing_export_reset_and_restore_flows(database, settings):
    from mockazure.evidence.repository import lock_state
    from mockazure.providers.arm.models import VmState

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        path = group + "/providers/Microsoft.Compute/virtualMachines/export-flow"
        await create(client, path, VM, headers, ARGS["api-version"])
        response = await client.post(
            f"/_mock/tenants/{tenant}/exports", params={"include_cloud": "true"}
        )
        assert response.status_code == 202, response.text
        now = datetime.now(UTC)
        job = await claimed(database, tenant, now, kind="tenant.export")
        assert job.payload["schema_version"] == 2
        assert (await dispatch_job(database, job, now)).state == "succeeded"
        result = await client.get(response.headers["Location"] + "/result")
        assert result.status_code == 200, result.text
        snapshot = result.json()
        assert snapshot["cloud"]["resources"]["resources"][0]["id"] == path
        response = await client.post(path + "/deallocate", params=ARGS, headers=headers)
        assert response.status_code == 202
        now = datetime.now(UTC)
        job = await claimed(database, tenant, now)
        async with database.sessions.begin() as session:
            generation = (await lock_state(session, tenant)).generation
        reset = await client.post(
            f"/_mock/tenants/{tenant}/evidence/reset", json={"expected_generation": str(generation)}
        )
        assert reset.status_code == 200, reset.text
        assert (await dispatch_job(database, job, now)).state == "stale"
        async with database.sessions() as session:
            assert (await session.get(VmState, (tenant, path.lower()))).power_state == "running"
        restored = await client.post(
            "/_mock/tenants/import", json={"snapshot": snapshot, "target_tenant_id": str(tenant)}
        )
        assert restored.status_code == 201, restored.text
        assert restored.json()["id"] == str(tenant)
        wrong = await client.post(
            "/_mock/tenants/import", json={"snapshot": snapshot, "target_tenant_id": str(uuid4())}
        )
        assert wrong.status_code == 422, wrong.text
        malformed = copy.deepcopy(snapshot)
        malformed["cloud"]["generation"] = "invalid"
        rejected = await client.post(
            "/_mock/tenants/import",
            json={"snapshot": malformed, "target_tenant_id": str(tenant)},
        )
        assert rejected.status_code == 422, rejected.text
        assert (await client.get(path, params=ARGS, headers=headers)).status_code == 200


async def test_group_deletion_rejects_external_provider_relationship(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        other = group + "-other"
        assert (
            await client.put(
                other,
                params={"api-version": "2021-04-01"},
                headers=headers,
                json={"location": "eastus"},
            )
        ).status_code in {200, 201}
        network = group + "/providers/Microsoft.Network/virtualNetworks/shared"
        args = {"api-version": VERSIONS["Microsoft.Network"]}
        await create(
            client,
            network,
            {
                "location": "eastus",
                "properties": {"addressSpace": {"addressPrefixes": ["10.5.0.0/16"]}},
            },
            headers,
            args["api-version"],
        )
        subnet = network + "/subnets/default"
        await create(
            client,
            subnet,
            {"properties": {"addressPrefix": "10.5.0.0/24"}},
            headers,
            args["api-version"],
        )
        await create(
            client,
            other + "/providers/Microsoft.Network/networkInterfaces/nic",
            {
                "location": "eastus",
                "properties": {
                    "ipConfigurations": [
                        {"name": "primary", "properties": {"subnet": {"id": subnet}}}
                    ]
                },
            },
            headers,
            args["api-version"],
        )
        response = await client.delete(group, params={"api-version": "2021-04-01"}, headers=headers)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "ResourceInUse"
        assert (
            await client.get(group, params={"api-version": "2021-04-01"}, headers=headers)
        ).json()["properties"]["provisioningState"] == "Succeeded"


async def test_provider_polling_authority_and_exhausted_lease(database, settings):
    from mockazure.db.job_model import Job
    from mockazure.providers.arm.models import RoleAssignment, VmState
    from tests.integration.test_arm_authorization import assign

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        path = group + "/providers/Microsoft.Compute/virtualMachines/authority"
        await create(client, path, VM, headers, ARGS["api-version"])
        assert (
            await client.post(
                path + "/deallocate", params=ARGS | {"hibernate": "true"}, headers=headers
            )
        ).status_code == 400
        assert (
            await client.post(path + "/start", params=ARGS, headers=headers, json={})
        ).status_code == 400
        response = await client.post(path + "/deallocate", params=ARGS, headers=headers)
        now = datetime.now(UTC)
        async with database.sessions() as session:
            assignments = list(
                await session.scalars(
                    select(RoleAssignment.id).where(
                        RoleAssignment.tenant_id == tenant, RoleAssignment.client_id == app
                    )
                )
            )
        for identifier in assignments:
            await client.delete(base + "/role-assignments/" + str(identifier))
        assert (
            await client.get(response.headers["Azure-AsyncOperation"], headers=headers)
        ).status_code == 403
        # ARM accepts the action at submission; removing the grant prevents new calls/polls,
        # not completion of already accepted service work.
        for attempt in range(3):
            claim = await claimed(database, tenant, now + timedelta(seconds=attempt * 2), 1)
        async with database.sessions.begin() as session:
            assert (
                await claim_job(
                    session,
                    owner=uuid4(),
                    now=now + timedelta(seconds=8),
                    lease_seconds=30,
                    tenant_id=tenant,
                    kind="arm.provider.operation",
                )
                is None
            )
        async with database.sessions() as session:
            failed = await session.get(Job, claim.id)
            assert failed.state == "failed" and failed.error_code == "WorkerLeaseExpired"
            assert (await session.get(VmState, (tenant, path.lower()))).power_state == "running"
            row = await session.get(ArmResource, (tenant, path.lower()))
            assert row.deletion_job_id is None and row.provisioning_state == "Failed"
            facts = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.id == claim.id
                    )
                )
            )
            assert len(facts) == 1 and facts[0].facts["status"] == "Failed"
        await assign(client, base, principal, f"/subscriptions/{sub}", "Contributor")
        poll = await client.get(response.headers["Azure-AsyncOperation"], headers=headers)
        assert poll.json()["status"] == "Failed"
        retry = await client.post(path + "/deallocate", params=ARGS, headers=headers)
        assert (
            retry.status_code == 202
            and retry.headers["Azure-AsyncOperation"] != response.headers["Azure-AsyncOperation"]
        )


async def test_cloud_migration_from_052_preserves_inventory(settings):
    from alembic import command
    from sqlalchemy import MetaData, Table, create_engine, text

    from mockazure.db.migrations import configuration, migrate
    from mockazure.db.models import Tenant

    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "052")
            tenant, subscription = uuid4(), uuid4()
            connection.execute(
                Tenant.__table__.insert().values(
                    id=tenant,
                    slug="legacy-cloud",
                    display_name="Legacy Cloud",
                    domain="legacy-cloud.test",
                    licences=[],
                    revision=1,
                )
            )
            metadata = MetaData()
            subscriptions = Table("arm_subscriptions", metadata, autoload_with=connection)
            inventory = Table("arm_resources", metadata, autoload_with=connection)
            connection.execute(
                subscriptions.insert().values(
                    id=subscription,
                    tenant_id=tenant,
                    display_name="Legacy inventory",
                    state="Enabled",
                )
            )
            group = f"/subscriptions/{subscription}/resourceGroups/legacy"
            resource = group + "/providers/Microsoft.Compute/virtualMachines/retained"
            for identifier, kind, group_key, resource_type in (
                (group, "group", None, "Microsoft.Resources/resourceGroups"),
                (resource, "resource", group.lower(), "Microsoft.Compute/virtualMachines"),
            ):
                connection.execute(
                    inventory.insert().values(
                        tenant_id=tenant,
                        subscription_id=subscription,
                        scope_key=identifier.lower(),
                        display_id=identifier,
                        kind=kind,
                        group_key=group_key,
                        name=identifier.rsplit("/", 1)[1],
                        resource_type=resource_type,
                        location="eastus",
                        tags={"legacy": "kept"},
                        payload={"properties": {"legacyMarker": True}},
                        provisioning_state="Succeeded",
                        revision=3,
                    )
                )
        migrate(settings.database_url.get_secret_value())
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "068"
            assert (
                connection.scalar(text("SELECT to_regclass('cloud_vm_states')"))
                == "cloud_vm_states"
            )
            preserved = (
                connection.execute(select(inventory).order_by(inventory.c.scope_key))
                .mappings()
                .all()
            )
            assert len(preserved) == 2
            assert all(
                row["tags"] == {"legacy": "kept"} and row["revision"] == 3 for row in preserved
            )
            assert all(row["payload"]["properties"]["legacyMarker"] is True for row in preserved)
            # Historical inventory does not silently become executable provider state.
            assert connection.scalar(text("SELECT count(*) FROM cloud_resources")) == 0
            config.attributes["connection"] = connection
            command.downgrade(config, "059")
        migrate(settings.database_url.get_secret_value())
    finally:
        engine.dispose()


async def test_failure_completion_cannot_outlive_worker_lease(database, settings):
    from mockazure.db.job_model import Job
    from mockazure.providers.arm import provider_jobs

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        path = group + "/providers/Microsoft.Compute/virtualMachines/failure-fence"
        await create(client, path, VM, headers, ARGS["api-version"])
        assert (await client.post(path + "/start", params=ARGS, headers=headers)).status_code == 202
        now = datetime.now(UTC)
        claim = await claimed(database, tenant, now)
        expired = False
        original = provider_jobs.append_source

        async def expire(*args, **kwargs):
            nonlocal expired
            result = await original(*args, **kwargs)
            expired = True
            return result

        with (
            patch.object(provider_jobs, "append_source", expire),
            patch.object(provider_jobs, "monotonic", lambda: 100.0 if expired else 0.0),
        ):
            async with database.sessions.begin() as session:
                changed = await provider_jobs.fail_provider_operation(
                    session, claim.id, now, claim=claim, error_code="ArmOperationFailed"
                )
                assert changed is False
        async with database.sessions() as session:
            assert (await session.get(Job, claim.id)).state == "running"
            assert (
                await session.get(ArmResource, (tenant, path.lower()))
            ).deletion_job_id == claim.id
            assert await session.get(EvidenceSource, (tenant, "arm", claim.id)) is None
            assert await session.get(CloudEvent, (tenant, claim.id)) is None


async def test_provider_domain_failure_is_terminal_and_queryable(database, settings):
    from mockazure.providers.arm import provider_jobs
    from mockazure.providers.arm.errors import ArmError

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await prepare(
            client, database, settings, tenant, app
        )
        path = group + "/providers/Microsoft.Compute/virtualMachines/delete-failure"
        await create(client, path, VM, headers, ARGS["api-version"])
        response = await client.delete(path, params=ARGS, headers=headers)
        assert response.status_code == 202
        now = datetime.now(UTC)
        claim = await claimed(database, tenant, now)
        with patch.object(
            provider_jobs,
            "require_unreferenced",
            side_effect=ArmError(409, "ResourceInUse", "Referenced"),
        ):
            result = await dispatch_job(database, claim, now)
        assert result.state == "failed"
        poll = await client.get(response.headers["Azure-AsyncOperation"], headers=headers)
        assert poll.json()["status"] == "Failed"
        assert (await client.get(path, params=ARGS, headers=headers)).status_code == 200
        async with database.sessions() as session:
            fact = await session.get(EvidenceSource, (tenant, "arm", claim.id))
            assert fact.facts["status"] == "Failed"
