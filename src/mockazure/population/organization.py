"""Versioned random-access business entities; relationships are logical, not Graph IDs."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mockazure.profiles.identity import stable_id

VERSION = "organization-v1"
DEPARTMENTS = ("Finance", "Human Resources", "Engineering", "Sales", "Operations", "Support")
LOCATIONS = ("New York", "London", "Tokyo", "Berlin", "Toronto", "Sydney")
GIVEN = ("Alex", "Samira", "Morgan", "Ravi", "Sofia", "Jordan", "Amara", "Kai")
SURNAMES = ("Chen", "Patel", "Rivera", "Okafor", "Martin", "Kim", "Andersson", "Singh")


class OrganizationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    users: int = Field(default=20, ge=1, le=100000)
    departments: int = Field(default=6, ge=1, le=100)
    projects: int = Field(default=4, ge=1, le=1000)
    locations: int = Field(default=3, ge=1, le=100)
    days: int = Field(default=7, ge=1, le=3660)
    seed: int = Field(default=42, ge=-(2**63), lt=2**63)
    reference_time: datetime

    @field_validator("reference_time")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Reference time requires a timezone")
        return value.astimezone(UTC)


class LogicalEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    kind: str
    properties: dict
    relationships: dict[str, str]


def entity_count(spec: OrganizationSpec, kind: str) -> int:
    counts = {
        "user": spec.users,
        "device": spec.users,
        "department": spec.departments,
        "project": spec.projects,
        "application": spec.projects,
        "resourceGroup": spec.projects,
        "workload": spec.projects,
        "location": spec.locations,
        "subscription": spec.locations,
        "group": spec.departments + spec.projects,
    }
    if kind not in counts:
        raise ValueError("Unknown organization entity kind")
    return counts[kind]


def entity_at(
    spec: OrganizationSpec, tenant_id: UUID, namespace: str, kind: str, index: int
) -> LogicalEntity:
    if (
        not isinstance(tenant_id, UUID)
        or not isinstance(namespace, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", namespace)
        or type(index) is not int
        or not 0 <= index < entity_count(spec, kind)
    ):
        raise ValueError("Invalid organization coordinates")
    key = f"{kind}:{index}"
    label = hashlib.sha256(f"{tenant_id}:{namespace}:{spec.seed}".encode()).hexdigest()[:10]
    identifier = str(stable_id(tenant_id, namespace, f"{VERSION}:{key}", spec.seed))
    properties = {
        "id": identifier,
        "displayName": f"{kind} {index + 1}",
        "createdDateTime": (spec.reference_time - timedelta(days=spec.days)).isoformat(),
    }
    relationships = {}
    if kind == "user":
        given = GIVEN[(index + spec.seed) % len(GIVEN)]
        surname = SURNAMES[(index // len(GIVEN) + spec.seed) % len(SURNAMES)]
        department = index % spec.departments
        properties.update(
            displayName=f"{given} {surname} {index + 1}",
            givenName=given,
            surname=surname,
            userPrincipalName=f"person-{label}-{index:06d}@lab.test",
            department=f"{DEPARTMENTS[department % len(DEPARTMENTS)]} {department + 1}",
            jobTitle="Department Lead" if index < spec.departments else "Specialist",
            officeLocation=LOCATIONS[index % spec.locations % len(LOCATIONS)],
        )
        relationships = {
            "department": f"department:{department}",
            "location": f"location:{index % spec.locations}",
            "project": f"project:{index % spec.projects}",
        }
        if index:
            relationships["manager"] = f"user:{(index - 1) // 8}"
    elif kind == "department":
        properties["displayName"] = f"{DEPARTMENTS[index % len(DEPARTMENTS)]} {index + 1}"
        relationships = {"owner": f"user:{index % spec.users}"}
    elif kind == "location":
        properties["displayName"] = f"{LOCATIONS[index % len(LOCATIONS)]} {index + 1}"
    elif kind == "device":
        properties.update(displayName=f"LAB-{label}-{index:06d}", operatingSystem="Windows")
        relationships = {"owner": f"user:{index}", "location": f"location:{index % spec.locations}"}
    elif kind == "subscription":
        relationships = {"location": f"location:{index}", "owner": f"user:{index % spec.users}"}
    elif kind == "group":
        target = (
            f"department:{index}"
            if index < spec.departments
            else f"project:{index - spec.departments}"
        )
        relationships = {"scope": target, "owner": f"user:{index % spec.users}"}
    else:
        relationships = {
            "owner": f"user:{index % spec.users}",
            "department": f"department:{index % spec.departments}",
            "subscription": f"subscription:{index % spec.locations}",
        }
        if kind != "project":
            relationships["project"] = f"project:{index}"
    return LogicalEntity(key=key, kind=kind, properties=properties, relationships=relationships)
