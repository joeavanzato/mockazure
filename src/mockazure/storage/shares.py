"""Azure Files REST share namespace, separate from SMB/NFS protocols."""

from mockazure.storage.containers import create_namespace


async def create_share(session, access, name, properties, now) -> dict:
    return await create_namespace(session, access, "file", name, properties, now)
