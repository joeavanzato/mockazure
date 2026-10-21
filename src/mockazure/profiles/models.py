"""Versioned profile inputs, separate from provider payload contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, strict=True)


class ParameterDefinition(ProfileInput):
    type: Literal["integer", "number", "string", "boolean"]
    default: JsonValue = None
    minimum: float | None = None
    maximum: float | None = None
    shared: bool = False

    @model_validator(mode="after")
    def bounds(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Invalid parameter bounds")
        return self


class ResourceDefinition(ProfileInput):
    family: str = Field(min_length=1, max_length=128)
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    override: bool = False


class StepDefinition(ProfileInput):
    mode: Literal["fixture", "service"]
    family: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    target: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    dependsOn: list[str] = Field(default_factory=list)
    override: bool = False


class ConsoleDefaults(ProfileInput):
    """Optional UI defaults, stored with a template; never an execution target."""

    datasetNamespace: str = Field(default="my-lab", max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    createTenant: bool = False
    tenantSlug: str = Field(default="my-lab", max_length=128)
    tenantDomain: str = Field(default="my-lab.test", max_length=253)
    displayName: str = Field(default="My lab", max_length=256)
    activityEnabled: bool = False
    activityStep: str = Field(default="calendar", max_length=128)
    actionCount: int = Field(default=10, ge=1, le=100000)
    pacingMs: int = Field(default=0, ge=0, le=60000)


class ProfileDocument(ProfileInput):
    schemaVersion: Literal[1] = 1
    kind: Literal["tenant", "data", "template"]
    name: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")
    extends: str | None = None
    parameterDefinitions: dict[str, ParameterDefinition] = Field(default_factory=dict)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    resources: dict[str, ResourceDefinition] = Field(default_factory=dict)
    steps: dict[str, StepDefinition] = Field(default_factory=dict)
    tenantProfile: str | None = None
    dataProfiles: list[str] = Field(default_factory=list)
    consoleDefaults: ConsoleDefaults | None = None

    @model_validator(mode="after")
    def kind_fields(self):
        forbidden = (
            {"resources", "steps", "parameterDefinitions"}
            if self.kind == "template"
            else {"tenantProfile", "dataProfiles", "consoleDefaults"}
        )
        if forbidden & self.model_fields_set:
            raise ValueError("Fields do not apply to this profile kind")
        for keys in (self.parameterDefinitions, self.resources, self.steps):
            if any(not key or len(key) > 128 or "/" in key or "\\" in key for key in keys):
                raise ValueError("Invalid logical key")
        return self


class ProfileBundle(ProfileInput):
    documents: dict[str, ProfileDocument]


class ResolvedProfile(ProfileInput):
    schemaVersion: Literal[1] = 1
    template: str
    parameters: dict[str, JsonValue]
    resources: dict[str, ResourceDefinition]
    steps: dict[str, StepDefinition]
    origins: dict[str, str]
    referenceTime: datetime
    seed: int
    digest: str
