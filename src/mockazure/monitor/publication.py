"""Generation-bound diagnostic delivery; no user credentials or recursive source writes."""

import json
from dataclasses import asdict
from datetime import timedelta
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import select

from mockazure.content.repository import stream_content
from mockazure.content.stream_upload import append_stream_chunk, begin_stream_upload
from mockazure.db.job_model import Job
from mockazure.jobs.repository import lock_claim
from mockazure.jobs.schemas import JobResult
from mockazure.monitor.diagnostic_models import DiagnosticDelivery, DiagnosticSetting
from mockazure.monitor.logs import ingest_log_rows
from mockazure.monitor.tables import catalog, instant, wire_time
from mockazure.monitor.workspaces import Workspace
from mockazure.providers.arm.models import ArmResource, Subscription
from mockazure.providers.arm.scopes import parse_scope
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.blob import find_blob, put_blob, selected_version
from mockazure.storage.containers import create_container
from mockazure.storage.models import StorageAccount, StorageNamespace
from mockazure.storage.types import StorageAccess

TABLES = {
    "blob": "StorageBlobLogs",
    "file": "StorageFileLogs",
    "queue": "StorageQueueLogs",
    "table": "StorageTableLogs",
    "keyvault": "AZKVAuditLogs",
    "arm": "AzureActivity",
}


def event_scope(event):
    resource = event.resource_id
    if event.service in {"blob", "file", "queue", "table"}:
        resource += "/" + event.service + "Services/default"
    return resource.lower()


def enabled(setting, event):
    for log in setting.properties.get("logs", []):
        if log["enabled"] and (
            log.get("category") == event.category
            or log.get("categoryGroup") in {"allLogs", "audit"}
        ):
            return True
    return False


async def queue_event(session, event, generation):
    if event.service not in TABLES:
        return
    source = event_scope(event)
    scope = parse_scope(event.resource_id)
    setting_scope = f"/subscriptions/{scope.subscription_id}" if event.service == "arm" else source
    settings = list(
        await session.scalars(
            select(DiagnosticSetting).where(
                DiagnosticSetting.tenant_id == event.tenant_id,
                DiagnosticSetting.resource_key == setting_scope,
                DiagnosticSetting.deleted.is_(False),
            )
        )
    )
    value = asdict(event)
    value.update(
        id=str(event.id),
        tenant_id=str(event.tenant_id),
        occurred_at=wire_time(event.occurred_at),
        resource_id=source,
    )
    # Store only source labels/properties already sanitized by validate_event.
    for setting in settings:
        if not enabled(setting, event):
            continue
        for destination in setting.destinations:
            previous = await session.scalar(
                select(DiagnosticDelivery.id).where(
                    DiagnosticDelivery.tenant_id == event.tenant_id,
                    DiagnosticDelivery.setting_id == setting.id,
                    DiagnosticDelivery.source_id == event.id,
                    DiagnosticDelivery.destination_kind == destination["kind"],
                )
            )
            if previous:
                continue
            identifier = uuid4()
            session.add(
                Job(
                    id=identifier,
                    tenant_id=event.tenant_id,
                    kind="monitor.diagnostic",
                    payload={"delivery_id": str(identifier)},
                    available_at=event.occurred_at,
                )
            )
            await session.flush()
            session.add(
                DiagnosticDelivery(
                    id=identifier,
                    tenant_id=event.tenant_id,
                    setting_id=setting.id,
                    source_id=event.id,
                    generation=generation,
                    destination_kind=destination["kind"],
                    destination=destination,
                    event=value,
                )
            )
    await session.flush()


def table_row(event, mode):
    service = event["service"]
    table = "AzureDiagnostics" if mode != "Dedicated" and service != "arm" else TABLES[service]
    scope = parse_scope(event["resource_id"])
    operation = event["operation"]
    values = {
        "TimeGenerated": event["occurred_at"],
        "OperationName": operation,
        "OperationNameValue": operation,
        "Category": event["category"],
        "CategoryValue": event["category"],
        "_ResourceId": scope.key,
        "ResourceId": scope.key,
        "ResourceGroup": scope.group or "",
        "SubscriptionId": str(scope.subscription_id),
        "CorrelationId": event["id"],
        "SourceSystem": "Azure",
        "ResultType": "Success",
        "ActivityStatusValue": "Succeeded",
        "ActivityStatus": "Succeeded",
        "AuthenticationType": event["payload"].get("authenticationType", ""),
        "AdditionalFields": event["payload"],
    }
    if service in {"blob", "file", "queue", "table"}:
        values["AccountName"] = scope.key.split("/")[-3]
    columns = {c["name"] for c in catalog()[table]["columns"]}
    return table, {k: v for k, v in values.items() if k in columns}


def archive_record(event):
    return {
        "time": event["occurred_at"],
        "resourceId": event["resource_id"].upper(),
        "category": event["category"],
        "operationName": event["operation"],
        "resultType": "Succeeded",
        "correlationId": event["id"],
        "level": "Informational",
        "properties": event["payload"],
    }


