"""Exact local cloud scale/recovery measurements; no production-service throughput claim."""

import argparse
import hashlib
import json
import re
import ssl
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Lock
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import msal
from azure.core.credentials import AzureNamedKeyCredential
from azure.core.pipeline.transport import RequestsTransport
from azure.data.tables import TableClient
from azure.storage.blob import BlobServiceClient
from azure.storage.fileshare import ShareServiceClient
from azure.storage.queue import QueueClient

from integrations.python.arm import LocalSession
from tools.benchmark_coverage import wait_for_replicas
from tools.verify_cloud_workflows import source_identity
from tools.verify_default import main as smoke


class RequestMetrics:
    """Bounded counters only: no URLs, credentials or response bodies are retained."""

    def __init__(self):
        self.lock = Lock()
        self.channels = Counter()
        self.statuses = Counter()
        self.transport_failures = 0

    def measure(self, channel, operation):
        status = None
        try:
            response = operation()
            status = response.status_code
            return response
        finally:
            with self.lock:
                self.channels[channel] += 1
                if status is None:
                    self.transport_failures += 1
                else:
                    self.statuses[str(status)] += 1

    def snapshot(self):
        with self.lock:
            return {
                "scope": "workload-http-transports",
                "attempts": sum(self.channels.values()),
                "responses": sum(self.statuses.values()),
                "transportFailures": self.transport_failures,
                "httpErrorResponses": sum(v for k, v in self.statuses.items() if int(k) >= 400),
                "serverErrorResponses": sum(v for k, v in self.statuses.items() if int(k) >= 500),
                "statusCounts": dict(self.statuses),
                "channelAttempts": dict(self.channels),
            }


_request_metrics = None


def measured(channel, operation):
    return operation() if _request_metrics is None else _request_metrics.measure(channel, operation)


class MeasuredSession(LocalSession):
    def send(self, request, **kwargs):
        return measured("sdk", lambda: super(MeasuredSession, self).send(request, **kwargs))


def measured_request(client, method, path, **kwargs):
    return measured("rest", lambda: client.request(method, path, **kwargs))


