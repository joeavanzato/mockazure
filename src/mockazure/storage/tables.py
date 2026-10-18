"""Table mutations share caller transactions, current authorization, and native facts."""

import hashlib
import re
from datetime import timedelta
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import delete, select, tuple_

from mockazure.cloud.models import CloudState
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.containers import record
from mockazure.storage.table_models import Table, TableCursor, TableEntity
from mockazure.storage.table_query import (
    check_key,
    compile_filter,
    date_value,
    invalid,
    normalize_properties,
    parse_table_filter,
    property_name,
    wire_properties,
)


def table_name(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,62}", value)
        or value.lower() == "tables"
    ):
        invalid("Invalid Table name", "InvalidResourceName")
    return value.lower()


def key_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def find_table(session, access, name):
    row = await session.scalar(
        select(Table).where(
            Table.tenant_id == access.tenant_id,
            Table.account_id == access.account_id,
            Table.name_key == table_name(name),
        )
    )
    if row is None:
        raise StorageError(404, "TableNotFound", "The specified table does not exist")
    return row


async def create_table(session, access, name, now):
    key = table_name(name)
    if await session.scalar(
        select(Table.id).where(
            Table.tenant_id == access.tenant_id,
            Table.account_id == access.account_id,
            Table.name_key == key,
        )
    ):
        raise StorageError(409, "TableAlreadyExists", "The specified table already exists")
    row = Table(
        tenant_id=access.tenant_id,
        account_id=access.account_id,
        name=name,
        name_key=key,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    await record(session, access, "table", name, "CreateTable", now)
    return row


async def delete_table(session, access, name, now):
    row = await find_table(session, access, name)
    await session.execute(
        delete(TableCursor).where(
            TableCursor.tenant_id == access.tenant_id, TableCursor.table_id == row.id
        )
    )
    await session.delete(row)
    await record(session, access, "table", name, "DeleteTable", now)


async def find_entity(session, access, table, partition, row):
    check_key(partition)
    check_key(row)
    entity = await session.scalar(
        select(TableEntity)
        .where(
            TableEntity.tenant_id == access.tenant_id,
            TableEntity.table_id == table.id,
            TableEntity.partition_hash == key_hash(partition),
            TableEntity.row_hash == key_hash(row),
        )
        .with_for_update()
    )
    if entity is not None and (entity.partition_key != partition or entity.row_key != row):
        raise StorageError(409, "EntityAlreadyExists", "Key hash collision")
    return entity


def table_etag(timestamp):
    return "W/\"datetime'" + quote(timestamp, safe="") + "'\""


def entity_view(entity, mode="minimalmetadata", projection=None):
    body = {
        "PartitionKey": entity.partition_key,
        "RowKey": entity.row_key,
        "Timestamp": entity.timestamp_text,
    } | wire_properties(entity.properties, mode)
    if projection is not None:
        body = {k: v for k, v in body.items() if k.split("@")[0] in projection}
    if mode == "fullmetadata":
        body["odata.etag"] = entity.etag
        for k in SYSTEM_TYPES:
            if k in body:
                body[k + "@odata.type"] = SYSTEM_TYPES[k]
    return body


SYSTEM_TYPES = {"PartitionKey": "Edm.String", "RowKey": "Edm.String", "Timestamp": "Edm.DateTime"}


async def mutate_entity(session, access, table, partition, row, operation, body, etag, now):
    parent = await find_table(session, access, table)
    current = await find_entity(session, access, parent, partition, row)
    if operation not in {"insert", "merge", "replace", "delete"}:
        invalid("Unknown entity operation")
    if not isinstance(body, dict):
        invalid()
    if any(k in body and body[k] != v for k, v in [("PartitionKey", partition), ("RowKey", row)]):
        invalid("Body keys differ from URI")
    if operation == "insert" and current is not None:
        raise StorageError(409, "EntityAlreadyExists", "The specified entity already exists")
    if current is None and (etag is not None or operation == "delete"):
        raise StorageError(404, "ResourceNotFound", "The specified entity does not exist")
    if current is not None and etag not in {None, "*", current.etag}:
        raise StorageError(
            412, "UpdateConditionNotSatisfied", "The update condition was not satisfied"
        )
    if operation == "delete":
        if etag is None:
            invalid("If-Match is required for Delete Entity")
        await session.delete(current)
        output = {"body": None, "etag": None, "status": 204}
    else:
        properties = normalize_properties(body)
        if operation == "merge" and current is not None:
            properties = current.properties | properties
            # Revalidate final size/count, not merely the supplied patch.
            properties = normalize_properties(wire_properties(properties))
        if current is None:
            current = TableEntity(
                tenant_id=access.tenant_id,
                table_id=parent.id,
                partition_key=partition,
                row_key=row,
                partition_hash=key_hash(partition),
                row_hash=key_hash(row),
            )
            session.add(current)
        current.properties = properties
        stamp = (
            now
            if current.changed_at is None
            else max(now, current.changed_at + timedelta(microseconds=1))
        )
        current.changed_at = stamp
        current.timestamp_text = date_value(stamp.isoformat())
        current.etag = table_etag(current.timestamp_text)
        await session.flush()
        output = {
            "body": entity_view(current),
            "etag": current.etag,
            "status": 201 if operation == "insert" else 204,
        }
    await record(
        session,
        access,
        "table",
        parent.name,
        {
            "insert": "InsertEntity",
            "merge": "MergeEntity",
            "replace": "UpdateEntity",
            "delete": "DeleteEntity",
        }[operation],
        now,
    )
    return output


async def transact_entities(session, access, table, operations, now):
    if not 1 <= len(operations) <= 100:
        invalid("Batch must have 1 to 100 operations")
    keys = [(op["partition"], op["row"]) for op in operations]
    if len(set(keys)) != len(keys) or len({p for p, r in keys}) != 1:
        invalid("Batch requires distinct entities in one partition")
    for p, r in keys:
        check_key(p)
        check_key(r)
    results = []
    async with session.begin_nested():
        for i, op in enumerate(operations):
            try:
                results.append(
                    await mutate_entity(
                        session,
                        access,
                        table,
                        op["partition"],
                        op["row"],
                        op["operation"],
                        op["body"],
                        op["etag"],
                        now,
                    )
                )
            except StorageError as error:
                error.batch_index = i
                raise
    return results


def range_predicate(model, bounds):
    clauses = []
    p = model.partition_key.collate("C")
    r = model.row_key.collate("C")
    if bounds.get("spk") is not None:
        clauses.append(
            tuple_(p, r) >= (bounds["spk"], bounds["srk"])
            if bounds.get("srk") is not None
            else p >= bounds["spk"]
        )
    if bounds.get("epk") is not None:
        clauses.append(
            tuple_(p, r) <= (bounds["epk"], bounds["erk"])
            if bounds.get("erk") is not None
            else p <= bounds["epk"]
        )
    return clauses


async def query_table(session, access, name, args, now, mode, bounds=None):
    parent = await find_table(session, access, name) if name else None
    model = TableEntity if parent else Table
    maximum = min(int(args.get("$top", "1000")), 1000)
    if maximum < 1:
        invalid("$top must be positive")
    projection = None
    if "$select" in args:
        projection = args["$select"].split(",")
        if not projection or len(set(projection)) != len(projection):
            invalid("Invalid projection")
        for item in projection:
            property_name(item)
        if parent is None and projection != ["TableName"]:
            invalid("Invalid Table projection")
    ast = parse_table_filter(args.get("$filter", ""))
    query = {
        "filter": args.get("$filter", ""),
        "select": projection,
        "top": maximum,
        "bounds": bounds or {},
    }
    generation = (await session.get(CloudState, access.tenant_id)).generation
    marker = args.get("NextPartitionKey") if parent else args.get("NextTableName")
    last = None
    if marker:
        try:
            identifier = UUID(marker)
        except ValueError:
            invalid("Invalid continuation")
        cursor = await session.get(TableCursor, (access.tenant_id, identifier))
        if (
            cursor is None
            or cursor.account_id != access.account_id
            or cursor.table_id != (parent.id if parent else None)
            or cursor.generation != generation
            or cursor.query != query
            or cursor.expires_at <= now
            or (parent and args.get("NextRowKey") != marker)
        ):
            invalid("Expired or mismatched continuation")
        last = cursor.last_key
    elif args.get("NextRowKey"):
        invalid("Missing partition continuation")
    query_sql = select(model).where(
        model.tenant_id == access.tenant_id, compile_filter(ast, model, tables=parent is None)
    )
    if parent:
        keys = [model.partition_key.collate("C"), model.row_key.collate("C")]
        query_sql = query_sql.where(
            model.table_id == parent.id, *range_predicate(model, bounds or {})
        )
    else:
        keys = [model.name_key.collate("C")]
        query_sql = query_sql.where(model.account_id == access.account_id)
    if last:
        query_sql = query_sql.where(
            tuple_(*keys) > tuple(last) if len(keys) > 1 else keys[0] > last[0]
        )
    rows = list(await session.scalars(query_sql.order_by(*keys).limit(maximum + 1)))
    headers = {}
    if len(rows) > maximum:
        end = rows[maximum - 1]
        cursor = TableCursor(
            tenant_id=access.tenant_id,
            account_id=access.account_id,
            table_id=parent.id if parent else None,
            generation=generation,
            query=query,
            last_key=[end.partition_key, end.row_key] if parent else [end.name_key],
            expires_at=now + timedelta(hours=1),
        )
        session.add(cursor)
        await session.flush()
        for suffix in ["NextPartitionKey", "NextRowKey"] if parent else ["NextTableName"]:
            headers["x-ms-continuation-" + suffix] = str(cursor.id)
    return (
        [
            entity_view(r, mode, projection) if parent else {"TableName": r.name}
            for r in rows[:maximum]
        ],
        headers,
    )
