"""Credential modes remain distinct from locally issued OAuth identities."""

from dataclasses import dataclass
from uuid import UUID

from mockazure.identity.types import PrincipalContext


@dataclass(frozen=True)
class StorageAccess:
    tenant_id: UUID
    account_id: str
    mode: str
    principal: PrincipalContext | None
    permissions: frozenset[str]


@dataclass(frozen=True)
class ContentVersion:
    id: UUID
    content_id: UUID
    etag: str
    size: int
