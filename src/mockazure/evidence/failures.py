"""Capture attributable failures after rollback using the existing diagnostic outbox."""

import asyncio
import logging
from uuid import UUID, uuid5

from sqlalchemy import select, text

from mockazure.cloud.events import append_service_event
from mockazure.cloud.types import ServiceEvent
from mockazure.evidence.failure_models import FailureFact
from mockazure.identity.models import ServicePrincipal
from mockazure.providers.arm.models import ArmResource
from mockazure.providers.arm.scopes import parse_scope

LOG = logging.getLogger(__name__)


async def capture_failure(database, failure: FailureFact) -> UUID:
    failure = FailureFact.model_validate(failure.model_dump())
    identifier = uuid5(failure.request_id, "mockazure:data-plane:failure:v1")
    async with asyncio.timeout(3):
        async with database.sessions.begin() as session:
            await session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
            await session.execute(text("SET LOCAL lock_timeout = '500ms'"))
            scope = parse_scope(failure.source_resource_id)
            # Recheck ownership after the failed request transaction has ended.
            if await session.get(ArmResource, (failure.tenant_id, scope.key)) is None:
                raise ValueError("Failure source is no longer owned")
            operation = failure.operation_key
            category = (
                "AuditEvent"
                if failure.service == "keyvault"
                else "StorageRead"
                if operation.startswith(("Get", "List", "Query", "Peek"))
                else "StorageDelete"
                if operation.startswith(("Delete", "Clear"))
                else "StorageWrite"
            )
            await append_service_event(
                session,
                ServiceEvent(
                    identifier,
                    failure.tenant_id,
                    scope.key,
                    failure.service,
                    category,
                    operation,
                    failure.occurred_at,
                    {
                        **failure.safe_properties,
                        "outcome": "Failed",
                        "statusCode": failure.status_code,
                        "errorCode": failure.error_code,
                        "requestId": str(failure.request_id),
                        "principalId": str(failure.principal_id) if failure.principal_id else None,
                    },
                ),
            )
    return identifier


async def principal_id(session, actor):
    if actor.subject_id:
        return actor.subject_id
    return await session.scalar(
        select(ServicePrincipal.id).where(
            ServicePrincipal.tenant_id == actor.tenant_id,
            ServicePrincipal.client_id == actor.client_id,
        )
    )


def attribute(request, tenant, resource, service, operation, now, principal, mode):
    if operation is not None:
        request.state.failure_context = {
            "tenant_id": tenant,
            "source_resource_id": resource,
            "service": service,
            "operation_key": operation,
            "occurred_at": now,
            "principal_id": principal,
            "request_id": UUID(request.state.request_id),
            "safe_properties": {"authenticationType": mode},
        }


async def capture_request_failure(request, status, code):
    context = getattr(request.state, "failure_context", None)
    if context is None or status < 400 or getattr(request.state, "failure_captured", False):
        return
    request.state.failure_captured = True
    try:
        failure = FailureFact(**context, status_code=status, error_code=code)
        await capture_failure(request.app.state.database, failure)
    except Exception:
        # Deliberately omit exception text/tracebacks: drivers and validators may
        # include request-derived values. The public service error is preserved.
        LOG.warning(
            "Failure diagnostic could not be persisted; request_id=%s", request.state.request_id
        )


def storage_operation(request, service, action):
    """Explicit wire-operation names, never an arbitrary request path or label."""
    comp, method = request.query_params.get("comp"), request.method
    if service == "queue":
        return {
            "enqueue": "PutMessage",
            "receive": "GetMessages",
            "peek": "PeekMessages",
            "update_message": "UpdateMessage",
            "delete_message": "DeleteMessage",
            "clear": "ClearMessages",
            "create": "CreateQueue",
            "delete": "DeleteQueue",
            "read": "GetQueueMetadata",
            "metadata": "SetQueueMetadata",
            "list": "ListQueues",
        }.get(action)
    if service == "table":
        return {
            "query_entities": "QueryEntities",
            "read_entity": "QueryEntities",
            "insert_entity": "InsertEntity",
            "replace_entity": "UpdateEntity",
            "merge_entity": "MergeEntity",
            "delete_entity": "DeleteEntity",
            "create": "CreateTable",
            "delete": "DeleteTable",
            "list": "QueryTables",
        }.get(action)
    if service == "blob" and action in {"read_blob", "write_blob", "delete_blob", "list_blobs"}:
        if action == "list_blobs":
            return "ListBlobs"
        if method == "DELETE":
            return "DeleteBlob"
        if method in {"GET", "HEAD"}:
            return {"blocklist": "GetBlockList", "metadata": "GetBlobMetadata"}.get(
                comp, "GetBlobProperties" if method == "HEAD" else "GetBlob"
            )
        return {
            None: "PutBlob",
            "block": "PutBlock",
            "blocklist": "PutBlockList",
            "properties": "SetBlobProperties",
            "metadata": "SetBlobMetadata",
            "undelete": "UndeleteBlob",
            "snapshot": "SnapshotBlob",
            "lease": "LeaseBlob",
        }.get(comp)
    if service == "file" and action in {"read_file", "write_file", "delete_file", "list_files"}:
        directory = request.query_params.get("restype") == "directory"
        if action == "list_files":
            return "ListFilesAndDirectories"
        if method == "DELETE":
            return "DeleteDirectory" if directory else "DeleteFile"
        if method in {"GET", "HEAD"}:
            if comp == "rangelist":
                return "ListRanges"
            if comp == "metadata":
                return "GetDirectoryMetadata" if directory else "GetFileMetadata"
            return (
                "GetDirectoryProperties"
                if directory
                else ("GetFileProperties" if method == "HEAD" else "GetFile")
            )
        if directory:
            return {
                None: "CreateDirectory",
                "metadata": "SetDirectoryMetadata",
                "properties": "SetDirectoryProperties",
            }.get(comp)
        return {
            None: "CreateFile",
            "range": "PutRange",
            "metadata": "SetFileMetadata",
            "properties": "SetFileProperties",
            "lease": "LeaseFile",
        }.get(comp)
    if service == "file" and comp == "snapshot":
        return "SnapshotShare"
    names = {
        "blob": {
            "create": "CreateContainer",
            "read": "GetContainerProperties",
            "get_metadata": "GetContainerProperties",
            "metadata": "SetContainerMetadata",
            "get_acl": "GetContainerACL",
            "set_acl": "SetContainerACL",
            "delete": "DeleteContainer",
            "list": "ListContainers",
            "delegation": "GetUserDelegationKey",
        },
        "file": {
            "create": "CreateShare",
            "read": "GetShareProperties",
            "get_metadata": "GetShareProperties",
            "metadata": "SetShareMetadata",
            "get_acl": "GetShareACL",
            "set_acl": "SetShareACL",
            "delete": "DeleteShare",
            "list": "ListShares",
        },
    }
    return names.get(service, {}).get(action)
