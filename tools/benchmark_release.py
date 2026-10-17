"""Measure actual local APIs, normal projection and exact concurrent page recovery."""

import argparse
import json
import math
import os
import re
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Lock
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg

from tools.qualification_profile import profile_for
from tools.release_report import SCALE_TARGETS, source_fingerprint
from tools.scale_dataset import (
    BASE,
    ExactCoverage,
    domain,
    iter_directory_batches,
    iter_evidence_batches,
    timestamp,
)
from tools.verify_release import ROOT, capture, prepare_suite, run, save


def validate_profile(value, fingerprint):
    assert value["name"] in SCALE_TARGETS
    users, records = SCALE_TARGETS[value["name"]]
    assert value["source_fingerprint"] == fingerprint
    assert value["status"] == "complete" and value["passed"] is True
    assert type(value["users"]) is int and value["users"] == users
    assert type(value["records"]) is int and value["records"] == records
    assert value["imported"] == {"users": users, "records": records}
    assert value["published"] == records
    assert value["worker_restart"] is True and value["api_restart"] is True
    span = value["restart_boundary"]
    assert type(span["started_ns"]) is int and type(span["completed_ns"]) is int
    assert 0 < span["started_ns"] <= span["completed_ns"]
    reads = value["reads"]
    assert len(reads) == 4
    assert {(r["family"], r["caller"]) for r in reads} == {
        (family, caller) for family in ("user", "signIn") for caller in (0, 1)
    }
    for family, count in (("user", users), ("signIn", records)):
        pair = [r for r in reads if r["family"] == family]
        assert len({r["id_sha256_xor"] for r in pair}) == 1
        for read in pair:
            assert read["count"] == count and read["pages"] > 0
            assert re.fullmatch("[0-9a-f]{64}", read["id_sha256_xor"])
            assert read["seconds"] > 0 and read["p95_seconds"] >= 0
            restart = read["restart"]
            assert all(
                type(restart[key]) is int
                for key in ("captured_ns", "resumed_ns", "pages_before", "pages_after")
            )
            assert 0 < restart["captured_ns"] <= span["started_ns"]
            assert span["completed_ns"] <= restart["resumed_ns"]
            assert restart["pages_before"] == 1 and restart["pages_after"] >= 1
            assert read["pages"] == restart["pages_before"] + restart["pages_after"]


def require_page_url(profile, url):
    parsed = urlsplit(url)
    assert not parsed.fragment
    if parsed.scheme:
        profile.require_local_origin(f"{parsed.scheme}://{parsed.netloc}")
    else:
        assert not parsed.netloc and url.startswith("/") and not url.startswith("//")


class RestartBoundary:
    """Pause every reader with an issued continuation before restarting an API."""

    def __init__(self, report, restart, checkpoint, *, timeout=600):
        self.report, self.restart, self.checkpoint = report, restart, checkpoint
        self.lock = Lock()
        self.arrivals = {}
        self.barrier = Barrier(4, action=self._restart, timeout=timeout)

    def _restart(self):
        assert set(self.arrivals) == {
            (family, caller) for family in ("user", "signIn") for caller in (0, 1)
        }
        self.report["restart_boundary"] = {"started_ns": time.perf_counter_ns()}
        self.restart()
        self.report["restart_boundary"]["completed_ns"] = time.perf_counter_ns()
        self.report["api_restart"] = True
        self.checkpoint()

    def capture(self, family, caller, continuation):
        assert continuation, "Restart coverage requires an outstanding continuation"
        key = family, caller
        with self.lock:
            assert key not in self.arrivals
            captured = self.arrivals[key] = time.perf_counter_ns()
        self.barrier.wait()
        return {
            "captured_ns": captured,
            "resumed_ns": time.perf_counter_ns(),
            "pages_before": 1,
        }


