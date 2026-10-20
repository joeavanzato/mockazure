"""Named-identity lifecycle actions and read-only public lifecycle metadata."""

import secrets
from datetime import timedelta

from sqlalchemy import String, case, cast, func

from mockazure.identity.deleted_models import DeletedObject
from mockazure.identity.models import User
from mockazure.population.actions import Action, PublicActions
from mockazure.population.adapters.browsing import browser
from mockazure.population.tokens import fingerprint
from mockazure.population.types import BatchResult, Inspection, ResourceItem, VerificationResult
from mockazure.security.pim.models import PimSchedule

ROOT = "/v1.0/roleManagement/directory"


class LifecycleActions(PublicActions):
    def describe(self):
        result = super().describe()
        result.limitations = [
            "Explicitly disposable subjects. These named-identity actions call real local APIs.",
            "Joiner passwords are random, memory-only, and never returned or stored in profiles.",
            "Security and retention use wall time; the reference clock does not grant authority.",
            "Service calls have manual replay semantics; inspect uncertain outcomes before retry.",
        ]
        for operation in result.operations:
            operation.replay_policy = "manual"
            operation.destructive = operation.key in {"leaver", "delete", "restore"}
        return result

    async def inspect(self, ctx, mutation):
        self.validate(mutation)
        return Inspection(
            actual_id=None,
            version=None,
            exists=False,
            conflicts=["Disposable lifecycle service action; manual reconciliation."],
        )

    async def apply(self, ctx, mutations):
        if len(mutations) != 1:
            raise ValueError("Lifecycle actions execute one checkpointed operation")
        mutation = mutations[0]
        action, data, path = self.prepare(mutation)
        if mutation.operation == "joiner":
            data.body["passwordProfile"] = {
                "password": secrets.token_urlsafe(40),
                "forceChangePasswordNextSignIn": False,
            }
        identity = mutation.identity_ref or ctx.identity_ref
        transport = await self.transport(ctx, identity)
        response = await transport.request("graph", action.method, path, json=data.body or None)
        self.check(response)
        body = response.json() if response.content else {}
        identifier = body.get("id") or data.variables.get("user")
        if not identifier:
            raise ValueError("Lifecycle response omitted its identity")
        read_path = (
            ROOT + "/roleEligibilityScheduleRequests/" + identifier
            if mutation.operation == "eligibility"
            else "/v1.0/users/" + identifier
        )
        return BatchResult(
            items=[
                ResourceItem(
                    id=identifier,
                    version=fingerprint([mutation.logical_key, identifier]),
                    properties={
                        "service": "graph",
                        "identity_ref": identity,
                        "read_path": read_path,
                        "expected_status": 404 if mutation.operation == "delete" else 200,
                        "expected_fields": data.body
                        if mutation.operation in {"mover", "leaver"}
                        else {},
                        "operation": mutation.operation,
                        "disposable": True,
                    },
                )
            ],
            effects={
                "created": int(mutation.operation in {"joiner", "eligibility"}),
                "updated": int(mutation.operation not in {"joiner", "eligibility"}),
            },
        )

    async def verify(self, ctx, result):
        count = 0
        for item in result.items:
            p = item.properties
            transport = await self.transport(ctx, p["identity_ref"])
            response = await transport.request(
                "graph",
                "GET",
                p["read_path"],
                params={"$select": ",".join(["id", *p["expected_fields"]])}
                if p["expected_fields"]
                else {},
            )
            if response.status_code != p["expected_status"]:
                continue
            if p["expected_fields"] and any(
                response.json().get(k) != v for k, v in p["expected_fields"].items()
            ):
                continue
            count += 1
        return VerificationResult(
            status="succeeded" if count == len(result.items) else "failed",
            expected=len(result.items),
            actual=count,
        )


def adapters():
    limits = [
        "Read-only lifecycle state. Security/retention use wall time, not lab reference time.",
        "Eligibility is never an effective permission; disabled principals have no authority.",
    ]
    user = browser(
        "identity.lifecycle",
        "Entra",
        "/v1.0/users",
        User,
        User.id,
        User.sessions_valid_from,
        {"id": User.id, "displayName": User.display_name, "accountEnabled": User.enabled},
        conditions=(User.deleted_at.is_(None),),
    )
    actions = LifecycleActions(
        user,
        [
            Action("joiner", "graph", "POST", "/v1.0/users", "v1.0", "user"),
            Action("mover", "graph", "PATCH", "/v1.0/users/{user}", "v1.0", "user"),
            Action("leaver", "graph", "PATCH", "/v1.0/users/{user}", "v1.0", "user"),
            Action("delete", "graph", "DELETE", "/v1.0/users/{user}", "v1.0"),
            Action(
                "restore", "graph", "POST", "/v1.0/directory/deletedItems/{user}/restore", "v1.0"
            ),
            Action(
                "eligibility",
                "graph",
                "POST",
                ROOT + "/roleEligibilityScheduleRequests",
                "v1.0",
                "unifiedRoleEligibilityScheduleRequest",
            ),
        ],
    )
    r = DeletedObject
    deleted = browser(
        "identity.deletedObjects",
        "Entra",
        "/v1.0/directory/deletedItems/microsoft.graph.user",
        r,
        r.object_id,
        r.deleted_at,
        {"id": r.object_id, "kind": r.kind, "deletedDateTime": r.deleted_at},
        conditions=(r.purged_at.is_(None), r.deleted_at > func.now() - timedelta(days=30)),
        limitations=limits,
    )
    result = [actions, deleted]
    r = PimSchedule
    for kind, family in (("eligibility", "pimEligibility"), ("assignment", "pimAssignments")):
        status = case(
            (r.revoked_at.is_not(None), "Revoked"),
            (r.ends_at <= func.now(), "Expired"),
            (r.starts_at > func.now(), "Granted"),
            else_="Provisioned",
        )
        result.append(
            browser(
                "identity." + family,
                "Entra",
                ROOT
                + (
                    "/roleEligibilitySchedules"
                    if kind == "eligibility"
                    else "/roleAssignmentSchedules"
                ),
                r,
                r.id,
                func.concat(cast(r.id, String), ":", status),
                {
                    "id": r.id,
                    "principalId": r.principal_id,
                    "role": r.role,
                    "startDateTime": r.starts_at,
                    "endDateTime": r.ends_at,
                    "status": status,
                    "securityTime": func.now(),
                },
                conditions=(r.kind == kind,),
                limitations=limits,
            )
        )
    return result
