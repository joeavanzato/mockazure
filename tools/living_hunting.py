"""Original SDK/PowerShell cookbook, isolation and scale gates on a populated lab."""

import asyncio
import os
import ssl
import time
from importlib.metadata import version
from uuid import uuid4

import httpx
from msgraph.generated.security.microsoft_graph_security_run_hunting_query import (
    run_hunting_query_post_request_body,
)
from sqlalchemy import String, cast, func, select

from integrations.python.graph_directory import graph_client
from mockazure.hunting.contracts import catalog
from mockazure.hunting.cookbook import cases, validate_case
from mockazure.hunting.models import HuntingRecord
from mockazure.identity.models import User
from mockazure.population.identity import resolve_identity
from tools.living_tenant_clients import acquire
from tools.verify_living_hunting import run_powershell


def peak_memory():
    if os.name != "nt":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("faults", wintypes.DWORD),
            *[
                (name, ctypes.c_size_t)
                for name in (
                    "peak",
                    "working",
                    "quota_peak_paged",
                    "quota_paged",
                    "quota_peak_nonpaged",
                    "quota_nonpaged",
                    "pagefile",
                    "peak_pagefile",
                )
            ],
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = wintypes.HANDLE
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    get_memory.restype = wintypes.BOOL
    if not get_memory(
        get_process(),
        ctypes.byref(counters),
        counters.cb,
    ):
        raise RuntimeError("Could not measure qualification process memory")
    return counters.peak


def validate(report):
    try:
        assert report["phase"] == "hunting" and report["passed"] is True
        assert report["failed_cases"] == []
        assert set(report["cases"]) == {
            "population",
            "cookbook",
            "python",
            "powershell",
            "isolation",
            "scale",
            "replay",
        }
        assert all(value["state"] == "passed" for value in report["cases"].values())
        book = report["cases"]["cookbook"]
        assert {c["id"] for c in book["queries"]} == {c["id"] for c in cases()}
        assert all(c["rows"] > 0 and c["seconds"] >= 0 for c in book["queries"])
        scale = report["cases"]["scale"]
        expected = 20 if report["scale"] == "small" else 5000
        assert scale["table_counts"] == dict.fromkeys(catalog(), expected)
        assert scale["total_rows"] == expected * 20 and scale["payload_bytes"] > 0
        assert scale["users"] == (20 if report["scale"] == "small" else 2500)
        assert scale["qualification_peak_memory_bytes"] > 0
        assert report["cases"]["replay"]["created"] == 0
        assert report["cases"]["isolation"]["foreign_rows"] == 0
        assert report["cases"]["isolation"]["incident_patch_status"] in {404, 405, 501}
        assert report["cases"]["powershell"]["cmdlet"] == "Start-MgSecurityHuntingQuery"
        assert report["cases"]["python"]["client"] == "msgraph-sdk"
    except (AssertionError, KeyError, TypeError):
        raise ValueError("Incomplete hunting qualification") from None


