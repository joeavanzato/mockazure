from sqlalchemy import select

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.identity.models import DirectoryRole, ServicePrincipal
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.security.models import SecurityOwner

APP_ADMINS = {
    "Global Administrator",
    "Application Administrator",
    "Cloud Application Administrator",
}


def forbidden():
    raise GraphError(
        403, "Authorization_RequestDenied", "Insufficient privileges for this operation"
    )


async def actor_object_id(session, actor):
    if actor.token_kind == "delegated":
        return actor.subject_id
    return await session.scalar(
        select(ServicePrincipal.id).where(
            ServicePrincipal.tenant_id == actor.tenant_id,
            ServicePrincipal.client_id == actor.client_id,
            ServicePrincipal.deleted_at.is_(None),
        )
    )


async def authorize_mutation(session, actor, kind, operation, identifier=None):
    if kind == "device":
        apps = (
            {"Device.ReadWrite.All", "Directory.ReadWrite.All"}
            if operation not in {"create", "add_owner", "remove_owner"}
            else set()
        )
        await permitted(
            actor,
            OperationRequirement(
                "graph",
                frozenset(apps),
                frozenset({"Directory.AccessAsUser.All"}),
                frozenset(
                    {
                        "Global Administrator",
                        "Intune Administrator",
                        "Windows 365 Administrator",
                        "Cloud Device Administrator",
                    }
                ),
            ),
            session,
        )
        return
    full = {"Application.ReadWrite.All", "Directory.ReadWrite.All"}
    await permitted(
        actor,
        OperationRequirement(
            "graph",
            frozenset(full | {"Application.ReadWrite.OwnedBy"}),
            frozenset(full),
        ),
        session,
    )
    if operation in {"add_owner", "remove_owner"}:
        if (
            "Directory.ReadWrite.All" not in actor.permissions
            and not {"Application.ReadWrite.All", "Directory.Read.All"} <= actor.permissions
        ):
            forbidden()
    if actor.token_kind == "application" and actor.permissions & full:
        return
    roles = (
        set(
            await session.scalars(
                select(DirectoryRole.role).where(
                    DirectoryRole.tenant_id == actor.tenant_id,
                    DirectoryRole.user_id == actor.subject_id,
                )
            )
        )
        if actor.subject_id
        else set()
    )
    if actor.token_kind == "delegated" and roles & APP_ADMINS:
        return
    if operation in {"add_owner", "remove_owner"}:
        forbidden()
    if operation == "create" and kind == "application":
        return
    owner_id = await actor_object_id(session, actor)
    if identifier and await session.get(SecurityOwner, (actor.tenant_id, identifier, owner_id)):
        return
    forbidden()
