"""Public collection of native facts from a fresh, owned living tenant."""

import asyncio
import re
import ssl
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import func, select

from integrations.python.graph_directory import graph_client
from mockazure.cloud.models import CloudEvent
from mockazure.db.job_model import Job
from mockazure.endpoints import local_origin
from mockazure.evidence.models import EvidenceObservation, EvidenceSource
from mockazure.monitor.diagnostic_models import DiagnosticDelivery
from mockazure.population.transport import ClientCredential, LocalTransport
from mockazure.providers.arm.scopes import parse_scope
from tools.living_tenant_clients import acquire, exchange_collect, verify_exchange_client

KINDS = {"authentication", "directory", "security", "mail", "workload", "arm", "service"}
SERVICES = {"blob", "file", "table", "queue", "keyvault"}
COLLECTORS = {"graph-sdk", "exchange-module", "activity-feed", "arm-rest", "logs-rest", "blob-sdk"}


def validate(report):
    try:
        assert report["phase"] == "evidence" and report["passed"] is True
        assert re.fullmatch("[0-9a-f]{64}", report["source_fingerprint"])
        assert report["missing_expected_ids"] == []
        cases = report["cases"]
        assert KINDS <= {r["source_kind"] for r in cases}
        assert SERVICES == {r["service"] for r in cases if r["outcome"] == "failure"}
        assert SERVICES == {
            r["service"]
            for r in cases
            if r["source_kind"] == "service" and r["outcome"] == "success"
        }
        assert COLLECTORS <= {r["collector"] for r in cases}
        for row in cases:
            assert row["state"] == "passed" and row["source_ids"] and row["expected_ids"]
            assert set(row["expected_ids"]) <= set(row["observed_ids"])
            assert row["expected_count"] == len(set(row["expected_ids"]))
            assert row["observed_count"] == len(set(row["observed_ids"]))
            assert all(
                row[k] for k in ("operation_key", "source_ref", "client", "version", "destination")
            )
        imported = report["import_check"]
        assert imported["stored"] is True
        assert imported["native_sources"] == imported["native_deliveries"] == []
        clock = report["clock_check"]
        assert clock["advanced_seconds"] > 3600 and clock["expired_token_status"] == 401
        assert clock["access_token_expiry_before"] == clock["access_token_expiry_after"]
        assert clock["valid_token_status"] == 200
    except (KeyError, TypeError, AssertionError):
        raise ValueError("Incomplete living-tenant evidence qualification") from None


def checked(response, status=200):
    if response.status_code != status:
        raise AssertionError(
            f"{response.request.method} {response.request.url.path}: "
            f"{response.status_code}, expected {status}"
        )
    return response.json() if response.content and status < 400 else None


def continuation(url, endpoint):
    parsed = urlsplit(url)
    origin = local_origin(f"{parsed.scheme}://{parsed.netloc}")
    if origin != local_origin(endpoint) or parsed.fragment:
        raise ValueError("Collector continuation escaped its local service")
    return parsed


async def client_fixture(admin, ctx, permissions, label):
    base = f"/_mock/tenants/{ctx.tenant_id}/identity"
    app = await admin.request(
        "POST",
        base + "/applications",
        json={
            "display_name": label,
            "client_mode": "confidential",
            "required_permissions": permissions,
        },
    )
    identifier = app["client_id"]
    secret = await admin.request(
        "POST",
        base + f"/applications/{identifier}/credentials",
        json={"kind": "secret"},
    )
    for permission in permissions:
        await admin.request(
            "PUT",
            base + "/grants/" + str(uuid4()),
            json={
                "client_id": identifier,
                **permission,
            },
        )
    return ClientCredential(UUID(identifier), SecretStr(secret["secret"]))


