"""Disposable lifecycle layers; public responses supply downstream object IDs."""

from mockazure.population.generators.common import Generation
from mockazure.population.types import Mutation
from mockazure.security.roles import role_ids

VERSION = "business-identity-v1"
FAMILIES = {
    key: "identity.lifecycle"
    for key in ("joiner", "mover", "leaver", "delete", "restore", "eligibility")
}
FAMILIES["client"] = "identity.clients"


def generate(name, parameters, refs, *, offset, limit):
    kind = name.removeprefix("lifecycle.")
    g = Generation(parameters, refs, offset, limit)
    subject = parameters.get("subject", "lifecycle")
    if subject not in {"lifecycle", "privileged"}:
        raise ValueError("Unknown disposable lifecycle subject")
    label = subject + "-" + g.label
    if kind not in FAMILIES or g.count != 1:
        raise ValueError("Lifecycle layers use one explicitly disposable subject per namespace")
    output = []
    for i in g.indexes:
        variables, body = {}, {}
        if kind == "client":
            body = {
                "id": label,
                "display_name": "Disposable lifecycle operator " + g.label,
                "permissions": [
                    {
                        "resource": "graph",
                        "kind": "application",
                        "permissions": [
                            "User.ReadWrite.All",
                            "User.EnableDisableAccount.All",
                            "User.Read.All",
                            "RoleManagement.ReadWrite.Directory",
                            "AuditLog.Read.All",
                            "User.Invite.All",
                            "User.RevokeSessions.All",
                            "Application.ReadWrite.All",
                        ],
                    }
                ],
            }
        elif kind == "joiner":
            body = {
                "displayName": "Disposable lifecycle subject " + g.label,
                "userPrincipalName": f"disposable-{label}@{refs['domain']}",
                "mailNickname": "disposable-" + label,
                "accountEnabled": True,
                "department": "Operations",
            }
        elif kind == "eligibility":
            body = {
                "principalId": refs[subject + "-user"],
                "roleDefinitionId": role_ids()["Security Reader"],
                "directoryScopeId": "/",
                "action": "adminAssign",
                "justification": "Disposable local lifecycle exercise",
                "scheduleInfo": {"expiration": {"type": "afterDuration", "duration": "PT2H"}},
            }
        else:
            variables = {"user": refs[subject + "-user"]}
            body = (
                {"department": "Investigation"}
                if kind == "mover"
                else ({"accountEnabled": False} if kind == "leaver" else {})
            )
        output.append(
            Mutation(
                operation="provision" if kind == "client" else kind,
                logical_key=f"{subject}.{name}:{i}",
                payload=body if kind == "client" else {"variables": variables, "body": body},
                identity_ref=None if kind == "client" else refs[subject + "-client"],
            )
        )
    return output
