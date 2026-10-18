"""Original generated Graph SDK collecting licensed local Intune evidence."""

import asyncio
import json
import ssl
import sys
from importlib.metadata import version
from pathlib import Path

import httpx
from graph_directory import graph_client
from graph_security import client_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.device_management.managed_devices.managed_devices_request_builder import (
    ManagedDevicesRequestBuilder,
)


async def collect(builder, page=None):
    page = page or await builder.get()
    rows, pages = list(page.value), 1
    while page.odata_next_link:
        page = await builder.with_url(page.odata_next_link).get()
        rows.extend(page.value)
        pages += 1
    return rows, pages


async def main():
    phase = sys.argv[1]
    config = json.loads(Path("/state/client.json").read_text(encoding="utf-8"))
    token = client_token(
        config,
        config["client_id"],
        json.loads(Path(config["secret_file"]).read_text(encoding="utf-8"))["secret"],
    )
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=config["ca_file"]), trust_env=False, timeout=30
    ) as http:
        graph = graph_client(config, token["access_token"], http)
        management = graph.device_management
        devices = management.managed_devices
        report_path = Path("/state/python-report.json")
        if phase == "prepare":
            first = await devices.get(
                RequestConfiguration(
                    query_parameters=ManagedDevicesRequestBuilder.ManagedDevicesRequestBuilderGetQueryParameters(
                        top=25
                    )
                )
            )
            rows, pages = await collect(devices, first)
            assert len(rows) == 101 and pages == 5
            assert (
                len((await collect(graph.users.by_user_id(config["user_id"]).managed_devices))[0])
                == 101
            )
            assert (
                len(
                    (
                        await collect(
                            management.detected_apps.by_detected_app_id("detected").managed_devices
                        )
                    )[0]
                )
                == 101
            )
            configurations, _ = await collect(management.device_configurations)
            policies, _ = await collect(management.device_compliance_policies)
            assert len(configurations) == len(policies) == 4
            assert all(type(v).__name__ != "DeviceConfiguration" for v in configurations)
            assert all(type(v).__name__ != "DeviceCompliancePolicy" for v in policies)
            statuses = 0
            for row in configurations:
                item = management.device_configurations.by_device_configuration_id(row.id)
                assert (await item.get()).id == row.id
                assert len((await collect(item.assignments))[0]) == 1
                statuses += len((await collect(item.device_statuses))[0])
            for row in policies:
                item = management.device_compliance_policies.by_device_compliance_policy_id(row.id)
                assert (await item.get()).id == row.id
                assert len((await collect(item.assignments))[0]) == 1
                statuses += len((await collect(item.device_statuses))[0])
            assert statuses == 108
            Path("/state/python-page.json").write_text(
                json.dumps({"link": first.odata_next_link}), encoding="utf-8"
            )
            report = {
                "client": "msgraph-sdk",
                "version": version("msgraph-sdk"),
                "managed_devices": 101,
                "pages": pages,
                "platform_policies": 8,
                "statuses": statuses,
            }
        else:
            link = json.loads(Path("/state/python-page.json").read_text(encoding="utf-8"))["link"]
            rows, _ = await collect(devices, await devices.with_url(link).get())
            assert len(rows) == 76 and all(v.device_name == "Before" for v in rows)
            assert (await devices.by_managed_device_id("m050").get()).device_name == "After"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["snapshot_after_restart"] = True
        report_path.write_text(json.dumps(report), encoding="utf-8")


asyncio.run(main())
