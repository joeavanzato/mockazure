from tests.integration.graph_support import graph_client, headers_for
from tests.integration.intune_support import import_records, license_intune, record


async def test_license_and_distinct_permission_families(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        inventory = await headers_for(
            database, settings, tenant, app, None, {"DeviceManagementManagedDevices.Read.All"}
        )
        config = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"DeviceManagementConfiguration.Read.All"},
            kind="delegated",
        )
        assert (
            await client.get("/v1.0/deviceManagement/managedDevices", headers=inventory)
        ).status_code == 403
        await license_intune(client, tenant)
        result = await import_records(
            client,
            tenant,
            [
                record(
                    "managedDevice",
                    "managed-1",
                    deviceName="Local endpoint",
                    complianceState="unknown",
                )
            ],
        )
        assert result.status_code == 200, result.text
        result = await client.get("/v1.0/deviceManagement/managedDevices", headers=inventory)
        assert result.status_code == 200, result.text
        assert result.json()["value"][0]["deviceName"] == "Local endpoint"
        assert (
            await client.get("/v1.0/deviceManagement/deviceConfigurations", headers=inventory)
        ).status_code == 403
        assert (
            await client.get("/v1.0/deviceManagement/managedDevices", headers=config)
        ).status_code == 403
        assert (
            await client.get("/v1.0/deviceManagement/deviceConfigurations", headers=config)
        ).json()["value"] == []
        await license_intune(client, tenant, enabled=0)
        assert (
            await client.get("/v1.0/deviceManagement/managedDevices/managed-1", headers=inventory)
        ).status_code == 403


async def test_policy_status_paging_relationships_and_observations(database, settings):
    from uuid import uuid4

    from mockazure.security.models import SecurityObject
    from mockazure.workloads.intune import device_observation
    from tests.integration.intune_support import edge

    async with graph_client(database, settings) as (client, tenant, app, user):
        await license_intune(client, tenant)
        object_id, device_id = uuid4(), uuid4()
        async with database.sessions.begin() as session:
            session.add(
                SecurityObject(
                    tenant_id=tenant,
                    id=object_id,
                    kind="device",
                    properties={"deviceId": str(device_id), "accountEnabled": True},
                )
            )
        records = [
            record(
                "managedDevice",
                "managed",
                azureADDeviceId=str(device_id),
                userId=str(user),
                complianceState="unknown",
                operatingSystem="Windows",
            ),
            record("detectedApp", "app", displayName="Evidence app"),
            record("deviceCompliancePolicy", "policy", derived="windows10CompliancePolicy"),
            record(
                "deviceCompliancePolicyAssignment",
                "assignment",
                target={"@odata.type": "#microsoft.graph.allDevicesAssignmentTarget"},
            ),
        ]
        edges = [
            edge("user", str(user), "managedDevices", "managedDevice", "managed"),
            edge("detectedApp", "app", "managedDevices", "managedDevice", "managed"),
            edge(
                "deviceCompliancePolicy",
                "policy",
                "assignments",
                "deviceCompliancePolicyAssignment",
                "assignment",
            ),
        ]
        for i in range(101):
            records.append(
                record("deviceComplianceDeviceStatus", f"status-{i:03}", status="compliant")
            )
            edges.append(
                edge(
                    "deviceCompliancePolicy",
                    "policy",
                    "deviceStatuses",
                    "deviceComplianceDeviceStatus",
                    f"status-{i:03}",
                )
            )
        imported = await import_records(client, tenant, records, edges)
        assert imported.status_code == 200, imported.text
        async with database.sessions() as session:
            observation = await device_observation(session, tenant, device_id)
            assert observation.compliant is None
            assert observation.platform == "Windows"
            assert await device_observation(session, tenant, object_id) is None
        auth = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"DeviceManagementManagedDevices.Read.All", "DeviceManagementConfiguration.Read.All"},
        )
        for path in (
            f"/users/{user}/managedDevices",
            "/deviceManagement/detectedApps/app/managedDevices",
        ):
            result = await client.get("/v1.0" + path, headers=auth)
            assert [v["id"] for v in result.json()["value"]] == ["managed"]
        url = "/v1.0/deviceManagement/deviceCompliancePolicies/policy/deviceStatuses?$top=30"
        ids = []
        while url:
            page = await client.get(url, headers=auth)
            assert page.status_code == 200, page.text
            ids += [v["id"] for v in page.json()["value"]]
            url = page.json().get("@odata.nextLink")
        assert len(set(ids)) == len(ids) == 101
        updated = record(
            "managedDevice",
            "managed",
            azureADDeviceId=str(device_id),
            complianceState="compliant",
            operatingSystem="Windows",
        )
        assert (await import_records(client, tenant, [updated])).status_code == 200
        async with database.sessions() as session:
            assert (await device_observation(session, tenant, device_id)).compliant is True
        duplicate = record("managedDevice", "duplicate", azureADDeviceId=str(device_id))
        assert (await import_records(client, tenant, [duplicate])).status_code == 422
        assert (
            await import_records(
                client, tenant, [], tombstones=[{"kind": "managedDevice", "id": "managed"}]
            )
        ).status_code == 200
        async with database.sessions() as session:
            assert await device_observation(session, tenant, device_id) is None


