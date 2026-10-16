"""Transactional directory commands shared by provider and trusted administration."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from mockazure.db.models import Tenant
from mockazure.directory.history import record_change, record_object_version, reserve_revision
from mockazure.directory.models import (
    DirectoryEntry,
    DirectoryRelation,
    EvidenceOutbox,
    Group,
    RelationVersion,
)
from mockazure.directory.repository import public_object
from mockazure.directory.types import DirectoryChange, DirectoryKind, DirectoryObject
from mockazure.identity.credentials import hash_secret
from mockazure.identity.models import DirectoryRole, ServicePrincipal, User
from mockazure.identity.repository import IdentityError
from mockazure.identity.types import PrincipalContext, UserCreate
from mockazure.providers.graph.body import invalid
from mockazure.providers.graph.permissions import (
    GROUP_ADMIN,
    GraphOperation,
    authorize_directory,
    deny,
    readable_fields,
)

USER_PROFILE = frozenset(
    {
        "givenName",
        "surname",
        "jobTitle",
        "department",
        "officeLocation",
        "preferredLanguage",
    }
)
USER_UPDATE = USER_PROFILE | {"displayName", "accountEnabled", "mailNickname"}
GROUP_UPDATE = frozenset({"displayName", "description", "mailNickname"})
GROUP_CREATE = GROUP_UPDATE | {
    "mailEnabled",
    "securityEnabled",
    "groupTypes",
    "isAssignableToRole",
    "owners@odata.bind",
    "members@odata.bind",
}


@dataclass(frozen=True)
class AdminActor:
    provenance: str = "administration"


@dataclass(frozen=True)
class DirectoryCommand:
    kind: DirectoryKind
    action: Literal["create", "update", "delete", "add_member", "remove_member"]
    target_id: UUID | None
    values: dict


@dataclass(frozen=True)
class MutationResult:
    object: DirectoryObject | None
    revision: int
    outbox_id: UUID


def text_field(value, name, limit=200, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not 1 <= len(value) <= limit or "\x00" in value:
        invalid(f"Invalid {name}")


def validate_values(command):
    values = command.values
    if command.action in {"delete", "add_member", "remove_member"}:
        return
    allowed = (
        (
            USER_UPDATE | {"passwordProfile", "userPrincipalName"}
            if command.action == "create"
            else USER_UPDATE
        )
        if command.kind == "user"
        else (GROUP_CREATE if command.action == "create" else GROUP_UPDATE)
    )
    if not values or not values.keys() <= allowed:
        invalid("Unknown, read-only or unsupported directory properties")
    if "userPrincipalName" in values:
        text_field(values["userPrincipalName"], "userPrincipalName", 320)
    for key in values.keys() & {"displayName", "mailNickname"}:
        text_field(values[key], key, 64 if key == "mailNickname" else 200)
    for key in values.keys() & (USER_PROFILE - {"businessPhones"} | {"description"}):
        text_field(values[key], key, 1024 if key == "description" else 200, True)
    if "businessPhones" in values and (
        not isinstance(values["businessPhones"], list)
        or len(values["businessPhones"]) > 1
        or any(
            not isinstance(x, str) or not 1 <= len(x) <= 64 or "\x00" in x
            for x in values["businessPhones"]
        )
    ):
        invalid("businessPhones supports at most one bounded phone value")
    if "accountEnabled" in values and type(values["accountEnabled"]) is not bool:
        invalid("accountEnabled must be boolean")
    if command.kind == "group" and command.action == "create":
        if (
            not {"displayName", "mailNickname", "mailEnabled", "securityEnabled"} <= values.keys()
            or values["mailEnabled"] is not False
            or values["securityEnabled"] is not True
            or values.get("groupTypes", []) != []
            or values.get("isAssignableToRole", False) is not False
        ):
            invalid("Only static, non-role-assignable security groups are supported")
    if command.kind == "user" and command.action == "create":
        if (
            not {
                "displayName",
                "mailNickname",
                "userPrincipalName",
                "accountEnabled",
                "passwordProfile",
            }
            <= values.keys()
        ):
            invalid("User creation requires the declared identity and password properties")
        profile = values["passwordProfile"]
        if (
            not isinstance(profile, dict)
            or set(profile) != {"password", "forceChangePasswordNextSignIn"}
            or profile["forceChangePasswordNextSignIn"] is not False
        ):
            invalid("Password profile requires forceChangePasswordNextSignIn=false")


async def active_object(session, tenant, kind, identifier):
    model = User if kind == "user" else Group
    row = await session.scalar(
        select(model).where(
            model.tenant_id == tenant, model.id == identifier, model.deleted_at.is_(None)
        )
    )
    if row is None:
        raise IdentityError(404, "Request_ResourceNotFound", "Directory object not found")
    return row


def static_group(row):
    if (
        row.profile.get("groupTypes", []) != []
        or row.profile.get("isAssignableToRole", False)
        or row.profile.get("mailEnabled", False)
        or row.profile.get("securityEnabled", True) is not True
    ):
        invalid("Writes to this group type are unsupported")


async def reference_target(session, tenant, identifier, relation):
    for kind, model in (("user", User), ("group", Group), ("servicePrincipal", ServicePrincipal)):
        row = await session.scalar(
            select(model).where(model.tenant_id == tenant, model.id == identifier)
        )
        if row is None or (kind != "servicePrincipal" and row.deleted_at is not None):
            continue
        if (
            relation == "owner"
            and kind == "group"
            or relation == "member"
            and kind == "servicePrincipal"
        ):
            invalid("Unsupported relationship target type")
        if kind == "group":
            static_group(row)
        await session.execute(
            insert(DirectoryEntry)
            .values(
                tenant_id=tenant,
                id=row.id,
                kind=kind,
                **{dict(user="user_id", group="group_id", servicePrincipal="sp_id")[kind]: row.id},
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "id"])
        )
        return kind, row
    raise IdentityError(404, "Request_ResourceNotFound", "Relationship target not found")


async def add_relation(session, tenant, group, identifier, relation, revision):
    key = dict(tenant_id=tenant, group_id=group, object_id=identifier, relation=relation)
    if await session.get(DirectoryRelation, tuple(key.values())) is not None:
        invalid("An added object reference already exists")
    session.add(DirectoryRelation(**key))
    session.add(RelationVersion(**key, valid_from=revision))
    await session.flush()


async def remove_relation(session, row, revision):
    conditions = [
        RelationVersion.tenant_id == row.tenant_id,
        RelationVersion.group_id == row.group_id,
        RelationVersion.object_id == row.object_id,
        RelationVersion.relation == row.relation,
        RelationVersion.valid_to.is_(None),
    ]
    await session.execute(update(RelationVersion).where(*conditions).values(valid_to=revision))
    await session.delete(row)
    await session.flush()


async def mutate_directory(
    session,
    tenant_id: UUID,
    command: DirectoryCommand,
    actor: PrincipalContext | AdminActor,
    now: datetime,
) -> MutationResult:
    if command.kind not in {"user", "group"}:
        invalid("Unsupported directory kind")
    validate_values(command)
    # Serialize all directory writes for this tenant; the returned watermark is commit ordered.
    revision = await reserve_revision(session, tenant_id)
    provider = isinstance(actor, PrincipalContext)
    if provider:
        if actor.tenant_id != tenant_id:
            deny()
        method = {
            "create": "POST",
            "update": "PATCH",
            "delete": "DELETE",
            "add_member": "POST",
            "remove_member": "DELETE",
        }[command.action]
        resource = "members" if command.action.endswith("_member") else command.kind + "s"
        await authorize_directory(
            session,
            actor,
            GraphOperation("v1.0", resource, method, frozenset(command.values)),
            command.target_id,
        )
    elif not isinstance(actor, AdminActor):
        raise TypeError("Trusted administrative actor required")
    values, before, row, changed = command.values, None, None, set()
    edge_facts = []
    if command.action != "create":
        row = await active_object(session, tenant_id, command.kind, command.target_id)
        before = public_object(row)
        if command.kind == "group":
            if row.profile.get("_anonymous"):
                invalid("Anonymous Group.Create groups are not modifiable in this profile")
            static_group(row)
        elif provider and row.profile.get("userType", "Member") != "Member":
            invalid("Writes to non-Member user targets are outside this profile")
        elif provider and await session.scalar(
            select(DirectoryRole.role)
            .where(DirectoryRole.tenant_id == tenant_id, DirectoryRole.user_id == row.id)
            .limit(1)
        ):
            invalid("Writes to role-bearing user targets are outside this profile")
    if command.action == "create":
        if command.kind == "user":
            try:
                data = UserCreate(
                    upn=values["userPrincipalName"],
                    display_name=values["displayName"],
                    enabled=values["accountEnabled"],
                    password=values["passwordProfile"]["password"],
                )
            except ValidationError:
                invalid("Invalid local user properties or password")
            tenant = await session.get(Tenant, tenant_id)
            if tenant is None:
                raise IdentityError(404, "TenantNotFound", "Tenant not found")
            if data.upn.split("@")[1] != tenant.domain.lower():
                raise IdentityError(422, "InvalidUPN", "UPN domain is not owned by tenant")
            row = User(
                tenant_id=tenant_id,
                upn=data.upn,
                display_name=data.display_name,
                enabled=data.enabled,
                created_at=now,
                verifier=await asyncio.to_thread(hash_secret, data.password.get_secret_value()),
                profile={k: v for k, v in values.items() if k in USER_PROFILE | {"mailNickname"}},
            )
        else:
            row = Group(
                tenant_id=tenant_id,
                display_name=values["displayName"],
                mail_nickname=values["mailNickname"],
                created_at=now,
                profile={k: v for k, v in values.items() if k in {"description"}},
            )
        session.add(row)
        await session.flush()
        if command.kind == "group":
            bindings = {}
            for relation in ("owner", "member"):
                value = values.get(relation + "s@odata.bind", [])
                if not isinstance(value, list) or any(not isinstance(x, UUID) for x in value):
                    invalid("Bindings must be local directory references")
                if len(set(value)) != len(value):
                    invalid("Duplicate initial relationship")
                bindings[relation] = value
            if sum(map(len, bindings.values())) > 20:
                invalid("At most 20 combined initial owners and members are supported")
            if provider and actor.token_kind == "delegated":
                roles = set(
                    await session.scalars(
                        select(DirectoryRole.role).where(
                            DirectoryRole.tenant_id == tenant_id,
                            DirectoryRole.user_id == actor.subject_id,
                        )
                    )
                )
                if "owners@odata.bind" not in values and not roles & GROUP_ADMIN:
                    bindings["owner"] = [actor.subject_id]
                elif actor.subject_id in bindings["owner"]:
                    invalid(
                        "Explicit self ownership during delegated group creation is unsupported"
                    )
            if (
                provider
                and actor.token_kind == "application"
                and not bindings["owner"]
                and not actor.permissions & {"Group.ReadWrite.All", "Directory.ReadWrite.All"}
            ):
                row.profile = {**row.profile, "_anonymous": True}
            for relation, identifiers in bindings.items():
                for identifier in identifiers:
                    kind, target = await reference_target(session, tenant_id, identifier, relation)
                    if provider:
                        self_sp = kind == "servicePrincipal" and target.client_id == actor.client_id
                        self_user = (
                            kind == "user"
                            and actor.token_kind == "delegated"
                            and identifier == actor.subject_id
                        )
                        if (
                            not self_sp
                            and not self_user
                            and len(readable_fields(actor, kind, identifier)) <= 1
                        ):
                            deny()
                    await add_relation(session, tenant_id, row.id, identifier, relation, revision)
                    edge_facts.append(
                        {
                            "group_id": str(row.id),
                            "object_id": str(identifier),
                            "relation": relation,
                            "removed": False,
                        }
                    )
                    changed.add(relation + "s")
        changed.update(public_object(row).properties)
    elif command.action == "update":
        if "displayName" in values:
            row.display_name = values["displayName"]
        if command.kind == "user":
            if "accountEnabled" in values:
                row.enabled = values["accountEnabled"]
            row.profile = {
                **row.profile,
                **{k: v for k, v in values.items() if k in USER_PROFILE | {"mailNickname"}},
            }
        else:
            if "mailNickname" in values:
                row.mail_nickname = values["mailNickname"]
            row.profile = {**row.profile, **{k: v for k, v in values.items() if k == "description"}}
        changed.update(
            k for k in values if before.properties.get(k) != public_object(row).properties.get(k)
        )
    elif command.action in {"add_member", "remove_member"}:
        identifier = values.get("member_id")
        if (
            command.kind != "group"
            or not isinstance(identifier, UUID)
            or set(values) != {"member_id"}
        ):
            invalid("Invalid member command")
        kind, _ = await reference_target(session, tenant_id, identifier, "member")
        if command.action == "add_member":
            if kind == "group":
                from mockazure.providers.graph.relationships import relation_ids

                tree = relation_ids(tenant_id, identifier, "transitiveMembers").subquery()
                cyclic = identifier == row.id or await session.scalar(
                    select(tree.c.id).where(tree.c.id == row.id).limit(1)
                )
                if cyclic:
                    invalid("Cyclic group membership is unsupported")
            await add_relation(session, tenant_id, row.id, identifier, "member", revision)
        else:
            edge = await session.get(DirectoryRelation, (tenant_id, row.id, identifier, "member"))
            if edge is None:
                raise IdentityError(404, "Request_ResourceNotFound", "Membership not found")
            await remove_relation(session, edge, revision)
        edge_facts.append(
            {
                "group_id": str(row.id),
                "object_id": str(identifier),
                "relation": "member",
                "removed": command.action == "remove_member",
            }
        )
        changed.add("members")
    elif command.action == "delete":
        row.deleted_at = now
        if command.kind == "user":
            row.enabled = False
        edges = list(
            await session.scalars(
                select(DirectoryRelation).where(
                    DirectoryRelation.tenant_id == tenant_id,
                    or_(
                        DirectoryRelation.object_id == row.id, DirectoryRelation.group_id == row.id
                    ),
                )
            )
        )
        parents = {}
        for edge in edges:
            edge_facts.append(
                {
                    "group_id": str(edge.group_id),
                    "object_id": str(edge.object_id),
                    "relation": edge.relation,
                    "removed": True,
                }
            )
            if edge.group_id != row.id:
                parents.setdefault(edge.group_id, set()).add(edge.relation + "s")
            await remove_relation(session, edge, revision)
        for parent_id, fields in parents.items():
            parent = public_object(await active_object(session, tenant_id, "group", parent_id))
            await record_object_version(
                session,
                DirectoryChange(revision, "group", parent_id, frozenset(fields)),
                parent,
                parent,
            )
        changed.update(before.properties)
    else:
        invalid("Unsupported directory command")
    await session.flush()
    after = None if command.action == "delete" else public_object(row)
    facts = {"operation": command.action, "provenance": "graph" if provider else actor.provenance}
    if edge_facts:
        facts["relationships"] = edge_facts
    if provider:
        facts.update(
            actor_id=str(actor.subject_id or actor.client_id), client_id=str(actor.client_id)
        )
    if command.action.endswith("_member"):
        facts["target_ids"] = [str(values["member_id"])]
    await record_change(
        session,
        DirectoryChange(revision, command.kind, row.id, frozenset(changed), after is None),
        before,
        after,
        facts,
    )
    outbox_id = await session.scalar(
        select(EvidenceOutbox.id).where(
            EvidenceOutbox.tenant_id == tenant_id, EvidenceOutbox.revision == revision
        )
    )
    return MutationResult(after, revision, outbox_id)
