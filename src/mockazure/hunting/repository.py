"""Tenant-scoped immutable records, atomic source replay and bounded administration."""

import base64
import hashlib
import json

from sqlalchemy import delete, insert, select

from mockazure.db.models import Tenant
from mockazure.hunting.contracts import table_contract
from mockazure.hunting.models import HuntingRecord
from mockazure.hunting.schemas import HuntingProvenance, validate_row
from mockazure.monitor.tables import instant
from mockazure.population.types import ResourceItem, ResourcePage


async def ingest_rows(session, tenant_id, table, rows, source_ids, provenance: HuntingProvenance):
    table_contract(table)
    provenance = HuntingProvenance.model_validate(provenance).model_dump()
    if not isinstance(rows, list) or len(rows) > 10000 or len(rows) != len(source_ids):
        raise ValueError("Hunting batches allow at most 10000 rows and aligned source IDs")
    if any(
        not isinstance(s, str) or not 1 <= len(s) <= 512 or "\0" in s for s in source_ids
    ) or len(set(source_ids)) != len(source_ids):
        raise ValueError("Hunting source IDs must be distinct nonempty strings")
    if len(json.dumps(rows, allow_nan=False, ensure_ascii=False).encode()) > 8 * 1024 * 1024:
        raise ValueError("Hunting batch exceeds eight MiB")
    values = [validate_row(table, row) for row in rows]
    if (
        await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
        is None
    ):
        raise ValueError("Hunting tenant not found")
    pending = []
    for offset in range(0, len(values), 500):
        existing = {
            r.source_id: r
            for r in await session.scalars(
                select(HuntingRecord).where(
                    HuntingRecord.tenant_id == tenant_id,
                    HuntingRecord.table_name == table,
                    HuntingRecord.source_id.in_(source_ids[offset : offset + 500]),
                )
            )
        }
        for payload, source_id in zip(
            values[offset : offset + 500], source_ids[offset : offset + 500], strict=True
        ):
            if source_id in existing:
                prior = existing[source_id]
                if prior.payload != payload or prior.provenance != provenance:
                    raise ValueError("Hunting source identity already has different content")
                continue
            pending.append(
                {
                    "tenant_id": tenant_id,
                    "table_name": table,
                    "source_id": source_id,
                    "timestamp": instant(payload["Timestamp"]),
                    "account_id": payload.get("AccountObjectId") or payload.get("AccountId"),
                    "device_id": payload.get("DeviceId"),
                    "payload": payload,
                    "provenance": provenance,
                    "digest": hashlib.sha256(
                        json.dumps(
                            [payload, provenance],
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest(),
                }
            )
    # Validate the complete batch/replay before the first insert. The savepoint also
    # preserves atomicity if a PostgreSQL bound is rejected by a later chunk.
    async with session.begin_nested():
        for offset in range(0, len(pending), 500):
            await session.execute(insert(HuntingRecord), pending[offset : offset + 500])
    return len(pending)


async def export_rows(session, tenant_id, table, *, limit=500, after=None):
    table_contract(table)
    if type(limit) is not int or not 1 <= limit <= 501:
        raise ValueError("Hunting export is paged with at most 500 records plus lookahead")
    rows = await session.scalars(
        select(HuntingRecord)
        .where(
            HuntingRecord.tenant_id == tenant_id,
            HuntingRecord.table_name == table,
            HuntingRecord.source_id > after if after is not None else True,
        )
        .order_by(HuntingRecord.source_id)
        .limit(limit)
    )
    result = [
        {
            "id": r.source_id,
            "table": table,
            "record": r.payload,
            "provenance": r.provenance,
            "version": r.digest,
        }
        for r in rows
    ]
    if len(json.dumps(result, ensure_ascii=False).encode()) > 8 * 1024 * 1024:
        raise ValueError("Hunting export page exceeds eight MiB; request a smaller limit")
    return result


async def browse_rows(session, tenant_id, table, query):
    if query.filters or query.parent or query.identifier:
        raise ValueError("Hunting repository pages support limit and continuation")
    after = None
    if query.continuation:
        try:
            cursor = json.loads(base64.urlsafe_b64decode(query.continuation))
            if (
                set(cursor) != {"tenant", "table", "after"}
                or (cursor["tenant"], cursor["table"]) != (str(tenant_id), table)
                or not isinstance(cursor["after"], str)
            ):
                raise ValueError()
            after = cursor["after"]
        except (ValueError, TypeError):
            raise ValueError("Invalid hunting continuation") from None
    rows = await export_rows(session, tenant_id, table, limit=query.limit + 1, after=after)
    continuation = None
    if len(rows) > query.limit:
        continuation = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "tenant": str(tenant_id),
                    "table": table,
                    "after": rows[query.limit - 1]["id"],
                }
            ).encode()
        ).decode()
    return ResourcePage(
        items=[
            ResourceItem(
                id=r["id"],
                properties={k: v for k, v in r.items() if k != "version"},
                version=r["version"],
            )
            for r in rows[: query.limit]
        ],
        continuation=continuation,
    )


async def prune_rows(session, tenant_id, before):
    if before.tzinfo is None:
        raise ValueError("Hunting retention boundary requires a timezone")
    await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
    result = await session.execute(
        delete(HuntingRecord).where(
            HuntingRecord.tenant_id == tenant_id,
            HuntingRecord.timestamp < before,
        )
    )
    return result.rowcount
