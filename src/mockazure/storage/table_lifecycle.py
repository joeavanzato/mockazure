"""Typed Table snapshots exclude transient continuations and public write events."""

from datetime import datetime

from pydantic import Field, model_validator
from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.identity.types import IdentityInput
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.table_models import Table, TableCursor, TableEntity
from mockazure.storage.table_query import (
    check_key,
    date_value,
    normalize_properties,
    wire_properties,
)
from mockazure.storage.tables import key_hash, table_etag, table_name


class EntityInput(IdentityInput):
    partition: str
    row: str
    properties: dict
    timestamp: str
    changed_at: datetime
    etag: str

    @model_validator(mode="after")
    def checked(self):
        try:
            check_key(self.partition)
            check_key(self.row)
            if any(
                not isinstance(v, dict) or set(v) != {"type", "value"}
                for v in self.properties.values()
            ):
                raise ValueError("Invalid typed property")
            if normalize_properties(wire_properties(self.properties)) != self.properties:
                raise ValueError("Noncanonical typed property")
            if (
                date_value(self.timestamp) != self.timestamp
                or self.changed_at.tzinfo is None
                or self.etag != table_etag(self.timestamp)
            ):
                raise ValueError("Invalid Table timestamp or ETag")
        except (StorageError, TypeError, KeyError):
            raise ValueError("Invalid typed Table entity") from None
        return self


class TableInput(IdentityInput):
    name: str
    created_at: datetime
    entities: list[EntityInput] = Field(default_factory=list, max_length=100000)

    @model_validator(mode="after")
    def checked(self):
        try:
            table_name(self.name)
        except StorageError:
            raise ValueError("Invalid Table name") from None
        if self.created_at.tzinfo is None or len(
            {(r.partition, r.row) for r in self.entities}
        ) != len(self.entities):
            raise ValueError("Invalid or duplicate Table state")
        return self


async def export_tables(session, tenant, account):
    rows = list(
        await session.scalars(
            select(Table)
            .where(Table.tenant_id == tenant, Table.account_id == account)
            .order_by(Table.name_key)
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise AdminError(413, "CloudStateLimit", "Too many Tables in snapshot")
    result = []
    for table in rows:
        entities = list(
            await session.scalars(
                select(TableEntity)
                .where(TableEntity.tenant_id == tenant, TableEntity.table_id == table.id)
                .order_by(TableEntity.partition_key.collate("C"), TableEntity.row_key.collate("C"))
                .limit(100001)
            )
        )
        if len(entities) > 100000:
            raise AdminError(413, "CloudStateLimit", "Too many entities in Table snapshot")
        result.append(
            TableInput(
                name=table.name,
                created_at=table.created_at,
                entities=[
                    EntityInput(
                        partition=e.partition_key,
                        row=e.row_key,
                        properties=e.properties,
                        timestamp=e.timestamp_text,
                        changed_at=e.changed_at,
                        etag=e.etag,
                    )
                    for e in entities
                ],
            )
        )
    return result


async def restore_tables(session, tenant, account, values):
    for value in values:
        table = Table(
            tenant_id=tenant,
            account_id=account,
            name=value.name,
            name_key=value.name.lower(),
            created_at=value.created_at,
        )
        session.add(table)
        await session.flush()
        for entity in value.entities:
            session.add(
                TableEntity(
                    tenant_id=tenant,
                    table_id=table.id,
                    partition_key=entity.partition,
                    row_key=entity.row,
                    partition_hash=key_hash(entity.partition),
                    row_hash=key_hash(entity.row),
                    properties=entity.properties,
                    timestamp_text=entity.timestamp,
                    changed_at=entity.changed_at,
                    etag=entity.etag,
                )
            )
    await session.flush()


async def prune_tables(session, tenant, now, limit):
    ids = list(
        await session.scalars(
            select(TableCursor.id)
            .where(TableCursor.tenant_id == tenant, TableCursor.expires_at <= now)
            .order_by(TableCursor.expires_at, TableCursor.id)
            .limit(limit)
        )
    )
    if ids:
        await session.execute(
            delete(TableCursor).where(TableCursor.tenant_id == tenant, TableCursor.id.in_(ids))
        )
    return len(ids)