async def qualify_hunting(settings, admin, ctx, refs, reference_time):
    await admin.request(
        "PATCH",
        f"/_mock/tenants/{ctx.tenant_id}/evidence/settings",
        json={
            "clock_mode": "fixed",
            "fixed_time": reference_time,
        },
    )
    credential = await resolve_identity(ctx, refs["hunting-client"])
    token = await asyncio.to_thread(
        acquire, settings, ctx, credential, "https://graph.microsoft.com"
    )
    results = []
    response_sizes = []

    async def measure(response):
        await response.aread()
        response_sizes.append(len(response.content))

    tls = ssl.create_default_context(cafile=str(settings.ca_file))
    async with httpx.AsyncClient(
        verify=tls,
        trust_env=False,
        follow_redirects=False,
        timeout=60,
        event_hooks={"response": [measure]},
    ) as http:
        graph = graph_client({"endpoints": {"graph": settings.endpoints.graph}}, token, http)
        for case in cases():
            started = time.monotonic()
            response = await graph.security.microsoft_graph_security_run_hunting_query.post(
                run_hunting_query_post_request_body.RunHuntingQueryPostRequestBody(
                    query=case["query"],
                    timespan="P30D",
                ),
            )
            value = {
                "schema": [{"name": c.name, "type": c.type} for c in response.schema],
                "results": [row.additional_data for row in response.results],
            }
            validate_case(case, value)
            results.append(
                {
                    "id": case["id"],
                    "rows": len(value["results"]),
                    "seconds": round(time.monotonic() - started, 4),
                    "response_bytes": response_sizes[-1],
                }
            )
        device_response = await graph.security.microsoft_graph_security_run_hunting_query.post(
            run_hunting_query_post_request_body.RunHuntingQueryPostRequestBody(
                query="DeviceInfo | project DeviceId, DeviceName | take 1",
            ),
        )
        device = device_response.results[0].additional_data
        stamp = uuid4().hex[:10]
        foreign = await admin.request(
            "POST",
            "/_mock/tenants",
            json={
                "slug": "hunting-isolation-" + stamp,
                "display_name": "Overlapping business names",
                "domain": "hunting-isolation-" + stamp + ".test",
                "licences": ["E5"],
            },
        )
        foreign_id = "foreign-" + stamp
        await admin.request(
            "POST",
            f"/_mock/tenants/{foreign['id']}/hunting/tables/DeviceInfo/import",
            json={
                "source_ids": ["overlap"],
                "rows": [
                    {
                        "Timestamp": reference_time,
                        "DeviceId": foreign_id,
                        "DeviceName": device["DeviceName"],
                    }
                ],
            },
        )
        isolated = await http.post(
            settings.endpoints.graph + "/v1.0/security/runHuntingQuery",
            headers={"Authorization": "Bearer " + token},
            json={
                "Query": f"DeviceInfo | where DeviceId == '{foreign_id}' | project DeviceId",
                "workspaceId": foreign["id"],
            },
        )
        assert isolated.status_code == 200 and isolated.json()["results"] == []
        mutation = await http.patch(
            settings.endpoints.graph + "/v1.0/security/incidents/nonexistent",
            headers={"Authorization": "Bearer " + token},
            json={"status": "resolved"},
        )
        assert mutation.status_code in {404, 405, 501}
    powershell = await run_powershell(
        settings,
        token,
        device["DeviceId"],
        credential_secret=credential.secret.get_secret_value(),
        query=f"DeviceInfo | where DeviceId == '{device['DeviceId']}'"
        " | distinct DeviceId, DeviceName",
    )
    async with ctx.database.sessions() as session:
        counts = dict(
            (
                await session.execute(
                    select(HuntingRecord.table_name, func.count())
                    .where(
                        HuntingRecord.tenant_id == ctx.tenant_id,
                        HuntingRecord.provenance["kind"].astext == "fixture",
                    )
                    .group_by(HuntingRecord.table_name)
                )
            ).all()
        )
        size = await session.scalar(
            select(func.sum(func.octet_length(cast(HuntingRecord.payload, String)))).where(
                HuntingRecord.tenant_id == ctx.tenant_id,
            )
        )
        users = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.tenant_id == ctx.tenant_id,
                User.deleted_at.is_(None),
            )
        )
    return {
        "failed_cases": [],
        "cases": {
            "cookbook": {"state": "passed", "queries": results},
            "python": {
                "state": "passed",
                "client": "msgraph-sdk",
                "version": version("msgraph-sdk"),
            },
            "powershell": powershell,
            "isolation": {
                "state": "passed",
                "other_tenant_id": foreign["id"],
                "foreign_rows": 0,
                "overlapping_device_name": device["DeviceName"],
                "incident_patch_status": mutation.status_code,
            },
            "scale": {
                "state": "passed",
                "users": users,
                "table_counts": counts,
                "total_rows": sum(counts.values()),
                "payload_bytes": size,
                "qualification_peak_memory_bytes": peak_memory(),
                "memory_scope": "client qualification process peak resident/working set; "
                "excludes API and PostgreSQL",
            },
        },
    }
