"""Explicit log snapshots, tenant reset and bounded retention cleanup."""

from collections import defaultdict
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import Integer, cast, delete, func, literal_column, select

from mockazure.admin.errors import AdminError
from mockazure.identity.types import IdentityInput
from mockazure.monitor.log_models import LogRecord
from mockazure.monitor.logs import ingest_log_rows
from mockazure.monitor.tables import validate_row
from mockazure.monitor.workspaces import Workspace
from mockazure.providers.arm.models import ArmResource


class LogInput(IdentityInput):
    workspace_id: UUID
    table: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1, max_length=512)
    payload: dict

    @model_validator(mode="after")
    def typed(self):
        self.payload = validate_row(self.table, self.payload)
        return self


class LogsStateInput(IdentityInput):
    records: list[LogInput] = Field(default_factory=list, max_length=100000)

    @model_validator(mode="after")
    def unique(self):
        if len({(r.workspace_id, r.table, r.source_id) for r in self.records}) != len(self.records):
            raise ValueError("Duplicate log source identity")
        return self


async def export_logs(session, tenant):
    rows = list(
        await session.scalars(
            select(LogRecord)
            .where(LogRecord.tenant_id == tenant)
            .order_by(LogRecord.workspace_id, LogRecord.table_name, LogRecord.source_id)
            .limit(100001)
        )
    )
    if len(rows) > 100000:
        raise AdminError(413, "CloudStateLimit", "Log snapshot exceeds local limit")
    # Restored fixtures are imported data, never new native delivery authority.
    return LogsStateInput(
        records=[
            LogInput(
                workspace_id=r.workspace_id,
                table=r.table_name,
                source_id=r.source_id,
                payload=r.payload,
            )
            for r in rows
        ]
    )


async def replace_logs(session, tenant, value):
    grouped = defaultdict(list)
    for record in value.records:
        grouped[record.workspace_id, record.table].append(record)
    async with session.begin_nested():
        await session.execute(delete(LogRecord).where(LogRecord.tenant_id == tenant))
        try:
            for (workspace, table), records in grouped.items():
                for start in range(0, len(records), 10000):
                    batch = records[start : start + 10000]
                    await ingest_log_rows(
                        session,
                        tenant,
                        workspace,
                        table,
                        [r.payload for r in batch],
                        [r.source_id for r in batch],
                    )
        except ValueError:
            raise AdminError(
                422, "InvalidCloudState", "Log records require owned workspace parents"
            ) from None


async def prune_logs(session, tenant, now, limit):
    if not 1 <= limit <= 1000 or now.tzinfo is None:
        raise ValueError("Invalid log cleanup bounds")
    days = func.coalesce(
        cast(ArmResource.payload["properties"]["retentionInDays"].as_string(), Integer), 30
    )
    ids = list(
        await session.scalars(
            select(LogRecord.id)
            .join(
                Workspace,
                (Workspace.tenant_id == LogRecord.tenant_id)
                & (Workspace.customer_id == LogRecord.workspace_id),
            )
            .join(
                ArmResource,
                (ArmResource.tenant_id == Workspace.tenant_id)
                & (ArmResource.scope_key == Workspace.scope_key),
            )
            .where(
                LogRecord.tenant_id == tenant,
                days > 0,
                LogRecord.time_generated < now - days * literal_column("interval '1 day'"),
            )
            .order_by(LogRecord.time_generated, LogRecord.id)
            .limit(limit)
        )
    )
    if ids:
        await session.execute(
            delete(LogRecord).where(LogRecord.tenant_id == tenant, LogRecord.id.in_(ids))
        )
    return len(ids)