def dataset_counts(records):
    if type(records) is not int or not 300 <= records <= 100000:
        raise ValueError("Scale records must be between 300 and 100000")
    inventory, diagnostic = min(100, records // 10), min(1000, records // 10)
    return {
        "inventory": inventory,
        "diagnostic": diagnostic,
        "table": records - inventory - diagnostic,
    }


def validate_scale_report(report, commit, fingerprint, *, full=True):
    try:
        assert report["passed"] is True
        assert report["sourceCommit"] == commit and report["sourceFingerprint"] == fingerprint
        assert re.fullmatch("[0-9a-f]{40}", commit) and re.fullmatch("[0-9a-f]{64}", fingerprint)
        assert report["records"] == 100000 if full else report["records"] >= 300
        assert report["binaryBytes"] == 268435456 if full else report["binaryBytes"] > 0
        assert report["apiReplicas"] == 2
        expected = dataset_counts(report["records"])
        assert set(report["datasets"]) == set(expected)
        for name, count in expected.items():
            assert (
                report["datasets"][name]["expected"] == report["datasets"][name]["actual"] == count
            )
        assert len(report["readers"]) == 8 and {r["id"] for r in report["readers"]} == set(range(8))
        assert all(
            r["exactIds"] is True and r["crossTenantSentinelHits"] == 0 for r in report["readers"]
        )
        for name, count in expected.items():
            digest = report["datasets"][name]["expectedIdsSha256"]
            assert re.fullmatch("[0-9a-f]{64}", digest)
            assert all(
                reader[name + "Count"] == count and reader[name + "IdsSha256"] == digest
                for reader in report["readers"]
            )
        assert len(report["restarts"]) == 2 and all(
            r["completed"] is True for r in report["restarts"]
        )
        binary = report["binary"]
        assert binary["bytes"] == report["binaryBytes"] and re.fullmatch(
            "[0-9a-f]{64}", binary["uploadedSha256"]
        )
        assert binary["uploadedSha256"] == binary["downloadedSha256"]
        assert report["lostAcknowledgedWrites"] == 0 and report["acknowledgedWrites"] >= 32
        assert all(
            report[k] is True
            for k in (
                "tenantIsolation",
                "queueContention",
                "fileRanges",
                "diagnosticFanout",
                "queryJoins",
            )
        )
        requests = report["requests"]
        assert requests["scope"] == "workload-http-transports"
        statuses, channels = requests["statusCounts"], requests["channelAttempts"]
        assert set(channels) == {"rest", "sdk"}
        assert all(type(v) is int and v > 0 for v in channels.values())
        assert all(re.fullmatch(r"[1-5]\d\d", k) for k in statuses)
        assert all(type(v) is int and v > 0 for v in statuses.values())
        assert requests["attempts"] == sum(channels.values())
        assert requests["responses"] == sum(statuses.values())
        assert requests["transportFailures"] == requests["serverErrorResponses"] == 0
        assert requests["attempts"] == requests["responses"] + requests["transportFailures"]
        assert requests["httpErrorResponses"] == sum(
            v for k, v in statuses.items() if int(k) >= 400
        )
        assert requests["serverErrorResponses"] == sum(
            v for k, v in statuses.items() if int(k) >= 500
        )
        assert statuses.get("403", 0) >= 1
        observation = report["observations"]
        assert type(observation["databaseBytes"]) is int and observation["databaseBytes"] > 0
        validate_memory_observations(observation, report["replicas"])
    except (AssertionError, KeyError, TypeError, ValueError) as error:
        raise ValueError("Incomplete, stale or reduced cloud scale evidence") from error


# Fixed local qualification budgets, deliberately below a whole 256 MiB payload.
MEMORY_ALLOWANCE = {"perProcessKiB": 256 * 1024, "transferGrowthKiB": 64 * 1024}
MEMORY_STAGES = (
    "start",
    "beforeUpload",
    "afterUpload",
    "replicated",
    "beforeRestart1",
    "afterRestart1",
    "beforeRestart2",
    "afterRestart2",
    "beforeDownload",
    "afterDownload",
    "finish",
)


def validate_memory_observations(observation, replicas):
    assert len(replicas) == len(set(replicas)) == 2
    assert observation["memoryAllowance"] == MEMORY_ALLOWANCE
    samples = observation["samples"]
    assert [sample["stage"] for sample in samples] == list(MEMORY_STAGES)
    peaks, by_stage = {}, {}
    for index, sample in enumerate(samples):
        assert datetime.fromisoformat(sample["at"]).tzinfo is not None
        processes = sample["processes"]
        expected = {replicas[0]} if index < 3 else set(replicas)
        assert {p["container"] for p in processes} == expected and len(processes) == len(expected)
        by_stage[sample["stage"]] = {p["container"]: p for p in processes}
        for process in processes:
            rss, peak = process["rssKiB"], process["highWaterKiB"]
            assert type(rss) is int and type(peak) is int
            assert 0 < rss <= peak <= MEMORY_ALLOWANCE["perProcessKiB"]
            generation = process["generation"]
            assert generation.startswith(process["container"] + ":")
            assert generation.rsplit(":", 1)[1].isdigit()
            # Linux proc RSS accounting is approximate; preserve the maximum of
            # every observed lifetime high-water instead of assuming monotonic reads.
            peaks[generation] = max(peak, peaks.get(generation, 0))
            by_stage[sample["stage"]][process["container"]] = {
                **process,
                "highWaterKiB": peaks[generation],
            }
    assert observation["processHighWaterKiB"] == peaks
    # Every process lifetime is preserved; only the two explicit restarts may
    # replace a process. A crash/replacement between checkpoints fails closed.
    for previous, current in zip(samples, samples[1:]):
        old, new = by_stage[previous["stage"]], by_stage[current["stage"]]
        restarting = current["stage"] in {"afterRestart1", "afterRestart2"}
        for container in old.keys() & new.keys():
            changed = old[container]["generation"] != new[container]["generation"]
            assert changed == restarting
    for transfer in ("Upload", "Download"):
        before, after = by_stage["before" + transfer], by_stage["after" + transfer]
        for container in before:
            growth = after[container]["highWaterKiB"] - before[container]["highWaterKiB"]
            assert 0 <= growth <= MEMORY_ALLOWANCE["transferGrowthKiB"]


class MemoryObserver:
    """Kernel VmHWM retains transient peaks without sampling/polling blind spots.

    Snapshot before every restart, so each old process's lifetime peak survives.
    PID 1 is the single MockAzure API process (all seven listeners share it).
    The docker-exec helper and healthcheck processes are intentionally excluded.
    """

    def __init__(self, command, compose, report):
        self.command, self.compose = command, compose
        self.observation = report.setdefault("observations", {})
        self.observation.update(
            samples=[],
            processHighWaterKiB={},
            memoryAllowance=dict(MEMORY_ALLOWANCE),
            highWaterDefinition=(
                "API PID 1 kernel VmHWM, retained separately for every container/process "
                "start time; not total container memory"
            ),
        )

    def capture(self, stage):
        containers = self.command(self.compose + ["ps", "-q", "api"], capture=True).splitlines()
        assert containers, "No API process available for memory observation"
        code = (
            "import json,pathlib; p=pathlib.Path('/proc/1'); "
            "cmd=(p/'cmdline').read_bytes(); assert b'mockazure' in cmd and b'serve' in cmd; "
            "v=dict(line.split(':',1) for line in (p/'status').read_text().splitlines()); "
            "print(json.dumps({'rssKiB':int(v['VmRSS'].split()[0]), "
            "'highWaterKiB':int(v['VmHWM'].split()[0]), "
            "'startTicks':(p/'stat').read_text().rsplit(')',1)[1].split()[19]}))"
        )
        processes = []
        for container in containers:
            value = json.loads(
                self.command(["docker", "exec", container, "python", "-c", code], capture=True)
            )
            generation = container + ":" + value.pop("startTicks")
            value.update(container=container, generation=generation)
            processes.append(value)
            peaks = self.observation["processHighWaterKiB"]
            peaks[generation] = max(peaks.get(generation, 0), value["highWaterKiB"])
        self.observation["samples"].append(
            {"stage": stage, "at": datetime.now(UTC).isoformat(), "processes": processes}
        )


def digest_ids(values):
    return hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()


@contextmanager
def sdk(fixture, service):
    endpoint = fixture["endpoints"][service]
    with MeasuredSession("https://" + urlsplit(endpoint).netloc, fixture["ca"]) as session:
        transport = RequestsTransport(
            session=session, session_owner=False, connection_verify=fixture["ca"]
        )
        common = {"transport": transport, "retry_total": 0}
        if service == "table":
            client = TableClient(
                endpoint,
                "ScaleEvidence",
                credential=AzureNamedKeyCredential(fixture["accountName"], fixture["key"]),
                api_version="2019-02-02",
                **common,
            )
        elif service == "blob":
            client = BlobServiceClient(
                endpoint,
                credential=fixture["key"],
                api_version="2025-11-05",
                max_single_put_size=268435456,
                **common,
            )
        elif service == "file":
            client = ShareServiceClient(
                endpoint, credential=fixture["key"], api_version="2025-11-05", **common
            )
        else:
            client = QueueClient(
                endpoint,
                "scale-contention",
                credential=fixture["key"],
                api_version="2026-02-06",
                **common,
            )
        with client:
            yield client


def request(client, headers, method, path, body=None, status=(200,)):
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Only paths on the configured local origin are permitted")
    response = measured_request(client, method, path, headers=headers, json=body, timeout=180)
    assert response.status_code in status, (method, path, response.status_code, response.text[:240])
    return response.json() if response.content else None


def provision(client, admin, tenant, ca, label):
    base = "/_mock/tenants/" + tenant["id"]

    def control(method, path, body=None, status=(200,)):
        return request(client, admin, method, base + path, body, status)

    app = control(
        "POST",
        "/identity/applications",
        {
            "display_name": "Cloud scale " + label,
            "client_mode": "confidential",
            "required_permissions": [
                {
                    "resource": "graph",
                    "kind": "application",
                    "permissions": ["Application.Read.All"],
                }
            ],
        },
        (201,),
    )
    secret = control(
        "POST", f"/identity/applications/{app['client_id']}/credentials", {"kind": "secret"}, (201,)
    )["secret"]
    control(
        "PUT",
        "/identity/grants/" + str(uuid4()),
        {
            "client_id": app["client_id"],
            "resource": "graph",
            "kind": "application",
            "permissions": ["Application.Read.All"],
        },
    )
    origin = str(client.base_url).rstrip("/")
    with MeasuredSession(origin, ca) as session:
        auth = msal.ConfidentialClientApplication(
            app["client_id"],
            client_credential=secret,
            authority=origin + "/" + tenant["id"],
            validate_authority=False,
            instance_discovery=False,
            http_client=session,
        )
        tokens = {
            name: auth.acquire_token_for_client([audience + "/.default"])["access_token"]
            for name, audience in (
                ("arm", "https://management.azure.com"),
                ("logs", "https://api.loganalytics.io"),
                ("graph", "https://graph.microsoft.com"),
            )
        }
    principal = request(
        client,
        {"Authorization": "Bearer " + tokens["graph"]},
        "GET",
        "/v1.0/servicePrincipals?$filter=appId eq '" + app["client_id"] + "'",
    )["value"][0]["id"]
    subscription = control("POST", "/subscriptions", {"display_name": label}, (201,))["id"]
    scope = "/subscriptions/" + subscription
    grant = str(uuid4())
    control(
        "PUT",
        "/subscriptions/" + subscription + "/role-assignments/" + grant,
        {
            "principal_id": principal,
            "principal_type": "ServicePrincipal",
            "scope": scope,
            "role": "Owner",
        },
    )
    bearer = {"Authorization": "Bearer " + tokens["arm"]}
    for namespace in ("Microsoft.Storage", "Microsoft.OperationalInsights"):
        request(
            client,
            bearer,
            "POST",
            scope + "/providers/" + namespace + "/register?api-version=2021-04-01",
        )
    group = scope + "/resourceGroups/scale"
    request(
        client, bearer, "PUT", group + "?api-version=2021-04-01", {"location": "eastus"}, (200, 201)
    )
    account_name = "scale" + label
    account = group + "/providers/Microsoft.Storage/storageAccounts/" + account_name
    resource = request(
        client,
        bearer,
        "PUT",
        account + "?api-version=2025-06-01",
        {
            "location": "eastus",
            "kind": "StorageV2",
            "sku": {"name": "Standard_LRS"},
            "properties": {},
        },
        (200, 201),
    )
    key = request(client, bearer, "POST", account + "/listKeys?api-version=2025-06-01")["keys"][0][
        "value"
    ]
    workspace = group + "/providers/Microsoft.OperationalInsights/workspaces/evidence"
    ws = request(
        client,
        bearer,
        "PUT",
        workspace + "?api-version=2025-02-01",
        {"location": "eastus", "properties": {"sku": {"name": "PerGB2018"}, "retentionInDays": 30}},
        (200, 201),
    )
    return {
        "tenant": tenant["id"],
        "base": base,
        "subscription": subscription,
        "group": group,
        "account": account,
        "accountName": account_name,
        "endpoints": resource["properties"]["primaryEndpoints"],
        "key": key,
        "arm": bearer,
        "logs": {"Authorization": "Bearer " + tokens["logs"]},
        "workspace": ws["properties"]["customerId"],
        "workspaceResource": workspace,
        "logsOrigin": f"https://localhost:{urlsplit(origin).port + 6}",
        "ca": ca,
        "grant": grant,
    }


def log_rows(fixture, query):
    with httpx.Client(
        base_url=fixture["logsOrigin"],
        verify=ssl.create_default_context(cafile=fixture["ca"]),
        trust_env=False,
        timeout=180,
    ) as client:
        result = request(
            client,
            fixture["logs"],
            "POST",
            f"/v1/workspaces/{fixture['workspace']}/query",
            {"query": query},
        )
        return result["tables"][0]["rows"]


def inventory(client, fixture):
    body = {
        "subscriptions": [fixture["subscription"]],
        "query": (
            "Resources | where tags.cohort == 'cloud-scale' "
            "| project id=tolower(id) | order by id asc"
        ),
        "options": {"$top": 50},
    }
    ids = []
    while True:
        result = request(
            client,
            fixture["arm"],
            "POST",
            "/providers/Microsoft.ResourceGraph/resources?api-version=2024-04-01",
            body,
        )
        ids.extend(row["id"] for row in result["data"])
        if "$skipToken" not in result:
            return ids
        body["options"]["$skipToken"] = result["$skipToken"]


def qualify(
    directory,
    command,
    compose,
    client,
    headers,
    tenant,
    check,
    *,
    records,
    binary_bytes,
    readers,
    report,
):
    counts = dataset_counts(records)
    assert readers == 8 and 0 < binary_bytes <= 268435456
    memory = MemoryObserver(command, compose, report)
    memory.capture("start")
    ca = str(directory / ".runtime/instance/ca.pem")
    primary = provision(client, headers, tenant, ca, "primary")
    other = request(
        client,
        headers,
        "POST",
        "/_mock/tenants",
        {
            "slug": "cloud-scale-other",
            "display_name": "Cloud scale isolation",
            "domain": "cloud-scale-other.test",
            "licences": ["E5"],
        },
        (201,),
    )
    secondary = provision(client, headers, other, ca, "foreign")
    report.update(
        records=records,
        binaryBytes=binary_bytes,
        apiReplicas=2,
        datasets={},
        readers=[],
        restarts=[],
    )
    expected_inventory = set()
    for i in range(counts["inventory"]):
        path = primary["group"] + f"/providers/Microsoft.Storage/storageAccounts/inventory{i:05}"
        request(
            client,
            primary["arm"],
            "PUT",
            path + "?api-version=2025-06-01",
            {
                "location": "eastus",
                "kind": "StorageV2",
                "sku": {"name": "Standard_LRS"},
                "tags": {"cohort": "cloud-scale"},
            },
            (200, 201),
        )
        expected_inventory.add(path.lower())
    with sdk(primary, "table") as table:
        table.create_table()
        for start in range(0, counts["table"], 100):
            table.submit_transaction(
                [
                    ("create", {"PartitionKey": "dataset", "RowKey": f"row{i:06}", "Ordinal": i})
                    for i in range(start, min(start + 100, counts["table"]))
                ]
            )
    with sdk(secondary, "table") as table:
        table.create_table()
        table.create_entity({"PartitionKey": "dataset", "RowKey": "FOREIGN_SENTINEL"})
    with sdk(primary, "blob") as blobs, sdk(secondary, "blob") as foreign:
        blobs.create_container("evidence")
        foreign.create_container("evidence")
        foreign.get_blob_client("evidence", "FOREIGN_SENTINEL").upload_blob(b"foreign-only-bytes")
        source = directory / "scale-content.bin"
        digest = hashlib.sha256()
        block = bytes(range(256)) * 4096
        with source.open("wb") as stream:
            for offset in range(0, binary_bytes, len(block)):
                part = block[: min(len(block), binary_bytes - offset)]
                stream.write(part)
                digest.update(part)
        memory.capture("beforeUpload")
        with source.open("rb") as stream:
            blobs.get_blob_client("evidence", "binary").upload_blob(
                stream, length=binary_bytes, max_concurrency=1
            )
        memory.capture("afterUpload")
        setting = (
            primary["account"]
            + "/blobServices/default/providers/Microsoft.Insights/diagnosticSettings/scale"
        )
        request(
            client,
            primary["arm"],
            "PUT",
            setting + "?api-version=2021-05-01-preview",
            {
                "properties": {
                    "workspaceId": primary["workspaceResource"],
                    "storageAccountId": primary["account"],
                    "logAnalyticsDestinationType": "Dedicated",
                    "logs": [{"category": "StorageWrite", "enabled": True}],
                }
            },
        )
        for i in range(counts["diagnostic"]):
            blobs.get_blob_client("evidence", f"event{i:06}").upload_blob(b"native-event")
    report["binary"] = {"uploadedSha256": digest.hexdigest(), "bytes": binary_bytes}
    check("cloud_scale_public_fixture_written")

    def sql(query):
        return command(
            compose
            + [
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "mockazure",
                "-d",
                "mockazure",
                "-At",
                "-c",
                query,
            ],
            capture=True,
        ).strip()

    # SQL observes persisted publication identities only; every counted record was
    # created by public APIs/native clients, never by direct database insertion.
    source_ids = set(
        sql(
            "SELECT DISTINCT source_id FROM monitor_diagnostic_deliveries WHERE tenant_id='"
            + primary["tenant"]
            + "' AND event->>'operation'='PutBlob';"
        ).splitlines()
    )
    assert len(source_ids) == counts["diagnostic"]
    report["diagnosticSourceIdsSha256"] = digest_ids(source_ids)
    query = "StorageBlobLogs | where OperationName == 'PutBlob' | project CorrelationId"
    for _ in range(600):
        observed = log_rows(primary, query)
        if len(observed) == counts["diagnostic"]:
            break
        time.sleep(1)
    assert {row[0] for row in observed} == source_ids
    check("cloud_scale_native_diagnostics_delivered")
    report["datasets"] = {name: {"expected": count} for name, count in counts.items()}
    # Actual dataset values are checked below, independently of expected counts.
    proxy = directory / "deploy/haproxy.cfg"
    value = proxy.read_text(encoding="utf-8")
    for port in range(8443, 8450):
        old = f"    server api api:{port} check resolvers docker init-addr libc,none"
        assert old in value
        value = value.replace(
            old,
            "    balance roundrobin\n"
            f"    server-template api 1-2 api:{port} check resolvers docker init-addr libc,none",
        )
    proxy.write_text(value, encoding="utf-8")
    command(compose + ["up", "-d", "--no-build", "--scale", "api=2", "--wait"], timeout=300)
    command(compose + ["restart", "ingress"])
    replicas = command(compose + ["ps", "-q", "api"], capture=True).splitlines()
    assert len(replicas) == 2
    first = report["observations"]["samples"][0]["processes"][0]["container"]
    replicas.sort(key=lambda container: container != first)
    report["replicas"] = replicas
    memory.capture("replicated")

    def restart():
        marker = {"started": datetime.now(UTC).isoformat(), "completed": False}
        report["restarts"].append(marker)
        memory.capture("beforeRestart" + str(len(report["restarts"])))
        command(compose + ["restart", "api", "worker"], timeout=300)
        wait_for_replicas(command, client, replicas)
        memory.capture("afterRestart" + str(len(report["restarts"])))
        marker.update(completed=True, finished=datetime.now(UTC).isoformat())
        check("cloud_scale_restart_" + str(len(report["restarts"])))

    barrier = Barrier(9, action=restart)
    expected_table = {f"row{i:06}" for i in range(counts["table"])}
    for name, identifiers in (
        ("table", expected_table),
        ("inventory", expected_inventory),
        ("diagnostic", source_ids),
    ):
        report["datasets"][name]["expectedIdsSha256"] = digest_ids(identifiers)

    def read(index):
        started = time.monotonic()
        seen = []
        with sdk(primary, "table") as table:
            for number, page in enumerate(
                table.query_entities(
                    "PartitionKey eq 'dataset'", select=["RowKey"], results_per_page=1000
                ).by_page()
            ):
                seen.extend(row["RowKey"] for row in page)
                if number == 0:
                    barrier.wait(timeout=900)
        assert len(seen) == len(set(seen)) and set(seen) == expected_table
        with httpx.Client(
            base_url=str(client.base_url),
            verify=ssl.create_default_context(cafile=ca),
            trust_env=False,
            timeout=180,
        ) as isolated:
            ids = inventory(isolated, primary)
        assert len(ids) == len(set(ids)) and set(ids) == expected_inventory
        logs = log_rows(primary, query)
        assert len(logs) == len(source_ids) and {r[0] for r in logs} == source_ids
        barrier.wait(timeout=900)
        return {
            "id": index,
            "exactIds": True,
            "tableCount": len(seen),
            "inventoryCount": len(ids),
            "diagnosticCount": len(logs),
            "tableIdsSha256": digest_ids(seen),
            "inventoryIdsSha256": digest_ids(ids),
            "diagnosticIdsSha256": digest_ids(row[0] for row in logs),
            "crossTenantSentinelHits": int("FOREIGN_SENTINEL" in seen),
            "seconds": time.monotonic() - started,
        }

    acknowledged = []

    def write():
        barrier.wait(timeout=900)
        with sdk(primary, "table") as table:
            for i in range(32):
                key = f"writer{i:04}"
                table.create_entity({"PartitionKey": "concurrent", "RowKey": key})
                acknowledged.append(key)
        barrier.wait(timeout=900)

    def guarded(function, *args):
        try:
            return function(*args)
        except BaseException:
            barrier.abort()
            raise

    with ThreadPoolExecutor(max_workers=9) as pool:
        futures = [pool.submit(guarded, read, i) for i in range(readers)]
        writer = pool.submit(guarded, write)
        wait([*futures, writer])
        errors = [f.exception() for f in [*futures, writer] if f.exception()]
        if errors:
            raise next((e for e in errors if not isinstance(e, BrokenBarrierError)), errors[0])
        report["readers"] = [f.result() for f in futures]
        writer.result()
    for name in counts:
        report["datasets"][name]["actual"] = report["readers"][0][name + "Count"]
    with sdk(primary, "table") as table:
        retained = {row["RowKey"] for row in table.query_entities("PartitionKey eq 'concurrent'")}
    report.update(
        acknowledgedWrites=len(acknowledged),
        lostAcknowledgedWrites=len(set(acknowledged) - retained),
    )
    assert retained == set(acknowledged)
    memory.capture("beforeDownload")
    with sdk(primary, "blob") as blobs:
        downloaded, size = hashlib.sha256(), 0
        for chunk in (
            blobs.get_blob_client("evidence", "binary").download_blob(max_concurrency=1).chunks()
        ):
            downloaded.update(chunk)
            size += len(chunk)
        assert size == binary_bytes and downloaded.hexdigest() == digest.hexdigest()
        report["binary"]["downloadedSha256"] = downloaded.hexdigest()
        memory.capture("afterDownload")
        archives = []
        for blob in blobs.get_container_client("insights-logs-storagewrite").list_blobs():
            archives.extend(
                json.loads(
                    blobs.get_blob_client("insights-logs-storagewrite", blob.name)
                    .download_blob()
                    .readall()
                )["records"]
            )
        assert {r["correlationId"] for r in archives} == source_ids and len(archives) == len(
            source_ids
        )
        report["diagnosticFanout"] = True
    joined = log_rows(
        primary,
        "let t=StorageBlobLogs | where OperationName == 'PutBlob' | project CorrelationId; "
        "t | join kind=inner (t) on CorrelationId | count",
    )
    assert joined == [[len(source_ids)]]
    report["queryJoins"] = True
    with httpx.Client(
        base_url=primary["logsOrigin"],
        verify=ssl.create_default_context(cafile=ca),
        trust_env=False,
    ) as logs:
        denied = measured_request(
            logs,
            "POST",
            f"/v1/workspaces/{primary['workspace']}/query",
            headers=secondary["logs"],
            json={"query": query},
        )
        assert denied.status_code == 403
    with sdk(secondary, "table") as table:
        assert {r["RowKey"] for r in table.list_entities()} == {"FOREIGN_SENTINEL"}
    with sdk(secondary, "blob") as blobs:
        assert (
            blobs.get_blob_client("evidence", "FOREIGN_SENTINEL").download_blob().readall()
            == b"foreign-only-bytes"
        )
    report["tenantIsolation"] = True
    exercise_contention(primary)
    report.update(queueContention=True, fileRanges=True)
    report["observations"]["databaseBytes"] = int(
        sql("SELECT pg_database_size(current_database());")
    )
    memory.capture("finish")
    check("cloud_scale_exact_counts_eight_readers_two_restarts")


def exercise_contention(fixture):
    with sdk(fixture, "queue") as queue:
        queue.create_queue()
        for i in range(32):
            queue.send_message("message" + str(i))

    def consume(_):
        values = []
        with sdk(fixture, "queue") as queue:
            while True:
                messages = list(
                    queue.receive_messages(
                        messages_per_page=1, max_messages=1, visibility_timeout=60
                    )
                )
                if not messages:
                    return values
                for message in messages:
                    values.append(message.content)
                    queue.delete_message(message.id, message.pop_receipt)

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = [v for batch in pool.map(consume, range(8)) for v in batch]
    assert len(values) == 32 and set(values) == {"message" + str(i) for i in range(32)}
    with sdk(fixture, "file") as files:
        share = files.create_share("range-contention")
        file = share.get_file_client("ranges")
        file.create_file(8 * 65536)

    def write_range(i):
        with sdk(fixture, "file") as files:
            file = files.get_share_client("range-contention").get_file_client("ranges")
            file.upload_range(bytes([i]) * 65536, offset=i * 65536, length=65536)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write_range, range(8)))
    with sdk(fixture, "file") as files:
        observed = (
            files.get_share_client("range-contention")
            .get_file_client("ranges")
            .download_file()
            .readall()
        )
    assert observed == b"".join(bytes([i]) * 65536 for i in range(8))


def main():
    global _request_metrics

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=100000)
    parser.add_argument("--binary-bytes", type=int, default=268435456)
    parser.add_argument("--readers", type=int, default=8)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    dataset_counts(args.records)
    if args.readers != 8 or not 0 < args.binary_bytes <= 268435456:
        parser.error("Eight readers and 1..268435456 binary bytes are required")
    commit, fingerprint = source_identity()
    report = {"sourceCommit": commit, "sourceFingerprint": fingerprint, "passed": False}
    started = time.monotonic()
    metrics = _request_metrics = RequestMetrics()
    try:
        smoke(
            qualify=lambda *values: qualify(
                *values,
                records=args.records,
                binary_bytes=args.binary_bytes,
                readers=args.readers,
                report=report,
            )
        )
        assert source_identity() == (commit, fingerprint)
        report["requests"] = metrics.snapshot()
        report["passed"] = True
        validate_scale_report(
            report,
            commit,
            fingerprint,
            full=args.records == 100000 and args.binary_bytes == 268435456,
        )
    except Exception:
        report["passed"] = False
        raise
    finally:
        report["requests"] = metrics.snapshot()
        _request_metrics = None
        report["elapsedSeconds"] = time.monotonic() - started
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
