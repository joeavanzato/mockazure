"""Original Azure SDK query/settings clients; every transport is origin-pinned."""

import argparse
import json
import time
from importlib.metadata import version
from pathlib import Path

from azure.core.pipeline.transport import RequestsTransport
from azure.mgmt.monitor import MonitorManagementClient
from azure.mgmt.monitor.v2021_05_01_preview.models import DiagnosticSettingsResource, LogSettings
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions
from azure.monitor.query import LogsQueryClient

from integrations.python.arm import LocalSession
from integrations.python.cloud_management import Credential


def run(p, workflow):
    checks = {}
    with LocalSession(p["endpoint"], p["ca_file"]) as session:
        transport = RequestsTransport(session=session, connection_verify=p["ca_file"])
        credential = Credential(p)
        graph = ResourceGraphClient(
            credential,
            base_url=p["endpoint"],
            api_version="2024-04-01",
            credential_scopes=["https://management.azure.com/.default"],
            transport=transport,
        )
        options = QueryRequestOptions(top=1, result_format="objectArray")
        seen = []
        while True:
            result = graph.resources(
                QueryRequest(
                    query=p["inventory_query"],
                    subscriptions=[p["subscription_id"]],
                    options=options,
                )
            )
            seen.extend(row["id"].lower() for row in result.data)
            if not result.skip_token:
                break
            options.skip_token = result.skip_token
        assert sorted(seen) == sorted(p["expected_resources"]) and len(seen) == len(set(seen))
        checks["inventory_paging"] = True
        monitor = MonitorManagementClient(
            credential,
            p["subscription_id"],
            base_url=p["endpoint"],
            credential_scopes=["https://management.azure.com/.default"],
            transport=transport,
        )
        if workflow == "write":
            monitor.diagnostic_settings.create_or_update(
                p["source"],
                p["setting"],
                DiagnosticSettingsResource(
                    workspace_id=p["workspace_resource"],
                    storage_account_id=p["account"],
                    log_analytics_destination_type="Dedicated",
                    logs=[LogSettings(enabled=True, category="StorageWrite")],
                ),
            )
        got = monitor.diagnostic_settings.get(p["source"], p["setting"])
        assert got.workspace_id.lower() == p["workspace_resource"].lower()
        assert got.logs[0].enabled and got.logs[0].category == "StorageWrite"
        checks["diagnostic_settings"] = True
    if workflow == "read":
        with LocalSession(p["logs_endpoint"], p["ca_file"]) as session:
            logs = LogsQueryClient(
                Credential(p | {"token_file": p["logs_token_file"]}),
                endpoint=p["logs_endpoint"],
                audience="https://api.loganalytics.io",
                transport=RequestsTransport(session=session, connection_verify=p["ca_file"]),
            )
            result = logs.query_workspace(p["workspace_id"], p["logs_query"], timespan=None)
            assert [list(row) for row in result.tables[0].rows] == [[p["expected_logs"]]], (
                result.tables[0].rows
            )
            checks["native_logs_query"] = checks["cross_language_reads"] = True
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--workflow", choices=["write", "read"], required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    checks = run(json.loads(args.profile.read_text()), args.workflow)
    args.report.write_text(
        json.dumps(
            {
                "ecosystem": "python",
                "passed": True,
                "assertions": checks,
                "clientVersions": {
                    p: version(p)
                    for p in [
                        "azure-mgmt-resourcegraph",
                        "azure-monitor-query",
                        "azure-mgmt-monitor",
                    ]
                },
                "transport": "sdk",
                "workflow": args.workflow,
                "elapsedSeconds": time.monotonic() - started,
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
