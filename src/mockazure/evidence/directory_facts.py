"""Immutable event-time snapshots; adapters never enrich old facts from current rows."""

import json
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid5

from pydantic import Field
from sqlalchemy import exists, select

from mockazure.directory.models import EvidenceOutbox, Group
from mockazure.directory.types import PUBLIC_FIELDS
from mockazure.evidence.schemas import EvidenceSettings, SourceInput, SourceRef, StrictModel
from mockazure.identity.models import Application, ServicePrincipal, User
from mockazure.identity.types import PrincipalContext
from mockazure.providers.graph.schema_reference import validate_payload


class DirectoryFacts(StrictModel):
    schema_version: Literal[2]
    operation: Literal[
        "create",
        "update",
        "delete",
        "add_member",
        "remove_member",
        "add_owner",
        "remove_owner",
        "import",
        "license",
        "unknown",
    ]
    object_kind: Literal["user", "group", "directory"]
    provenance: str = Field(max_length=200)
    correlation_id: UUID
    initiated_by: dict
    targets: list[dict] = Field(max_length=10000)
    changed_fields: list[str] = Field(max_length=100)
    legacy_incomplete: bool = False
    actor_id: UUID | None = None
    client_id: UUID | None = None
    target_ids: list[UUID] = Field(default_factory=list, max_length=10000)
    relationships: list[dict] = Field(default_factory=list, max_length=10000)
    modified_properties: dict = Field(default_factory=dict)
    deletion_semantics: Literal["recoverable-v1"] | None = None


def validate_directory_facts(facts):
    value = DirectoryFacts.model_validate(facts)
    validate_payload("v1.0", "auditActivityInitiator", value.initiated_by)
    allowed = PUBLIC_FIELDS.get(value.object_kind, frozenset()) | {"members", "owners"}
    if not set(value.changed_fields) <= allowed or not value.modified_properties.keys() <= allowed:
        raise ValueError("Unsupported directory difference")
    for target in value.targets:
        validate_payload("v1.0", "targetResource", target)
        UUID(target["id"])
        for item in target.get("modifiedProperties", []):
            if item["displayName"] not in allowed:
                raise ValueError("Unsupported directory property")
            json.loads(item["oldValue"])
            json.loads(item["newValue"])
    for edge in value.relationships:
        if set(edge) != {"group_id", "object_id", "relation", "removed"}:
            raise ValueError("Unsupported directory relationship")
        UUID(edge["group_id"])
        UUID(edge["object_id"])
        if edge["relation"] not in {"member", "owner"} or type(edge["removed"]) is not bool:
            raise ValueError("Unsupported directory relationship")
    return value


def difference(name, old, new):
    return {
        "displayName": name,
        "oldValue": json.dumps(old, separators=(",", ":")),
        "newValue": json.dumps(new, separators=(",", ":")),
    }


def target_snapshot(obj):
    return {
        "id": str(obj.id),
        "type": obj.kind.title(),
        "displayName": obj.properties.get("displayName"),
        "userPrincipalName": obj.properties.get("userPrincipalName"),
        "modifiedProperties": [],
    }


async def capture_directory_facts(
    session, actor, command, before, after, correlation_id, relationships
):
    from mockazure.directory.repository import public_object

    obj = after or before
    provider = isinstance(actor, PrincipalContext)
    initiated = {}
    if provider and actor.token_kind == "delegated" and actor.subject_id:
        user = await session.scalar(
            select(User).where(User.tenant_id == obj.tenant_id, User.id == actor.subject_id)
        )
        initiated = {
            "user": {
                "id": str(actor.subject_id),
                "displayName": user.display_name if user else None,
                "userPrincipalName": user.upn if user else None,
            }
        }
    elif provider and actor.token_kind == "application":
        app = await session.scalar(
            select(Application).where(
                Application.tenant_id == obj.tenant_id, Application.client_id == actor.client_id
            )
        )
        sp = await session.scalar(
            select(ServicePrincipal).where(
                ServicePrincipal.tenant_id == obj.tenant_id,
                ServicePrincipal.client_id == actor.client_id,
            )
        )
        initiated = {
            "app": {
                "appId": str(actor.client_id),
                "displayName": app.display_name if app else None,
                "servicePrincipalId": str(sp.id) if sp else None,
                "servicePrincipalName": app.display_name if app else None,
            }
        }
    differences = {}
    for name in sorted(PUBLIC_FIELDS[obj.kind]):
        old = before.properties.get(name) if before else None
        new = after.properties.get(name) if after else None
        if old != new:
            differences[name] = {"before": old, "after": new}
    for relation in ("member", "owner"):
        edges = [
            e for e in relationships if e["relation"] == relation and e["group_id"] == str(obj.id)
        ]
        if edges:
            differences[relation + "s"] = {
                "before": sorted(e["object_id"] for e in edges if e["removed"]),
                "after": sorted(e["object_id"] for e in edges if not e["removed"]),
            }
    primary = target_snapshot(obj)
    primary["modifiedProperties"] = [
        difference(k, v["before"], v["after"]) for k, v in sorted(differences.items())
    ]
    targets = [primary]
    if command.action in {"add_member", "remove_member", "add_owner", "remove_owner"}:
        member_id = command.values["owner_id" if command.action.endswith("_owner") else "member_id"]
        for model in (User, Group):
            member = await session.scalar(
                select(model).where(model.tenant_id == obj.tenant_id, model.id == member_id)
            )
            if member is not None:
                targets.append(target_snapshot(public_object(member)))
                break
        else:
            principal = await session.scalar(
                select(ServicePrincipal).where(
                    ServicePrincipal.tenant_id == obj.tenant_id, ServicePrincipal.id == member_id
                )
            )
            if principal is not None:
                application = await session.get(Application, principal.client_id)
                targets.append(
                    {
                        "id": str(principal.id),
                        "type": "ServicePrincipal",
                        "displayName": principal.profile.get("displayName")
                        or (application.display_name if application else None),
                        "modifiedProperties": [],
                    }
                )
    facts = DirectoryFacts(
        schema_version=2,
        operation=command.action,
        deletion_semantics="recoverable-v1"
        if obj.kind == "user" and command.action == "delete"
        else None,
        object_kind=obj.kind,
        provenance="graph" if provider else actor.provenance,
        correlation_id=correlation_id,
        initiated_by=initiated,
        targets=targets,
        changed_fields=sorted(differences),
        relationships=relationships,
        modified_properties=differences,
        actor_id=(actor.subject_id or actor.client_id) if provider else None,
        client_id=actor.client_id if provider else None,
        target_ids=[command.values["member_id"]]
        if command.action.endswith("_member")
        else [command.values["owner_id"]]
        if command.action.endswith("_owner")
        else [],
    ).model_dump(mode="json")
    validate_directory_facts(facts)
    return facts


