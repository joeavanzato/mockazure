"""Exact collection recovery through two local API replicas; no service latency claims."""

import argparse
import hashlib
import json
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from tools.scale_dataset import collection_id, iter_collection_batches, streamed_block
from tools.verify_default import main as smoke


def local_url(client, value):
    origin = urlsplit(str(client.base_url))
    parsed = urlsplit(value)
    assert not parsed.netloc or (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc)
    assert not parsed.fragment and (parsed.netloc or value.startswith("/"))
    return value


def call(client, headers, method, path, body=None, status=200):
    response = client.request(
        method, local_url(client, path), headers=headers, json=body, timeout=180
    )
    assert response.status_code == status, (path, response.status_code, response.text[:200])
    return response.json() if response.content else None


def wait_for_replicas(command, client, replicas, *, attempts=120):
    for _ in range(attempts):
        states = json.loads(command(["docker", "inspect", *replicas], capture=True))
        healthy = len(states) == len(replicas) and all(
            row["State"].get("Running") is True
            and row["State"].get("Health", {}).get("Status") == "healthy"
            for row in states
        )
        if healthy:
            try:
                if client.get("/_mock/ready").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
        time.sleep(0.5)
    raise AssertionError("Local replicas did not recover")


def collection_fixture(client, headers, tenant, *, seed=42, count=101):
    base = "/_mock/tenants/" + tenant["id"]

    def request(method, path, body=None, status=200):
        return call(client, headers, method, base + path, body, status)

    permissions = ["Mail.ReadWrite", "Files.Read.All", "Sites.Read.All", "Directory.Read.All"]
    app = request(
        "POST",
        "/identity/applications",
        {
            "display_name": "Collection scale",
            "client_mode": "confidential",
            "required_permissions": [
                {"resource": "graph", "kind": "application", "permissions": permissions}
            ],
        },
        201,
    )
    secret = request(
        "POST", f"/identity/applications/{app['client_id']}/credentials", {"kind": "secret"}, 201
    )
    request(
        "PUT",
        f"/identity/grants/{uuid4()}",
        {
            "client_id": app["client_id"],
            "resource": "graph",
            "kind": "application",
            "permissions": permissions,
        },
    )
    tokens = client.post(
        "/" + tenant["id"] + "/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": app["client_id"],
            "client_secret": secret["secret"],
            "scope": "https://graph.microsoft.com/.default",
        },
    )
    assert tokens.status_code == 200
    auth = {"Authorization": "Bearer " + tokens.json()["access_token"]}
    user = request(
        "POST",
        "/identity/users",
        {
            "upn": "scale@" + tenant["domain"],
            "display_name": "Scale evidence owner",
            "password": "Local collection qualification password 42!",
        },
        201,
    )
    request("PUT", "/mail/settings", {"exchange_enabled": True, "audit_tier": "standard"})
    request("PUT", "/mail/mailboxes/" + user["id"], {"primary_smtp": user["upn"], "alias": "scale"})
    request(
        "POST",
        "/mail/content/import",
        {
            "mailbox_user_id": user["id"],
            "mode": "content-backed",
            "expected_revision": 0,
            "folders": [{"id": "inbox", "displayName": "Inbox", "well_known_name": "inbox"}],
        },
    )
    records = [
        {
            "kind": "site",
            "id": "scale-site",
            "payload": {
                "id": "scale-site",
                "name": "scale",
                "displayName": "Scale",
                "webUrl": "https://local.test/sites/scale",
            },
        },
        {
            "kind": "drive",
            "id": "scale-drive",
            "payload": {"id": "scale-drive", "name": "Documents", "driveType": "documentLibrary"},
        },
        {
            "kind": "driveItem",
            "id": "scale-root",
            "payload": {
                "id": "scale-root",
                "name": "root",
                "root": {},
                "folder": {"childCount": count},
            },
        },
    ]
    for record in records:
        record["payload"].update(
            {
                "createdDateTime": "2026-10-07T00:00:00Z",
                "lastModifiedDateTime": "2026-10-07T00:00:00Z",
            }
        )
    edges = [
        {
            "source": {"kind": "site", "id": "scale-site"},
            "relation": "drives",
            "target": {"kind": "drive", "id": "scale-drive"},
        },
        {
            "source": {"kind": "drive", "id": "scale-drive"},
            "relation": "root",
            "target": {"kind": "driveItem", "id": "scale-root"},
        },
    ]
    request(
        "POST",
        "/workloads/sharepoint/import",
        {"scope": "scale-site", "expected_revision": 0, "records": records, "edges": edges},
    )
    revisions = {}
    for kind, path in (
        ("message", "/mail/content/import"),
        ("driveItem", "/workloads/sharepoint/import"),
    ):
        for batch in iter_collection_batches(kind, seed=seed, count=count, user=user["id"]):
            revisions[kind] = request("POST", path, batch)["revision"]
    return {
        "tenant": tenant["id"],
        "base": base,
        "user": user["id"],
        "auth": auth,
        "seed": seed,
        "count": count,
        "revisions": revisions,
    }


