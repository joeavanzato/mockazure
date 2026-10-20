from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from mockazure.population.types import Input

MAX_BYTES = 8 * 1024 * 1024
FieldName = Annotated[str, Field(min_length=1, max_length=128)]
FieldValue = Annotated[str, Field(max_length=65536)]


class ContentSpec(Input):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    logical_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")
    format: Literal["txt", "csv", "json", "pdf", "docx", "xlsx", "pptx"]
    theme: Literal["finance", "hr", "engineering", "sales", "operations", "support"]
    seed: int = Field(default=42, ge=-(2**63), lt=2**63)
    revision: int = Field(default=1, ge=1, le=1000000)
    fields: dict[FieldName, FieldValue] = Field(default_factory=dict, max_length=128)

    @field_validator("fields")
    @classmethod
    def printable(cls, fields):
        for value in (*fields.keys(), *fields.values()):
            if any(
                not (
                    c in "\t\n\r"
                    or 0x20 <= ord(c) <= 0xD7FF
                    or 0xE000 <= ord(c) <= 0xFFFD
                    or 0x10000 <= ord(c) <= 0x10FFFF
                )
                for c in value
            ):
                raise ValueError("Content contains characters invalid in XML")
        return fields

    @model_validator(mode="after")
    def bounded(self):
        # Bound input before XML escaping, layout, or compression can allocate large buffers.
        if sum(len(k.encode()) + len(v.encode()) for k, v in self.fields.items()) > 512 * 1024:
            raise ValueError("Document fields exceed 512 KiB; use opaque streaming content")
        return self


@dataclass(frozen=True)
class RenderedContent:
    media_type: str
    filename: str
    length: int
    sha256: str
    chunks: Callable[[], Iterator[bytes]]

    def metadata(self):
        return self.length, self.sha256
