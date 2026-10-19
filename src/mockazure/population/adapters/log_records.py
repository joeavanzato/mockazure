"""Typed immutable log ingestion with stable source identities."""

from uuid import UUID

from pydantic import Field
from sqlalchemy import String, cast, literal

from mockazure.monitor.log_models import LogRecord
from mockazure.monitor.logs import ingest_log_rows
from mockazure.monitor.tables import validate_row
from mockazure.population.adapters.arm import json_object
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import FamilyDescriptor, Input, OperationDescriptor


class LogInput(Input):
    id: str = Field(min_length=1, max_length=512)
    record: dict


class LogAdapter(PayloadAdapter):
    def normalize(self, mutation):
        result = super().normalize(mutation)
        workspace, table = (mutation.parent or "").split("/", 1)
        UUID(workspace)
        result["record"] = validate_row(table, result["record"])
        return result

    def matches(self, existing, desired):
        return existing["id"] == desired["id"] and all(
            existing["record"].get(k) == v for k, v in desired["record"].items()
        )


def adapters():
    async def write(session, ctx, pending):
        for mutation, payload in pending:
            workspace, table = mutation.parent.split("/", 1)
            await ingest_log_rows(
                session, ctx.tenant_id, UUID(workspace), table, [payload["record"]], [payload["id"]]
            )

    row = LogRecord
    payload = json_object(
        id=row.source_id, record=row.payload, table=row.table_name, TimeGenerated=row.time_generated
    )
    return [
        LogAdapter(
            FamilyDescriptor(
                key="monitor.records",
                service="Logs and diagnostics",
                api_path="/v1/workspaces/{workspace}/query",
                display_fields=["id", "table", "TimeGenerated"],
                operations=[
                    OperationDescriptor(
                        key="import",
                        mode="fixture",
                        method="POST",
                        api_version="local-fixture-v1",
                        path_template="/_mock/tenants/{tenant}/cloud/workspaces/{workspace}/logs/{table}",
                        input_schema=LogInput.model_json_schema(),
                    )
                ],
                contract_refs=["docs/log-analytics.md"],
                limitations=[
                    "Parent is workspace customer UUID/table name. Existing source IDs are "
                    "immutable."
                ],
            ),
            ReadSpec(
                row,
                row.source_id,
                payload,
                row.time_generated,
                parent=cast(row.workspace_id, String) + literal("/") + row.table_name,
            ),
            lambda value: LogInput.model_validate(value).model_dump(mode="json"),
            write,
        )
    ]