def exact_read(client, fixture, kind, *, boundary=None, caller=0):
    count, seed = fixture["count"], fixture["seed"]
    expected = {collection_id(kind, seed, i) for i in range(count)}
    seen, digest, pages = set(), hashlib.sha256(), 0
    path = (
        f"/v1.0/users/{fixture['user']}/messages?$top=100"
        if kind == "message"
        else "/v1.0/drives/scale-drive/root/children?$top=100"
    )
    started = time.monotonic()
    latencies = []
    while path:
        before = time.monotonic()
        response = call(client, fixture["auth"], "GET", path)
        latencies.append(time.monotonic() - before)
        ids = [r["id"] for r in response["value"]]
        assert len(set(ids)) == len(ids) and not seen.intersection(ids)
        seen.update(ids)
        pages += 1
        path = response.get("@odata.nextLink")
        if boundary and pages == 1:
            assert path, "Profile requires a continuation across the restart"
            boundary.wait(timeout=600)
            # Retry the exact issued continuation before accepting it into the result.
            first = call(client, fixture["auth"], "GET", path)
            retried = call(client, fixture["auth"], "GET", path)
            assert first == retried, "Continuation retry changed the acknowledged snapshot"
    assert seen == expected, ("Wrong or missing identities", kind, len(seen), len(expected))
    for identifier in sorted(seen):
        digest.update(identifier.encode())
    return {
        "kind": kind,
        "caller": caller,
        "count": len(seen),
        "pages": pages,
        "id_sha256": digest.hexdigest(),
        "seconds": time.monotonic() - started,
        "page_p95_seconds": sorted(latencies)[max(0, (len(latencies) * 95 + 99) // 100 - 1)],
        "page_max_seconds": max(latencies),
        "restart_and_retry": boundary is not None,
    }


def stream_object(client, headers, fixture, mib):
    digest = hashlib.sha256()
    for i in range(mib):
        digest.update(streamed_block(42, i))
    base = fixture["base"]
    upload = call(
        client,
        headers,
        "POST",
        base + "/content/uploads",
        {
            "size": mib * 1024 * 1024,
            "sha256": digest.hexdigest(),
            "media_type": "application/octet-stream",
        },
        201,
    )
    path = base + "/content/uploads/" + upload["uploadId"]
    for i in range(mib):
        response = client.put(
            path + f"/chunks/{i}",
            headers={**headers, "Content-Type": "application/octet-stream"},
            content=streamed_block(42, i),
            timeout=60,
        )
        assert response.status_code == 204
    content = call(client, headers, "POST", path + "/complete")
    item = collection_id("driveItem", fixture["seed"], 0)
    call(
        client,
        headers,
        "POST",
        base + "/workloads/sharepoint/import",
        {
            "scope": "scale-site",
            "expected_revision": fixture["revisions"]["driveItem"],
            "records": [
                {
                    "kind": "driveItem",
                    "id": item,
                    "content_id": content["id"],
                    "payload": {
                        "id": item,
                        "name": "Streamed evidence.bin",
                        "createdDateTime": "2026-10-07T00:00:00Z",
                        "lastModifiedDateTime": "2026-10-07T00:00:00Z",
                        "size": mib * 1024 * 1024,
                        "file": {"mimeType": "application/octet-stream"},
                        "parentReference": {"id": "scale-root", "driveId": "scale-drive"},
                    },
                }
            ],
        },
    )
    return {"item": item, "size": mib * 1024 * 1024, "sha256": digest.hexdigest()}


def verify_stream(client, fixture, expected):
    response = client.get(
        "/v1.0/drives/scale-drive/items/" + expected["item"] + "/content", headers=fixture["auth"]
    )
    assert response.status_code == 302
    path = local_url(client, response.headers["location"])
    digest, size = hashlib.sha256(), 0
    with client.stream("GET", path, timeout=180) as download:
        assert download.status_code == 200
        for chunk in download.iter_bytes(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    assert size == expected["size"] and digest.hexdigest() == expected["sha256"]
    partial = client.get(path, headers={"Range": "bytes=0-4095"})
    assert partial.status_code == 206 and partial.content == streamed_block(42, 0)[:4096]
    return {**expected, "verified_after_restart": True, "range_retry": True}


def qualify(
    directory, command, compose, client, headers, tenant, check, *, records=100000, mib=256
):
    assert records >= 202 and records % 2 == 0 and 1 <= mib <= 256
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "passed": False,
        "records": records,
        "object_mib": mib,
        "clients": 8,
        "api_replicas": 2,
    }
    report_path = directory / "coverage-scale-report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    save()
    report["hardware"] = json.loads(
        command(
            [
                "docker",
                "info",
                "--format",
                '{"cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}},"os":{{json .OperatingSystem}}}',
            ],
            capture=True,
        )
    )
    primary = collection_fixture(client, headers, tenant, count=records // 2)
    other = call(
        client,
        headers,
        "POST",
        "/_mock/tenants",
        {
            "slug": "coverage-isolation",
            "display_name": "Coverage isolation",
            "domain": "coverage-isolation.test",
            "licences": ["E5"],
        },
        201,
    )
    secondary = collection_fixture(client, headers, other, count=101)
    report["secondary_reads"] = [
        exact_read(client, secondary, kind) for kind in ("message", "driveItem")
    ]
    assert client.get(
        f"/v1.0/users/{primary['user']}/messages", headers=secondary["auth"]
    ).status_code in (403, 404)
    content = stream_object(client, headers, primary, mib)
    message = collection_id("message", primary["seed"], 0)
    call(
        client,
        primary["auth"],
        "PATCH",
        f"/v1.0/users/{primary['user']}/messages/{message}",
        {"isRead": True},
    )
    check("deterministic_metadata_content_and_acknowledged_mutation")
    # Only this generated lab's proxy is changed; ordinary installations retain their config.
    proxy = directory / "deploy/haproxy.cfg"
    proxy.write_text(
        proxy.read_text(encoding="utf-8").replace(
            "    server api api:8443 check resolvers docker init-addr libc,none",
            "    balance roundrobin\n"
            "    server-template api 1-2 api:8443 check resolvers docker init-addr libc,none",
        ),
        encoding="utf-8",
    )
    command(compose + ["up", "-d", "--no-build", "--scale", "api=2", "--wait"], timeout=300)
    command(compose + ["restart", "ingress"])
    replicas = command(compose + ["ps", "-q", "api"], capture=True).splitlines()
    assert len(replicas) == 2
    report["replicas"] = replicas

    def restart():
        report["restart_started_at"] = datetime.now(UTC).isoformat()
        command(compose + ["restart", "api"], timeout=300)
        wait_for_replicas(command, client, replicas)
        report["restart_completed_at"] = datetime.now(UTC).isoformat()

    boundary = Barrier(8, action=restart)

    def reader(index):
        with httpx.Client(
            base_url=str(client.base_url),
            verify=ssl.create_default_context(cafile=str(directory / ".runtime/instance/ca.pem")),
            trust_env=False,
            timeout=180,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as isolated:
            return exact_read(
                isolated,
                primary,
                "message" if index % 2 == 0 else "driveItem",
                boundary=boundary,
                caller=index,
            )

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            report["reads"] = list(pool.map(reader, range(8)))
        report["stream"] = verify_stream(client, primary, content)
        assert (
            call(
                client, primary["auth"], "GET", f"/v1.0/users/{primary['user']}/messages/{message}"
            )["isRead"]
            is True
        )
        report["acknowledged_change_preserved"] = True
        report["tenant_isolation"] = True
        report["resources_after"] = [
            json.loads(line)
            for line in command(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{json .}}",
                    *replicas,
                    command(compose + ["ps", "-q", "db"], capture=True),
                ],
                capture=True,
            ).splitlines()
        ]
        sql = (
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT id FROM mail_messages WHERE tenant_id='"
            + primary["tenant"]
            + "' AND user_id='"
            + primary["user"]
            + "' ORDER BY id LIMIT 100; "
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
            "SELECT id FROM workload_versions WHERE tenant_id='"
            + primary["tenant"]
            + "' AND workload='sharepoint' AND scope='scale-site' "
            "AND kind='driveItem' AND valid_to IS NULL ORDER BY id LIMIT 100;"
        )
        report["query_plans"] = command(
            compose
            + ["exec", "-T", "db", "psql", "-U", "mockazure", "-d", "mockazure", "-At", "-c", sql],
            capture=True,
        )
        report["passed"] = True
        check("eight_readers_two_replicas_exact_ids_stream_hash_restart_retry")
    finally:
        save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--small", action="store_true")
    args = parser.parse_args()
    smoke(
        qualify=lambda *values: qualify(
            *values, records=404 if args.small else 100000, mib=2 if args.small else 256
        )
    )