async def page_ids(transport, service, path, *, params=None, field="id"):
    """Only follow continuation URLs on the explicitly configured local service."""
    ids = set()
    for _ in range(200):
        response = await transport.request(service, "GET", path, params=params)
        page = checked(response)
        ids.update(str(item[field]) for item in page["value"])
        link = page.get("@odata.nextLink", page.get("nextLink"))
        if not link:
            return ids
        parsed = continuation(link, getattr(transport.context.settings.endpoints, service))
        path, params = parsed.path, parse_qs(parsed.query)
    raise TimeoutError("Collection paging exceeded its bound")


async def drain(admin, ctx):
    base = f"/_mock/tenants/{ctx.tenant_id}"
    deadline = monotonic() + 180
    while monotonic() < deadline:
        await admin.request("POST", base + "/evidence/drain", json={"max_jobs": 1000})
        await admin.request("POST", base + "/activity/drain", json={"max_rows": 1000})
        async with ctx.database.sessions() as session:
            states = list(
                await session.scalars(
                    select(Job.state).where(
                        Job.tenant_id == ctx.tenant_id,
                        Job.kind.in_(
                            ["evidence.project", "activity.publish", "monitor.diagnostic"]
                        ),
                    )
                )
            )
            available = await session.scalar(
                select(func.max(EvidenceObservation.available_at)).where(
                    EvidenceObservation.tenant_id == ctx.tenant_id,
                    EvidenceObservation.source_kind != "historical",
                )
            )
        assert "failed" not in states, "Native evidence delivery failed"
        if all(s == "succeeded" for s in states) and (
            available is None or available <= datetime.now(UTC)
        ):
            return
        await asyncio.sleep(0.5)
    raise TimeoutError("Native evidence delivery did not drain")


async def actions(admin, ctx, refs, entities):
    stamp = uuid4().hex[:10]
    permissions = [
        {
            "resource": "graph",
            "kind": "application",
            "permissions": [
                "User.ReadWrite.All",
                "Application.ReadWrite.All",
                "Files.ReadWrite.All",
                "Mail.ReadWrite",
                "MailboxSettings.ReadWrite",
                "AuditLog.Read.All",
                "AuditLogsQuery.Read.All",
            ],
        },
        {"resource": "activity", "kind": "application", "permissions": ["ActivityFeed.Read"]},
    ]
    credential = await client_fixture(
        admin, ctx, permissions, "Living evidence actor and collector"
    )
    transport = LocalTransport(ctx, credential)
    feed = f"/api/v1.0/{ctx.tenant_id}/activity/feed"
    for kind in [
        "Audit.AzureActiveDirectory",
        "Audit.Exchange",
        "Audit.SharePoint",
        "Audit.General",
    ]:
        checked(
            await transport.request(
                "activity",
                "POST",
                feed + "/subscriptions/start",
                params={"contentType": kind},
            )
        )
    checked(
        await transport.request(
            "graph",
            "PATCH",
            "/v1.0/users/" + refs["primary-user"],
            json={"department": "Living evidence " + stamp},
        ),
        204,
    )
    app = checked(
        await transport.request(
            "graph",
            "POST",
            "/v1.0/applications",
            json={"displayName": "Living evidence application"},
        ),
        201,
    )
    password = checked(
        await transport.request(
            "graph",
            "POST",
            "/v1.0/applications/" + app["id"] + "/addPassword",
            json={"passwordCredential": {"displayName": "Temporary qualification credential"}},
        )
    )
    # Discard reusable material; retain only the public key identity needed to remove it.
    key_id = password["keyId"]
    password.clear()
    checked(
        await transport.request(
            "graph",
            "POST",
            "/v1.0/applications/" + app["id"] + "/removePassword",
            json={"keyId": key_id},
        ),
        204,
    )
    checked(
        await transport.request(
            "graph",
            "POST",
            f"/v1.0/users/{refs['primary-user']}/mailFolders/inbox/messageRules",
            json={
                "displayName": "Living invoice review",
                "sequence": 1,
                "isEnabled": True,
                "conditions": {"subjectContains": ["invoice"]},
                "actions": {"markAsRead": True},
            },
        ),
        201,
    )
    checked(
        await transport.request(
            "graph",
            "PATCH",
            f"/v1.0/drives/{entities['business.drives:0']}/items/{entities['business.documents:0']}",
            json={"name": "living-evidence-review-" + stamp + ".docx"},
        )
    )
    denied = LocalTransport(ctx, await client_fixture(admin, ctx, [], "Living denied actor"))
    account, vault = refs["account"].rsplit("/", 1)[1], refs["vault"].rsplit("/", 1)[1]
    calls = [
        ("storage", f"/{account}/evidence/project-review.docx", {"x-ms-version": "2025-11-05"}, {}),
        (
            "file",
            f"/{account}/evidence/project-review.docx",
            {"x-ms-version": "2025-11-05", "x-ms-file-request-intent": "backup"},
            {},
        ),
        (
            "table",
            f"/{account}/Evidence()",
            {"x-ms-version": "2019-02-02", "Accept": "application/json"},
            {},
        ),
        (
            "queue",
            f"/{account}/evidence/messages",
            {"x-ms-version": "2026-02-06"},
            {"peekonly": "true"},
        ),
        ("keyvault", f"/{vault}/secrets/evidence-secret", {}, {"api-version": "2025-07-01"}),
    ]
    for service, path, headers, params in calls:
        checked(await denied.request(service, "GET", path, headers=headers, params=params), 403)
    return credential, transport, feed


