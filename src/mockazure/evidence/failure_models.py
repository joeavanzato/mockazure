"""Allowlisted internal failure facts; request bodies and URLs are never accepted."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from mockazure.evidence.schemas import StrictModel
from mockazure.providers.arm.scopes import parse_scope

OPERATIONS = {
    "blob": frozenset(
        (
            "GetBlob",
            "GetBlobProperties",
            "GetBlobMetadata",
            "PutBlob",
            "PutBlock",
            "PutBlockList",
            "GetBlockList",
            "SetBlobProperties",
            "SetBlobMetadata",
            "DeleteBlob",
            "UndeleteBlob",
            "ListBlobs",
            "SnapshotBlob",
            "LeaseBlob",
            "GetContainerProperties",
            "GetContainerACL",
            "SetContainerACL",
            "SetContainerMetadata",
            "CreateContainer",
            "DeleteContainer",
            "ListContainers",
            "GetUserDelegationKey",
        )
    ),
    "file": frozenset(
        (
            "GetFile",
            "GetFileProperties",
            "GetFileMetadata",
            "CreateFile",
            "PutRange",
            "ListRanges",
            "SetFileProperties",
            "SetFileMetadata",
            "DeleteFile",
            "LeaseFile",
            "CreateDirectory",
            "GetDirectoryProperties",
            "GetDirectoryMetadata",
            "SetDirectoryProperties",
            "SetDirectoryMetadata",
            "DeleteDirectory",
            "ListFilesAndDirectories",
            "GetShareProperties",
            "GetShareACL",
            "SetShareACL",
            "SetShareMetadata",
            "CreateShare",
            "DeleteShare",
            "SnapshotShare",
            "DeleteShareSnapshot",
            "ListShares",
        )
    ),
    "queue": frozenset(
        (
            "PutMessage",
            "GetMessages",
            "PeekMessages",
            "UpdateMessage",
            "DeleteMessage",
            "ClearMessages",
            "CreateQueue",
            "DeleteQueue",
            "GetQueueMetadata",
            "SetQueueMetadata",
            "ListQueues",
        )
    ),
    "table": frozenset(
        (
            "QueryTables",
            "CreateTable",
            "DeleteTable",
            "QueryEntities",
            "InsertEntity",
            "UpdateEntity",
            "MergeEntity",
            "DeleteEntity",
        )
    ),
    "keyvault": frozenset(
        (
            "SecretGet",
            "SecretSet",
            "SecretUpdate",
            "SecretDelete",
            "SecretList",
            "SecretListVersions",
            "SecretBackup",
            "SecretRestore",
            "SecretRecover",
            "SecretPurge",
        )
    ),
}
AUTHENTICATION_TYPES = frozenset(
    (
        "oauth",
        "application",
        "delegated",
        "shared_key",
        "service_sas",
        "account_sas",
        "user_delegation_sas",
    )
)


class FailureFact(StrictModel):
    tenant_id: UUID
    source_resource_id: str = Field(max_length=2048)
    service: Literal["blob", "file", "table", "queue", "keyvault"]
    operation_key: str
    request_id: UUID
    occurred_at: AwareDatetime
    principal_id: UUID | None
    status_code: int = Field(ge=400, le=599, strict=True)
    error_code: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    safe_properties: dict

    @model_validator(mode="after")
    def allowed(self):
        scope = parse_scope(self.source_resource_id)
        provider = (
            "microsoft.keyvault/vaults"
            if self.service == "keyvault"
            else ("microsoft.storage/storageaccounts")
        )
        if (
            len(scope.resource_parts) != 4
            or "/".join(scope.resource_parts[1:3]).lower() != provider
            or self.operation_key not in OPERATIONS[self.service]
            or set(self.safe_properties) != {"authenticationType"}
            or not isinstance(self.safe_properties["authenticationType"], str)
            or self.safe_properties["authenticationType"] not in AUTHENTICATION_TYPES
        ):
            raise ValueError("Invalid failure source, operation or safe properties")
        return self
