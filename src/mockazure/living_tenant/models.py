"""Declared operation scope; inventory entries are not claims of implementation."""

from typing import Literal

from pydantic import Field

from mockazure.population.types import Input


class OperationContract(Input):
    key: str
    area: Literal["content", "evidence", "identity", "hunting"]
    method: Literal["GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"]
    path: str
    status: Literal["existing", "planned", "fixture"]
    request_type: str | None = None
    response_type: str | None = None
    permissions: list[str] = Field(default_factory=list)
    role_constraints: list[str] = Field(default_factory=list)
    entitlement_constraints: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(min_length=1)
    supported_variants: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