async def graph_collect(settings, ctx, credential, transport, start, end):
    raw = await asyncio.to_thread(acquire, settings, ctx, credential, "https://graph.microsoft.com")
    collected = {}
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=str(settings.ca_file)),
        trust_env=False,
        follow_redirects=False,
        timeout=60,
    ) as http:
        graph = graph_client({"endpoints": {"graph": settings.endpoints.graph}}, raw, http)
        for family, builder in [("directoryAudit", graph.audit_logs.directory_audits)]:
            ids, page = set(), await builder.get()
            for _ in range(200):
                ids.update(row.id for row in page.value)
                if not page.odata_next_link:
                    break
                continuation(page.odata_next_link, settings.endpoints.graph)
                page = await builder.with_url(page.odata_next_link).get()
            else:
                raise TimeoutError("Graph SDK paging exceeded its bound")
            collected[family] = ids
    collected["signIn"] = await page_ids(
        transport,
        "graph",
        "/beta/auditLogs/signIns",
        params={"$filter": "signInEventTypes/any(t:t eq 'servicePrincipal')"},
    )
    path = "/v1.0/security/auditLog/queries"
    query = checked(
        await transport.request(
            "graph",
            "POST",
            path,
            json={
                "displayName": "Living native evidence",
                "filterStartDateTime": start,
                "filterEndDateTime": end,
            },
        ),
        201,
    )
    path += "/" + query["id"]
    deadline = monotonic() + 120
    while monotonic() < deadline:
        state = checked(await transport.request("graph", "GET", path))["status"]
        assert state != "failed"
        if state == "succeeded":
            break
        await asyncio.sleep(0.5)
    else:
        raise TimeoutError("Graph UAL query did not complete")
    collected["auditLogRecord"] = await page_ids(transport, "graph", path + "/records")
    return collected


async def feed_collect(transport, path):
    ids = set()
    for kind in [
        "Audit.AzureActiveDirectory",
        "Audit.Exchange",
        "Audit.SharePoint",
        "Audit.General",
    ]:
        target, params = path + "/subscriptions/content", {"contentType": kind}
        for _ in range(200):
            response = await transport.request("activity", "GET", target, params=params)
            for content in checked(response):
                parsed = continuation(
                    content["contentUri"], transport.context.settings.endpoints.activity
                )
                records = checked(
                    await transport.request(
                        "activity",
                        "GET",
                        parsed.path,
                        params=parse_qs(parsed.query),
                    )
                )
                ids.update(r["Id"] for r in records)
            link = response.headers.get("NextPageUri")
            if not link:
                break
            parsed = continuation(link, transport.context.settings.endpoints.activity)
            target, params = parsed.path, parse_qs(parsed.query)
        else:
            raise TimeoutError("Activity feed paging exceeded its bound")
    return ids


