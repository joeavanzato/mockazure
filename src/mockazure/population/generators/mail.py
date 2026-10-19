"""Historical mail content, separate from service-side send actions."""

from mockazure.population.generators.common import Generation
from mockazure.population.types import Mutation


def generate(name, parameters, refs, *, offset, limit):
    if name != "mail.messages":
        raise ValueError("Unknown mail generator")
    g = Generation(parameters, refs, offset, limit)
    return [
        Mutation(
            operation="append",
            logical_key=f"{name}:{i}",
            parent=refs["primary-user"],
            payload={
                "id": g.id(name, i),
                "folder_id": parameters.get("folder", "inbox"),
                "payload": {
                    "subject": f"Lab evidence message {i}",
                    "receivedDateTime": g.stamp(i),
                    "createdDateTime": g.stamp(i),
                    "lastModifiedDateTime": g.stamp(i),
                    "isRead": i % 2 == 0,
                    "body": {
                        "contentType": "text",
                        "content": f"Synthetic {g.namespace} message {i}.",
                    },
                    "from": {
                        "emailAddress": {
                            "address": g.upn(i % g.users),
                            "name": f"Lab user {i % g.users}",
                        }
                    },
                },
            },
        )
        for i in g.indexes
    ]
