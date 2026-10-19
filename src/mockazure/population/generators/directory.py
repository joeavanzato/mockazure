"""Reusable directory construction shared with the existing scale fixtures."""

from mockazure.population.generators.common import Generation
from mockazure.population.types import Mutation


def user_payload(identifier, display_name, upn, nickname, stamp):
    return {
        "id": identifier,
        "displayName": display_name,
        "userPrincipalName": upn,
        "mailNickname": nickname,
        "accountEnabled": True,
        "createdDateTime": stamp,
    }


def generate(name, parameters, refs, *, offset, limit):
    g = Generation(parameters, refs, offset, limit)
    result = []
    for i in g.indexes:
        if name == "directory.users":
            value = user_payload(
                g.id("user", i), f"Lab user {i}", g.upn(i), f"lab-{g.label}-{i}", g.stamp(0)
            )
        elif name == "directory.groups":
            value = {
                "id": g.id("group", i),
                "displayName": f"Lab group {i}",
                "mailNickname": f"group-{g.label}-{i}",
                "securityEnabled": True,
                "mailEnabled": False,
                "groupTypes": [],
                "createdDateTime": g.stamp(0),
            }
        else:
            raise ValueError("Unknown directory generator")
        result.append(Mutation(operation="import", logical_key=f"{name}:{i}", payload=value))
    return result
