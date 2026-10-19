"""Typed local role-state lifecycle; independent of display-only evidence."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mockazure.mail.role_management_contracts import GROUP_ROLES, ROLE_COMMANDS


class RoleMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_name: str
    principal_id: UUID

    @field_validator("group_name")
    @classmethod
    def supported_group(cls, v):
        if v not in GROUP_ROLES:
            raise ValueError("Unsupported role group")
        return v


class RoleAssignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: str = Field(min_length=1, max_length=128)
    role: str
    principal_id: UUID | None = None
    group_name: str | None = None
    recipient_group_id: UUID | None = None

    @model_validator(mode="after")
    def supported_assignment(self):
        if not self.name.strip() or "\0" in self.name:
            raise ValueError("Invalid name")
        if self.role not in ROLE_COMMANDS:
            raise ValueError("Unsupported role")
        if (self.principal_id is None) == (self.group_name is None):
            raise ValueError("One assignee required")
        if self.group_name is not None and self.group_name not in GROUP_ROLES:
            raise ValueError("Unsupported role group")
        if self.recipient_group_id is not None and self.role != "Mail Recipients":
            raise ValueError("Only Mail Recipients supports recipient group scope in this profile")
        return self


class RoleState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    members: list[RoleMemberInput] = Field(default_factory=list, max_length=10000)
    assignments: list[RoleAssignmentInput] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def unique(self):
        keys = [(r.group_name, r.principal_id) for r in self.members]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate membership")
        if len(self.assignments) != len({r.id for r in self.assignments}) or len(
            self.assignments
        ) != len({r.name.casefold() for r in self.assignments}):
            raise ValueError("Duplicate assignment")
        return self