async def test_snapshot_restart_reset_and_alias_budget(database, settings):
    from tests.integration.intune_support import edge

    async with graph_client(database, settings) as (client, tenant, app, user):
        await license_intune(client, tenant)
        rows = [record("managedDevice", f"m{i}", deviceName="Before") for i in range(3)]
        edges = [
            edge("user", str(user), "managedDevices", "managedDevice", f"m{i}") for i in range(3)
        ]
        assert (await import_records(client, tenant, rows, edges)).status_code == 200
        auth = await headers_for(
            database, settings, tenant, app, None, {"DeviceManagementManagedDevices.Read.All"}
        )
        first = await client.get(
            "/v1.0/deviceManagement/managedDevices?$top=1&$select=id,deviceName", headers=auth
        )
        assert first.status_code == 200, first.text
        link = first.json()["@odata.nextLink"]
        assert (
            await import_records(
                client, tenant, [record("managedDevice", "m1", deviceName="After")]
            )
        ).status_code == 200
        from datetime import UTC, datetime

        from mockazure.workloads.cleanup import cleanup_workloads

        async with database.sessions.begin() as session:
            assert (await cleanup_workloads(session, tenant, datetime.now(UTC), 1000))[
                "deleted"
            ] == 0
        await database.engine.dispose()
        second = await client.get(link, headers=auth)
        assert second.json()["value"] == [
            {"id": "m1", "deviceName": "Before", "@odata.type": "#microsoft.graph.managedDevice"}
        ]
        exported = await client.get(
            f"/_mock/tenants/{tenant}/workloads/intune/export?scope=deviceManagement"
        )
        assert exported.status_code == 200, exported.text
        reset = await client.post(
            f"/_mock/tenants/{tenant}/workloads/intune/reset",
            json={"scope": "deviceManagement", "expected_revision": 2},
        )
        assert reset.status_code == 200, reset.text
        assert (await client.get(link, headers=auth)).status_code == 410
        restored = await client.post(
            f"/_mock/tenants/{tenant}/workloads/intune/import",
            json={**exported.json(), "expected_revision": None},
        )
        assert restored.status_code == 200, restored.text
        assert (await client.get("/v1.0/deviceManagement/managedDevices/m1", headers=auth)).json()[
            "deviceName"
        ] == "After"
        fault = await client.post(
            f"/_mock/tenants/{tenant}/graph/faults",
            json={
                "client_id": str(app),
                "resource": "deviceManagement",
                "kind": "next_read_429",
                "retry_after": 1,
            },
        )
        assert fault.status_code == 201, fault.text
        throttled = await client.get(f"/v1.0/users/{user}/managedDevices", headers=auth)
        assert throttled.status_code == 429, throttled.text
        assert throttled.headers["retry-after"] == "1"
        assert (
            await client.get(f"/v1.0/users/{user}/managedDevices", headers=auth)
        ).status_code == 200
