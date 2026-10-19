"""Persisted namespace mutations; callers hold the account transaction locks."""

import re
from uuid import uuid4

from mockazure.cloud.events import append_service_event
from mockazure.cloud.types import ServiceEvent
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.models import StorageNamespace


def check_name(name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", name) or "--" in name:
        raise StorageError(400, "InvalidResourceName", "Invalid container or share name")


def view(row):
    return {
        "name": row.name,
        "etag": row.etag,
        "created_at": row.created_at,
        "changed_at": row.changed_at,
        "properties": row.properties,
    }


async def record(session, access, service, name, operation, now):
    await append_service_event(
        session,
        ServiceEvent(
            uuid4(),
            access.tenant_id,
            access.account_id,
            service,
            "StorageWrite",
            operation,
            now,
            {"name": name, "authenticationType": access.mode, "outcome": "Succeeded"},
        ),
    )


async def create_namespace(session, access, service, name, properties, now):
    check_name(name)
    key = (access.tenant_id, access.account_id, service, name)
    if await session.get(StorageNamespace, key):
        raise StorageError(
            409,
            "ContainerAlreadyExists" if service == "blob" else "ShareAlreadyExists",
            "The resource already exists",
        )
    row = StorageNamespace(
        tenant_id=key[0],
        account_id=key[1],
        service=service,
        name=name,
        etag='"' + uuid4().hex + '"',
        created_at=now,
        changed_at=now,
        properties=properties,
        policies=[],
    )
    session.add(row)
    await record(
        session,
        access,
        service,
        name,
        "CreateContainer" if service == "blob" else "CreateShare",
        now,
    )
    await session.flush()
    return view(row)


async def create_container(session, access, name, properties, now) -> dict:
    return await create_namespace(session, access, "blob", name, properties, now)