def case(
    kind,
    operation,
    sources,
    expected,
    observed,
    collector,
    destination,
    *,
    outcome="success",
    service="",
    client="httpx",
    client_version=None,
    limitation="",
):
    expected, observed = sorted(set(expected)), sorted(set(observed))
    return {
        "source_kind": kind,
        "operation_key": operation,
        "source_ids": sorted(set(sources)),
        "source_ref": "evidence/data/operation_mappings.json@business-evidence-v1",
        "expected_ids": expected,
        "observed_ids": observed,
        "expected_count": len(expected),
        "observed_count": len(observed),
        "collector": collector,
        "destination": destination,
        "client": client,
        "version": client_version or version(client),
        "outcome": outcome,
        "service": service,
        "state": "passed" if expected and set(expected) <= set(observed) else "failed",
        "limitation": limitation,
    }


async def qualify_evidence(settings, admin, ctx, credential, transport, refs, entities, domain):
    from tools.living_evidence_checks import archive_collect, clock_check, import_check

    await asyncio.to_thread(verify_exchange_client)
    base = f"/_mock/tenants/{ctx.tenant_id}"
    await admin.request("PATCH", base + "/evidence/settings", json={"delay_seconds": 0})
    await drain(admin, ctx)
    async with ctx.database.sessions() as session:
        before = set(
            await session.scalars(
                select(EvidenceObservation.record_id).where(
                    EvidenceObservation.tenant_id == ctx.tenant_id,
                )
            )
        )
    actor, actor_transport, feed = await actions(admin, ctx, refs, entities)
    await drain(admin, ctx)
    # Freeze expected committed sources before collection itself creates read/login diagnostics.
    async with ctx.database.sessions() as session:
        observations = list(
            await session.scalars(
                select(EvidenceObservation).where(
                    EvidenceObservation.tenant_id == ctx.tenant_id,
                    EvidenceObservation.source_kind != "historical",
                )
            )
        )
        sources = {
            (s.kind, s.id): s
            for s in await session.scalars(
                select(EvidenceSource).where(
                    EvidenceSource.tenant_id == ctx.tenant_id,
                )
            )
        }
        events = list(
            await session.scalars(
                select(CloudEvent).where(
                    CloudEvent.tenant_id == ctx.tenant_id,
                    CloudEvent.id.in_(
                        select(DiagnosticDelivery.source_id).where(
                            DiagnosticDelivery.tenant_id == ctx.tenant_id,
                        )
                    ),
                )
            )
        )
    now = datetime.now(UTC)
    start, end = (now - timedelta(hours=1)).isoformat(), (now + timedelta(minutes=10)).isoformat()
    graph = await graph_collect(settings, ctx, actor, actor_transport, start, end)
    activity = await feed_collect(actor_transport, feed)
    arm = await page_ids(
        transport,
        "arm",
        f"/subscriptions/{parse_scope(refs['account']).subscription_id}"
        + "/providers/Microsoft.Insights/eventtypes/management/values",
        params={
            "api-version": "2015-04-01",
            "$filter": f"eventTimestamp ge '{start}' and eventTimestamp le '{end}'",
        },
        field="eventDataId",
    )
    exchange_permission = [
        {"resource": "exchange", "kind": "application", "permissions": ["Exchange.ManageAsApp"]}
    ]
    exchange_actor = await client_fixture(
        admin, ctx, exchange_permission, "Living Exchange audit collector"
    )
    await admin.request(
        "PUT",
        base + f"/identity/applications/{exchange_actor.client_id}/roles/Exchange Administrator",
        json={},
    )
    exchange_raw = await asyncio.to_thread(
        acquire, settings, ctx, exchange_actor, "https://outlook.office365.com"
    )
    exchange = await exchange_collect(
        settings,
        {
            "audit": True,
            "start": start,
            "end": end,
            "endpoint": settings.endpoints.exchange,
            "login": settings.endpoints.login,
            "client_id": str(exchange_actor.client_id),
            "domain": domain,
            "token": exchange_raw,
            "ca_file": str(settings.ca_file),
            "relay": False,
            "vendor": str(Path("work/living-clients").resolve()),
            "logs": str(settings.data_dir / "exo-qualification"),
        },
    )
    cases = []
    for kind in sorted(KINDS - {"service"}):
        rows = [
            r
            for r in observations
            if r.source_kind == kind
            and (kind in {"arm", "authentication"} or r.record_id not in before)
        ]
        assert rows, f"No native {kind} observations"
        for family in sorted({r.family for r in rows}):
            selected = [r for r in rows if r.family == family]
            source_ids = [str(r.source_id) for r in selected]
            expected = [str(r.record_id) for r in selected]
            operations = sorted(
                {
                    str(
                        sources[(kind, r.source_id)].facts.get(
                            "operation",
                            r.payload.get("activityDisplayName", r.payload.get("operation", kind)),
                        )
                    )
                    for r in selected
                }
            )
            common = (kind, "; ".join(operations), source_ids, expected)
            if family == "activityLog":
                cases.append(case(*common, arm, "arm-rest", family))
            else:
                cases.append(
                    case(
                        *common,
                        graph[family],
                        "graph-sdk" if family == "directoryAudit" else "graph-rest",
                        family,
                        client="msgraph-sdk" if family == "directoryAudit" else "httpx",
                        limitation=(
                            "App-only sign-ins use the documented beta event-type filter; "
                            "v1.0 sign-ins expose interactive user events."
                            if family == "signIn"
                            else ""
                        ),
                    )
                )
            if family == "auditLogRecord":
                cases.append(case(*common, activity, "activity-feed", family))
                cases.append(
                    case(
                        *common,
                        exchange["ids"],
                        "exchange-module",
                        family,
                        client="ExchangeOnlineManagement",
                        client_version=exchange["version"],
                        limitation="Original module runs in an isolated Linux client on Windows.",
                    )
                )
    workspace = refs["workspace"]
    keys = checked(
        await transport.request(
            "arm",
            "POST",
            refs["account"] + "/listKeys",
            params={"api-version": "2025-06-01"},
            json={},
        )
    )
    archives = await asyncio.to_thread(archive_collect, settings, refs, keys["keys"][0]["value"])
    from mockazure.monitor.publication import TABLES

    for service in sorted(SERVICES):
        table = TABLES[service]
        result = checked(
            await transport.request(
                "logs",
                "POST",
                f"/v1/workspaces/{workspace}/query",
                json={"query": table},
            )
        )["tables"][0]
        index = [c["name"] for c in result["columns"]].index("CorrelationId")
        observed = {r[index] for r in result["rows"]}
        for outcome in ("success", "failure"):
            selected = [
                e
                for e in events
                if e.service == service
                and (e.payload.get("outcome") == "Failed") == (outcome == "failure")
            ]
            expected = [str(e.id) for e in selected]
            assert expected, f"No {service} {outcome} facts"
            common = (
                "service",
                "; ".join(sorted({e.operation for e in selected})),
                expected,
                expected,
            )
            cases.append(
                case(*common, observed, "logs-rest", table, service=service, outcome=outcome)
            )
            cases.append(
                case(
                    *common,
                    archives,
                    "blob-sdk",
                    "diagnostic archive",
                    service=service,
                    outcome=outcome,
                    client="azure-storage-blob",
                )
            )
    result = {
        "cases": cases,
        "missing_expected_ids": sorted(
            {
                identifier
                for row in cases
                for identifier in set(row["expected_ids"]) - set(row["observed_ids"])
            }
        ),
        "import_check": await import_check(admin, ctx, entities, actor_transport),
        "clock_check": await clock_check(admin, ctx, actor),
    }
    return result
