from mockazure.population.catalog import get_adapter
from mockazure.population.generators import inventory
from mockazure.population.types import AdapterContext, Mutation
from tests.integration.graph_support import graph_client, headers_for
from tests.unit.test_business_relationships import values


async def test_small_template_persists_service_bindings(database, settings, monkeypatch):
    from datetime import UTC, datetime

    from sqlalchemy import select

    from mockazure.population.actions import PublicActions
    from mockazure.population.identity import resolve_identity
    from mockazure.population.models import EntityBindingRow, OperationReceipt, PopulationStep
    from mockazure.population.planner import RunRequest, preview
    from mockazure.population.protection import unseal
    from mockazure.population.repository import export_manifest, submit_run
    from mockazure.population.transport import LocalTransport
    from tests.integration.identity_support import identity_client
    from tests.integration.test_population_runs import drive

    async with identity_client(database, settings) as (client, tenant, *_):

        async def transport(self, context, identity):
            return LocalTransport(context, await resolve_identity(context, identity), client=client)

        monkeypatch.setattr(PublicActions, "transport", transport)
        request = RunRequest.model_validate(
            {
                "bundle_id": "builtin",
                "template": "living-small-business",
                "target": {"existing_tenant_id": str(tenant)},
                "dataset_namespace": "estate",
                "parameters": {
                    "shared.users": 2,
                    "shared.projects": 1,
                    "shared.referenceTime": "2026-10-07T00:00:00Z",
                },
            }
        )
        planned = await preview(database, request, datetime.now(UTC))
        run = await submit_run(database, planned.id, planned.digest, datetime.now(UTC))
        result = await drive(database, run.id, limit=300)
        async with database.sessions() as session:
            steps = list(
                (
                    await session.scalars(
                        select(PopulationStep).where(
                            PopulationStep.run_id == run.id,
                        )
                    )
                ).all()
            )
            bindings = list(
                (
                    await session.scalars(
                        select(EntityBindingRow).where(
                            EntityBindingRow.namespace == "estate",
                        )
                    )
                ).all()
            )
            receipts = list(
                (
                    await session.scalars(
                        select(OperationReceipt).where(
                            OperationReceipt.origin_run == run.id,
                        )
                    )
                ).all()
            )
        assert result.state == "succeeded", (
            result.errors,
            [(s.key, s.state) for s in steps if s.state != "succeeded"],
        )
        assert {"graph", "arm", "storage", "file"} <= {b.service for b in bindings}
        assert not any(b.parent_id == "deviceManagement" for b in bindings)
        actions = {
            row.family: unseal(database, row.encrypted_result)["items"][0]
            for row in receipts
            if row.encrypted_result
            and unseal(database, row.encrypted_result)["items"]
            and unseal(database, row.encrypted_result)["items"][0]["properties"].get(
                "public_action"
            )
        }
        ctx = AdapterContext(database, settings, run.tenant_id)

        async def read(family):
            item = actions[family]
            p = item["properties"]
            caller = await transport(None, ctx, p["identity_ref"])
            caller.shared_key = p["shared_key"]
            response = await caller.request(
                p["service"],
                p["read_method"],
                p["read_path"],
                params=p["parameters"],
                headers=p["headers"],
            )
            assert response.status_code == 200, (family, response.text)
            return response

        import hashlib
        import json
        from xml.etree import ElementTree as ET

        blob, file = await read("storage.blobs"), await read("storage.files")
        assert blob.content == file.content and blob.content.startswith(b"PK")
        assert (
            hashlib.sha256(blob.content).hexdigest()
            == actions["storage.blobs"]["properties"]["content_sha256"]
        )
        entity = (await read("storage.entities")).json()
        assert entity["RowKey"] == "example" and entity["Count"] == 1
        queue = ET.fromstring((await read("storage.messages")).content)
        assert "Synthetic queue item estate" in [e.text for e in queue.findall(".//MessageText")]
        diagnostics = (await read("monitor.diagnostics")).json()["properties"]
        workspace = (await read("monitor.workspaces")).json()
        assert diagnostics["workspaceId"].lower() == workspace["id"].lower()
        # Public collectors see application ownership and the same resource owner IDs.
        p = actions["monitor.diagnostics"]["properties"]
        caller = await transport(None, ctx, p["identity_ref"])
        users = (await caller.request("graph", "GET", "/v1.0/users")).json()["value"]
        apps = (await caller.request("graph", "GET", "/v1.0/applications")).json()["value"]
        project_app = next(a for a in apps if a["displayName"] == "Project 1 local application")
        owners = await caller.request(
            "graph", "GET", f"/v1.0/applications/{project_app['id']}/owners"
        )
        assert owners.status_code == 200 and len(owners.json()["value"]) == 1
        owner_ids = {u["id"] for u in users}
        assert owners.json()["value"][0]["id"] in owner_ids
        group = diagnostics["workspaceId"].split("/providers/")[0]
        resources = await caller.request(
            "arm", "GET", group + "/resources", params={"api-version": "2021-04-01"}
        )
        assert resources.status_code == 200, resources.text
        observed = [
            r for r in resources.json()["value"] if r.get("tags", {}).get("provenance") == "fixture"
        ]
        assert len(observed) == 8
        assert all(r["tags"]["ownerObjectId"] in owner_ids for r in observed)
        replay_preview = await preview(database, request, datetime.now(UTC))
        replay_run = await submit_run(
            database, replay_preview.id, replay_preview.digest, datetime.now(UTC)
        )
        replay = await drive(database, replay_run.id, limit=300)
        assert replay.state == "succeeded", replay.errors
        assert replay.progress["created"] == 0
        secret = (await read("keyvault.secrets")).json()
        assert secret["value"].startswith("synthetic-")
        p = actions["keyvault.secrets"]["properties"]
        caller = await transport(None, ctx, p["identity_ref"])
        secret_path = p["read_path"].rsplit("/", 1)[0]
        rotated = await caller.request(
            "keyvault",
            "PUT",
            secret_path,
            params=p["parameters"],
            json={"value": "synthetic-rotated-test-value"},
        )
        assert rotated.status_code == 200, rotated.text
        assert rotated.json()["id"] != secret["id"]
        versions = await caller.request(
            "keyvault", "GET", secret_path + "/versions", params=p["parameters"]
        )
        assert versions.status_code == 200 and len(versions.json()["value"]) == 2
        manifest = json.dumps(await export_manifest(database, run.id))
        assert secret["value"] not in manifest and "synthetic-rotated-test-value" not in manifest
        assert all("value" not in v for v in versions.json()["value"])