async def deliver_blob(session, tenant, destination, event, now):
    access = StorageAccess(tenant, destination["resource"], "diagnostic", None, frozenset())
    category = event["category"].lower()
    activity = event["service"] == "arm"
    container = "insights-activity-logs" if activity else "insights-logs-" + category
    scope = parse_scope(event["resource_id"])
    resource = f"/subscriptions/{scope.subscription_id}" if activity else scope.key
    when = instant(event["occurred_at"])
    name = "resourceId=" + resource.upper() + when.strftime("/y=%Y/m=%m/d=%d/h=%H/m=00/PT1H.json")
    if await session.get(StorageNamespace, (tenant, access.account_id, "blob", container)) is None:
        await create_container(session, access, container, {}, now)
    blob = await find_blob(session, access, container, name, now)
    version = await selected_version(session, blob)
    records = []
    if version:
        if version.size > 64 * 1024 * 1024:
            raise ValueError("Diagnostic hourly archive exceeds local limit")
        raw = b"".join([part async for part in stream_content(session, tenant, version.content_id)])
        records = json.loads(raw)["records"]
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise ValueError("Existing diagnostic archive has invalid records")
    # Idempotency belongs to the fenced outbox transaction, not public blob content.
    # A caller can edit the archive and must not spoof a completed source delivery.
    records.append(archive_record(event))
    raw = json.dumps({"records": records}, separators=(",", ":"), ensure_ascii=False).encode()
    upload = await begin_stream_upload(session, tenant, len(raw), 64 * 1024 * 1024)
    await append_stream_chunk(session, tenant, upload, raw)
    await put_blob(
        session,
        access,
        container,
        name,
        upload,
        {"properties": {"content_type": "application/json"}},
        now,
    )
    return {"container": container, "name": name}


async def eligible(session, delivery, setting):
    if setting is None or setting.deleted:
        return False
    destination = delivery.destination
    # Category and mode changes affect new events. Destination removal revokes
    # pending work; matching resource identity prevents delete/recreate reuse.
    if not any(
        d["kind"] == destination["kind"]
        and d["resource"] == destination["resource"]
        and d["identity"] == destination["identity"]
        for d in setting.destinations
    ):
        return False
    row = await session.get(ArmResource, (delivery.tenant_id, destination["resource"]))
    if (
        row is None
        or row.created_at.isoformat() != destination["identity"]
        or row.provisioning_state != "Succeeded"
    ):
        return False
    sub = await session.get(Subscription, row.subscription_id)
    if sub is None or sub.state != "Enabled":
        return False
    if destination["kind"] == "workspace":
        workspace = await session.get(Workspace, (delivery.tenant_id, destination["resource"]))
        return workspace is not None and str(workspace.customer_id) == destination["workspace"]
    account = await session.get(StorageAccount, (delivery.tenant_id, destination["resource"]))
    network = row.payload.get("properties", {}).get("networkAcls", {})
    if network.get("defaultAction") == "Deny" and "AzureServices" not in network.get(
        "bypass", ""
    ).split(","):
        return False
    return account is not None


async def process_diagnostic_delivery(database, claim, wall_now):
    from mockazure.cloud.lifecycle import lock_lifecycle
    from mockazure.mail.send import DeliveryLeaseExpired

    started = monotonic()

    def now():
        return wall_now + timedelta(seconds=monotonic() - started)

    try:
        async with database.sessions.begin() as session:
            state = await lock_lifecycle(session, claim.tenant_id)
            job = await lock_claim(session, claim, now())
            if job is None:
                return JobResult("stale", None, None)
            delivery = await session.get(DiagnosticDelivery, claim.id)
            setting = (
                await session.get(DiagnosticSetting, delivery.setting_id) if delivery else None
            )
            if (
                delivery is None
                or delivery.tenant_id != claim.tenant_id
                or delivery.generation != state.generation
                or not await eligible(session, delivery, setting)
            ):
                job.state, job.error_code = "failed", "DiagnosticDestinationInvalidated"
            else:
                try:
                    async with session.begin_nested():
                        if delivery.destination_kind == "storage":
                            result = await deliver_blob(
                                session,
                                claim.tenant_id,
                                delivery.destination,
                                delivery.event,
                                now(),
                            )
                        else:
                            table, row = table_row(delivery.event, delivery.destination.get("mode"))
                            await ingest_log_rows(
                                session,
                                claim.tenant_id,
                                UUID(delivery.destination["workspace"]),
                                table,
                                [row],
                                [str(delivery.source_id)],
                                origin="native",
                            )
                            result = {
                                "table": table,
                                "workspace": delivery.destination["workspace"],
                            }
                        if job.lease_until <= now():
                            raise DeliveryLeaseExpired()
                    job.state, job.error_code = "succeeded", None
                    job.result = {"sourceId": str(delivery.source_id), "destination": result}
                except (OSError, StorageError):
                    job.state = "pending" if claim.attempt < 3 else "failed"
                    job.error_code = "DiagnosticDestinationUnavailable"
                    job.available_at = now() + timedelta(seconds=5 * 2 ** (claim.attempt - 1))
            job.lease_owner = job.lease_until = None
            job.updated_at = now()
            await session.flush()
            return JobResult(job.state, job.result, job.error_code)
    except DeliveryLeaseExpired:
        return JobResult("stale", None, None)
