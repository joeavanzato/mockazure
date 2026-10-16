import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    slug: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
    display_name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=253)
    licences: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("display_name", mode="before")
    @classmethod
    def trim_display(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("domain")
    @classmethod
    def dns_domain(cls, value):
        value = value.lower()
        if not value.isascii() or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in value.split(".")
        ):
            raise ValueError("domain must contain ASCII DNS labels")
        return value

    @field_validator("licences")
    @classmethod
    def licence_labels(cls, values):
        labels = [value.strip() for value in values]
        if any(not value or len(value) > 100 for value in labels):
            raise ValueError("licences must contain nonempty labels of at most 100 characters")
        return list(dict.fromkeys(labels))


class TenantView(TenantCreate):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: UUID
    revision: int
    created_at: datetime


class TenantPage(BaseModel):
    items: list[TenantView]
    next_after: UUID | None