def legacy_facts(row):
    facts = row.facts
    operation = facts.get("operation")
    if operation not in {
        "create",
        "update",
        "delete",
        "add_member",
        "remove_member",
        "import",
        "license",
    }:
        operation = "unknown"
    kind = row.kind
    allowed = PUBLIC_FIELDS.get(kind, frozenset())
    differences = {k: v for k, v in facts.get("modified_properties", {}).items() if k in allowed}
    target = {
        "id": str(row.object_id),
        "type": kind.title(),
        "modifiedProperties": [
            difference(k, v.get("before"), v.get("after")) for k, v in sorted(differences.items())
        ],
    }
    targets = [] if operation == "import" else [target]
    # IDs and known public differences survive; actor kind and mutable labels cannot be inferred.
    for identifier in facts.get("target_ids", []) if operation != "import" else []:
        targets.append({"id": str(UUID(identifier)), "modifiedProperties": []})
    return DirectoryFacts(
        schema_version=2,
        operation=operation,
        object_kind=kind,
        provenance=facts.get("provenance")
        if isinstance(facts.get("provenance"), str)
        else ("directory_import" if operation == "import" else "legacy_unknown"),
        correlation_id=UUID(facts["correlation_id"])
        if facts.get("correlation_id")
        else uuid5(row.id, "correlation"),
        initiated_by={},
        targets=targets,
        changed_fields=sorted(differences),
        modified_properties=differences,
        actor_id=facts.get("actor_id"),
        client_id=facts.get("client_id"),
        legacy_incomplete=True,
    ).model_dump(mode="json")


async def capture_outbox(session, row, *, legacy_scan=False):
    from mockazure.evidence.models import EvidenceSource
    from mockazure.evidence.repository import append_source, lock_state
    from mockazure.evidence.sources import enqueue_projection

    ref = SourceRef(tenant_id=row.tenant_id, kind="directory", id=row.id)
    if await session.get(EvidenceSource, (ref.tenant_id, ref.kind, ref.id)):
        return ref
    facts = row.facts if row.facts.get("schema_version") == 2 else legacy_facts(row)
    state = await lock_state(session, row.tenant_id)
    if legacy_scan and row.revision <= state.legacy_directory_revision:
        return None
    settings = EvidenceSettings.model_validate(state.settings)
    await append_source(
        session,
        SourceInput(
            ref=ref,
            occurred_at=row.occurred_at,
            available_at=row.occurred_at + timedelta(seconds=settings.delay_seconds),
            facts=facts,
        ),
    )
    await enqueue_projection(session, ref)
    return ref


async def scan_directory_outbox(session, limit, tenant_id=None):
    from mockazure.evidence.models import EvidenceSource, EvidenceState

    captured = exists(
        select(EvidenceSource.id).where(
            EvidenceSource.tenant_id == EvidenceOutbox.tenant_id,
            EvidenceSource.kind == "directory",
            EvidenceSource.id == EvidenceOutbox.id,
        )
    )
    rows = list(
        await session.scalars(
            select(EvidenceOutbox)
            .where(
                EvidenceOutbox.projected_at.is_(None),
                ~captured,
                True if tenant_id is None else EvidenceOutbox.tenant_id == tenant_id,
                ~exists(
                    select(1).where(
                        EvidenceState.tenant_id == EvidenceOutbox.tenant_id,
                        EvidenceState.legacy_directory_revision >= EvidenceOutbox.revision,
                    )
                ),
            )
            .order_by(EvidenceOutbox.tenant_id, EvidenceOutbox.revision)
            .limit(limit)
        )
    )
    for row in rows:
        await capture_outbox(session, row, legacy_scan=True)
    return len(rows)
