"""Transactional Blob leases; lease changes preserve content validators."""

from datetime import timedelta
from math import ceil
from uuid import UUID, uuid4

from mockazure.providers.storage.errors import StorageError
from mockazure.storage.blob_models import BlobLease


def lease_state(row, now):
    if row is None:
        return "available"
    if row.break_at is not None:
        return "broken" if row.break_at <= now else "breaking"
    if row.expires_at is not None and row.expires_at <= now:
        return "expired"
    return "leased"


async def lease_headers(session, blob, now):
    row = await session.get(BlobLease, (blob.tenant_id, blob.id))
    state = lease_state(row, now)
    result = {
        "x-ms-lease-state": state,
        "x-ms-lease-status": "locked" if state in {"leased", "breaking"} else "unlocked",
    }
    if state == "leased":
        result["x-ms-lease-duration"] = "infinite" if row.duration == -1 else "fixed"
    return result


async def require_lease(session, blob, supplied, now, *, write=False):
    row = await session.get(BlobLease, (blob.tenant_id, blob.id))
    state = lease_state(row, now)
    if state in {"leased", "breaking"}:
        if (write or supplied) and supplied != str(row.lease_id):
            raise StorageError(
                412,
                "LeaseIdMissing" if not supplied else "LeaseIdMismatchWithBlobOperation",
                "An active lease requires its current ID",
            )
    elif supplied:
        raise StorageError(
            412, "LeaseNotPresentWithBlobOperation", "No active lease matches this request"
        )
    elif write and row is not None:
        await session.delete(row)
        await session.flush()


def uuid_header(value):
    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise StorageError(400, "InvalidHeaderValue", "Invalid lease identifier") from None


async def operate_lease(session, blob, version, action, values, now):
    if version is None:
        raise StorageError(404, "BlobNotFound", "Blob was not found")
    row = await session.get(BlobLease, (blob.tenant_id, blob.id))
    state = lease_state(row, now)
    supplied = values.get("x-ms-lease-id")
    if action == "acquire":
        duration = int(values.get("x-ms-lease-duration", "0"))
        if duration != -1 and not 15 <= duration <= 60:
            raise StorageError(400, "InvalidHeaderValue", "Lease duration must be -1 or 15..60")
        proposed = values.get("x-ms-proposed-lease-id")
        if state == "breaking" or (state == "leased" and proposed != str(row.lease_id)):
            raise StorageError(409, "LeaseAlreadyPresent", "Blob already has a lease")
        identifier = (
            uuid_header(values["x-ms-proposed-lease-id"])
            if values.get("x-ms-proposed-lease-id")
            else uuid4()
        )
        if row is not None:
            await session.delete(row)
            await session.flush()
        row = BlobLease(
            tenant_id=blob.tenant_id,
            blob_id=blob.id,
            lease_id=identifier,
            duration=duration,
            expires_at=None if duration == -1 else now + timedelta(seconds=duration),
            break_at=None,
            etag=version.etag,
        )
        session.add(row)
        await session.flush()
        return 201, {"x-ms-lease-id": str(identifier)}
    if action == "break":
        if row is None or state in {"available", "broken"}:
            raise StorageError(409, "LeaseNotPresentWithLeaseOperation", "No lease is present")
        default = max(0, ceil((row.expires_at - now).total_seconds())) if row.expires_at else 0
        period = int(values.get("x-ms-lease-break-period", str(default)))
        if not 0 <= period <= 60:
            raise StorageError(400, "InvalidHeaderValue", "Break period must be 0..60")
        until = now + timedelta(seconds=period)
        row.break_at = min(x for x in (until, row.expires_at, row.break_at) if x is not None)
        await session.flush()
        return 202, {"x-ms-lease-time": str(max(0, ceil((row.break_at - now).total_seconds())))}
    if row is None or supplied != str(row.lease_id):
        raise StorageError(409, "LeaseIdMismatchWithLeaseOperation", "Lease ID does not match")
    if action == "renew":
        if row.break_at is not None or row.etag != version.etag:
            raise StorageError(409, "LeaseLost", "Lease can no longer be renewed")
        row.expires_at = None if row.duration == -1 else now + timedelta(seconds=row.duration)
        await session.flush()
        return 200, {"x-ms-lease-id": str(row.lease_id)}
    if action == "release":
        await session.delete(row)
        await session.flush()
        return 200, {}
    if action == "change":
        if state != "leased":
            raise StorageError(409, "LeaseLost", "Lease is not active")
        row.lease_id = uuid_header(values.get("x-ms-proposed-lease-id"))
        await session.flush()
        return 200, {"x-ms-lease-id": str(row.lease_id)}
    raise StorageError(400, "InvalidHeaderValue", "Unsupported lease action")