class Runtime:
    def __init__(self):
        self.profile = profile_for("m5")
        assert self.profile.release, "Scale only runs in the owned M6 profile"
        self.instance = ROOT / self.profile.runtime_dir / "instance"
        self.origin = f"https://localhost:{self.profile.https_port}"
        self.profile.require_local_origin(self.origin)
        self.tls = ssl.create_default_context(cafile=str(self.instance / "ca.pem"))
        self.admin = {
            "Authorization": "Bearer " + (self.instance / "admin.token").read_text().strip()
        }
        config = json.loads((self.instance / "native.json").read_text())
        self.database_url = config["database_url"].replace("postgresql+psycopg:", "postgresql:")
        parsed = urlsplit(self.database_url)
        assert parsed.hostname in ("127.0.0.1", "localhost") and parsed.port == 55450
        self.compose = ["docker", "compose", "-f", self.profile.compose]
        from tools.verify_arm import source_fingerprint as runtime_fingerprint

        self.image = json.loads(capture(["docker", "image", "inspect", self.profile.image]))[0]
        assert (
            self.image["Config"]["Labels"]["org.mockazure.source-fingerprint"]
            == runtime_fingerprint()
        )
        for service in ("api", "api2", "worker", "worker2"):
            identifier = capture(self.compose + ["ps", "-q", service])
            actual = json.loads(capture(["docker", "inspect", identifier]))[0]
            assert actual["Image"] == self.image["Id"] and actual["State"]["Running"]

    def client(self):
        return httpx.Client(
            base_url=self.origin,
            verify=self.tls,
            trust_env=False,
            follow_redirects=False,
            timeout=300,
        )

    def request(self, client, method, path, *, admin=True, **kwargs):
        if path.startswith("https:"):
            parsed = urlsplit(path)
            self.profile.require_local_origin(f"{parsed.scheme}://{parsed.netloc}")
        else:
            assert path.startswith("/") and not path.startswith("//")
        if admin:
            assert path.startswith("/_mock/")
            kwargs["headers"] = self.admin
        response = client.request(method, path, **kwargs)
        if response.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"Local scale API returned {response.status_code} for {method} "
                + path.split("?")[0]
            )
        return response.json() if response.content else None

    def stats(self, tenant, records):
        # Read-only instrumentation. All data and jobs are created through public APIs.
        with psycopg.connect(self.database_url) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            published = connection.execute(
                "SELECT count(*) FROM evidence_observations WHERE tenant_id=%s "
                "AND family='signIn' AND occurred_at >= %s AND occurred_at <= %s",
                (tenant, BASE, timestamp(records - 1)),
            ).fetchone()[0]
            jobs = dict(
                connection.execute(
                    "SELECT state,count(*) FROM jobs WHERE tenant_id=%s GROUP BY state",
                    (tenant,),
                ).fetchall()
            )
            size = connection.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        return {"published": published, "jobs": jobs, "database_bytes": size}

    def provision(self, client, name, seed, users):
        tenant = self.request(
            client,
            "POST",
            "/_mock/tenants",
            json={
                "slug": f"scale-{seed}",
                "display_name": f"M6 {name} scale {seed}",
                "domain": domain(seed),
                "licences": ["P2"],
            },
        )["id"]
        base = f"/_mock/tenants/{tenant}"
        self.request(
            client,
            "PATCH",
            base + "/evidence/settings",
            json={"delay_seconds": 0, "retention_days": 3650, "clock_mode": "wall"},
        )
        self.request(
            client,
            "PUT",
            base + "/licensing/skus/84a661c4-e949-4bd2-a560-ed7766fcaf2b",
            json={
                "sku_part_number": "AAD_PREMIUM_P2",
                "status": "Enabled",
                "enabled": users,
                "suspended": 0,
                "warning": 0,
                "allowed_usage_locations": [],
                "service_plans": [
                    {
                        "servicePlanId": "eec0eb4f-6444-4f95-aba0-50c24d67f998",
                        "servicePlanName": "AAD_PREMIUM_P2",
                        "provisioningStatus": "Success",
                        "appliesTo": "User",
                    }
                ],
            },
        )
        permissions = ["User.Read.All", "AuditLog.Read.All"]
        app = self.request(
            client,
            "POST",
            base + "/identity/applications",
            json={
                "display_name": "Scale reader",
                "client_mode": "confidential",
                "required_permissions": [
                    {
                        "resource": "graph",
                        "kind": "application",
                        "permissions": permissions,
                    }
                ],
            },
        )
        credential = self.request(
            client,
            "POST",
            base + "/identity/applications/" + app["client_id"] + "/credentials",
            json={"kind": "secret"},
        )
        self.request(
            client,
            "PUT",
            base + "/identity/grants/" + str(uuid4()),
            json={
                "client_id": app["client_id"],
                "resource": "graph",
                "kind": "application",
                "permissions": permissions,
            },
        )
        private = ROOT / self.profile.runtime_dir / f"scale-{seed}.json"
        from mockazure.files import write_private

        write_private(
            private,
            json.dumps(
                {
                    "tenant_id": tenant,
                    "client_id": app["client_id"],
                    "secret": credential["secret"],
                }
            ).encode(),
        )
        return tenant, app["client_id"], credential["secret"]

    def token(self, tenant, app, secret):
        with self.client() as client:
            return self.request(
                client,
                "POST",
                f"/{tenant}/oauth2/v2.0/token",
                admin=False,
                data={
                    "grant_type": "client_credentials",
                    "client_id": app,
                    "client_secret": secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )["access_token"]

    def read(self, family, caller, token, report, boundary):
        coverage = ExactCoverage(
            family, seed=report["seed"], users=report["users"], records=report["records"]
        )
        path = "/v1.0/users" if family == "user" else "/v1.0/auditLogs/signIns"
        params = (
            {"$top": min(999, max(1, report["users"] // 2))}
            if family == "user"
            else {
                "$top": 500,
                "$filter": f"createdDateTime ge {timestamp(0)} and "
                f"createdDateTime le {timestamp(report['records'] - 1)}",
            }
        )
        latencies, retries, pages = [], 0, 0
        started = time.perf_counter()
        with self.client() as client:
            while path:
                require_page_url(self.profile, path)
                for attempt in range(20):
                    before = time.perf_counter()
                    try:
                        response = client.get(
                            path, params=params, headers={"Authorization": "Bearer " + token}
                        )
                    except httpx.TransportError:
                        if attempt == 19:
                            raise
                        retries += 1
                        time.sleep(1)
                        continue
                    latencies.append(time.perf_counter() - before)
                    if response.status_code in (429, 502, 503):
                        retries += 1
                        time.sleep(min(60, max(1, int(response.headers.get("Retry-After", "1")))))
                        continue
                    break
                assert response.status_code == 200, f"Read returned {response.status_code}"
                body = response.json()
                for row in body["value"]:
                    coverage.add(row)
                pages += 1
                path, params = body.get("@odata.nextLink"), None
                if pages == 1:
                    restart = boundary.capture(family, caller, path)
        ordered = sorted(latencies)
        return {
            **coverage.finish(),
            "family": family,
            "caller": caller,
            "pages": pages,
            "restart": {**restart, "pages_after": pages - 1},
            "seconds": time.perf_counter() - started,
            "retries": retries,
            "p50_seconds": ordered[math.ceil(len(ordered) * 0.5) - 1],
            "p95_seconds": ordered[math.ceil(len(ordered) * 0.95) - 1],
            "max_seconds": max(ordered),
        }


def run_profile(name, runtime, path, *, seed, projection_timeout):
    users, records = SCALE_TARGETS[name]
    report = {
        "name": name,
        "seed": seed,
        "users": users,
        "records": records,
        "source_fingerprint": source_fingerprint(),
        "started_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "passed": False,
        "imported": {"users": 0, "records": 0},
        "published": 0,
        "worker_restart": False,
        "api_restart": False,
        "reads": [],
        "configuration": {
            "profile": "m6-arm",
            "api_replicas": 2,
            "workers": 2,
            "batch_size": 1000,
            "postgres_major": 18,
            "image_id": runtime.image["Id"],
            "runtime_fingerprint": runtime.image["Config"]["Labels"][
                "org.mockazure.source-fingerprint"
            ],
            "docker": json.loads(
                capture(["docker", "info", "--format", '{"cpus":{{.NCPU}},"memory":{{.MemTotal}}}'])
            ),
        },
    }

    def checkpoint():
        save(path, report)

    checkpoint()
    try:
        with runtime.client() as client:
            tenant, app, secret = runtime.provision(client, name, seed, users)
            report["tenant_id"] = tenant
            base = f"/_mock/tenants/{tenant}"
            for family, batches, endpoint in (
                ("users", iter_directory_batches(seed, users), "/directory/import"),
                ("records", iter_evidence_batches(seed, users, records), "/evidence/import"),
            ):
                started = time.perf_counter()
                if family == "records":
                    pipeline_started = started
                for batch in batches:
                    runtime.request(client, "POST", base + endpoint, json=batch)
                    report["imported"][family] += len(batch[family])
                    if family == "records" and not report["worker_restart"]:
                        run(
                            runtime.compose + ["kill", "-s", "SIGKILL", "worker"],
                            "scale-worker-kill",
                        )
                        run(runtime.compose + ["start", "worker"], "scale-worker-start")
                        report["worker_restart"] = True
                    checkpoint()
                report[family + "_import_seconds"] = time.perf_counter() - started
                checkpoint()
            deadline = time.monotonic() + projection_timeout
            last_print = 0
            while True:
                stats = runtime.stats(tenant, records)
                report.update(stats)
                checkpoint()
                if time.monotonic() - last_print >= 30:
                    print(
                        f"{name}: imported {records}; published {stats['published']}; "
                        f"jobs {stats['jobs']}",
                        flush=True,
                    )
                    last_print = time.monotonic()
                if stats["published"] == records:
                    break
                if stats["published"] > records or stats["jobs"].get("failed", 0):
                    raise RuntimeError("Projection produced duplicate or failed work")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Normal projection did not reach the declared target")
                time.sleep(5)
            report["pipeline_seconds"] = time.perf_counter() - pipeline_started
            report["published_records_per_second"] = records / report["pipeline_seconds"]
            token = runtime.token(tenant, app, secret)
            boundary = RestartBoundary(
                report,
                lambda: run(runtime.compose + ["restart", "api"], "scale-api-restart"),
                checkpoint,
            )
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [
                    pool.submit(runtime.read, family, caller, token, report, boundary)
                    for family in ("user", "signIn")
                    for caller in (0, 1)
                ]
                for future in futures:
                    report["reads"].append(future.result())
            # Recheck the published cardinality after recovery and concurrent traversals.
            assert runtime.stats(tenant, records)["published"] == records
            report.update(
                status="complete", passed=True, completed_at=datetime.now(UTC).isoformat()
            )
            validate_profile(report, source_fingerprint())
    except Exception as error:
        report.update(status="failed", passed=False, error_type=type(error).__name__)
        checkpoint()
        raise
    checkpoint()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=SCALE_TARGETS, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--projection-timeout", type=int, default=1800)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    os.environ["MOCKAZURE_QUALIFICATION_PROFILE"] = "release"
    if args.prepare:
        prepare_suite("m5")
    path = ROOT / "work/release" / f"scale-{args.profile}-{args.seed}.json"
    assert not path.exists(), "Keep prior measurements; use a new seed for a new run"
    result = run_profile(
        args.profile, Runtime(), path, seed=args.seed, projection_timeout=args.projection_timeout
    )
    print(
        json.dumps(
            {
                "name": result["name"],
                "passed": result["passed"],
                "published_records_per_second": result["published_records_per_second"],
            }
        )
    )


if __name__ == "__main__":
    main()