async def device_estate(database, settings, tenant):
    ctx = AdapterContext(database, settings, tenant)
    await get_adapter("identity.skus").apply(
        ctx,
        [
            Mutation(
                operation="provision",
                logical_key="intune",
                payload={
                    "id": "061f9ace-7d42-4136-88ac-31dc755f143f",
                    "sku_part_number": "INTUNE_A",
                    "enabled": 10,
                    "service_plans": [
                        {
                            "servicePlanId": "c1ec4a95-1f05-45b3-a911-aa3fa01094f5",
                            "servicePlanName": "INTUNE_A",
                            "provisioningStatus": "Success",
                        }
                    ],
                },
            )
        ],
    )
    for kind, count in (
        ("users", 4),
        ("directory-devices", 4),
        ("managed-devices", 4),
        ("detected-apps", 2),
        ("device-configurations", 2),
        ("compliance-policies", 2),
        ("compliance-statuses", 4),
        ("configuration-statuses", 4),
        ("software-links", 4),
    ):
        await get_adapter(inventory()["business." + kind]["family"]).apply(
            ctx, values(kind, count, tenant=str(tenant))
        )


async def test_directory_device_owner_and_intune_link_are_public(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        await device_estate(database, settings, tenant)
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            ["Directory.Read.All", "DeviceManagementManagedDevices.Read.All"],
        )
        devices = (await client.get("/v1.0/devices", headers=headers)).json()["value"]
        managed = (
            await client.get("/v1.0/deviceManagement/managedDevices", headers=headers)
        ).json()["value"]
        assert len(devices) == len(managed) == 4
        for observed in managed:
            directory = next(d for d in devices if d["deviceId"] == observed["azureADDeviceId"])
            owners = await client.get(
                f"/v1.0/devices/{directory['id']}/registeredOwners", headers=headers
            )
            assert owners.status_code == 200, owners.text
            assert observed["userId"] in {owner["id"] for owner in owners.json()["value"]}


async def test_observed_compliance_does_not_execute_policy(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        await device_estate(database, settings, tenant)
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            ["DeviceManagementManagedDevices.Read.All", "DeviceManagementConfiguration.Read.All"],
        )
        policies = (
            await client.get("/v1.0/deviceManagement/deviceCompliancePolicies", headers=headers)
        ).json()["value"]
        assert len(policies) == 2
        assignments = await client.get(
            f"/v1.0/deviceManagement/deviceCompliancePolicies/{policies[0]['id']}/assignments",
            headers=headers,
        )
        assert assignments.status_code == 200 and assignments.json()["value"] == []
        devices = (
            await client.get("/v1.0/deviceManagement/managedDevices", headers=headers)
        ).json()["value"]
        assert {d["complianceState"] for d in devices} == {"compliant", "noncompliant"}
