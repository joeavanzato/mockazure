"""Ordinary and optional suspicious records with deterministic subjects and timestamps."""

from mockazure.population.generators.common import Generation
from mockazure.population.types import Mutation


def signin_payload(
    identifier, stamp, user_id, upn, display_name, app_id, app_name="Lab client", *, error=0
):
    return {
        "id": identifier,
        "createdDateTime": stamp,
        "userId": user_id,
        "userPrincipalName": upn,
        "userDisplayName": display_name,
        "appId": app_id,
        "appDisplayName": app_name,
        "ipAddress": "192.0.2.42",
        "isInteractive": True,
        "status": {"errorCode": error},
    }


def generate(name, parameters, refs, *, offset, limit):
    g = Generation(parameters, refs, offset, limit)
    rows = []
    for i in g.indexes:
        identifier, stamp, subject = g.id(name, i), g.stamp(i), i % g.users
        if name in {"evidence.signins", "evidence.suspicious-signins"}:
            value = signin_payload(
                identifier,
                stamp,
                g.id("user", subject),
                g.upn(subject),
                f"Lab user {subject}",
                g.id("application"),
                error=50126 if name.endswith("suspicious-signins") else 0,
            )
            value = {
                "id": identifier,
                "payload": value,
                "occurred_at": stamp,
                "available_at": stamp,
            }
        elif name == "evidence.directory-audits":
            value = {
                "id": identifier,
                "occurred_at": stamp,
                "available_at": stamp,
                "payload": {
                    "id": identifier,
                    "activityDateTime": stamp,
                    "activityDisplayName": "Update user",
                    "category": "UserManagement",
                    "loggedByService": "Core Directory",
                    "result": "success",
                    "initiatedBy": {
                        "user": {"id": g.id("user", subject), "userPrincipalName": g.upn(subject)}
                    },
                    "targetResources": [
                        {
                            "id": g.id("user", subject),
                            "type": "User",
                            "displayName": f"Lab user {subject}",
                        }
                    ],
                },
            }
        elif name == "activity.records":
            value = {
                "Id": identifier,
                "OrganizationId": str(g.tenant),
                "CreationTime": stamp,
                "Operation": "FileAccessed",
                "UserKey": g.id("user", subject),
                "UserId": g.upn(subject),
                "Workload": "SharePoint",
                "RecordType": 6,
                "UserType": 0,
                "ObjectId": "https://lab.test/documents/evidence.txt",
                "ClientIP": "192.0.2.42",
            }
        elif name == "security.alerts":
            value = {
                "id": identifier,
                "title": f"Synthetic test alert {i}",
                "severity": "medium",
                "tenantId": str(g.tenant),
            }
        else:
            raise ValueError("Unknown evidence generator")
        rows.append(Mutation(operation="import", logical_key=f"{name}:{i}", payload=value))
    return rows
