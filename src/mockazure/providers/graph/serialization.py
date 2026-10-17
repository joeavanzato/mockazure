from mockazure.directory.types import GROUP_FIELDS, PUBLIC_FIELDS, DirectoryObject
from mockazure.providers.graph.errors import GraphError

USER_DEFAULT = (
    "id",
    "displayName",
    "userPrincipalName",
    "givenName",
    "surname",
    "mail",
    "businessPhones",
    "jobTitle",
    "mobilePhone",
    "officeLocation",
    "preferredLanguage",
)
DEFAULT_FIELDS = {"user": USER_DEFAULT, "group": tuple(sorted(GROUP_FIELDS))}


def serialize_object(
    obj: DirectoryObject,
    fields: tuple[str, ...] | None,
    readable_fields: frozenset[str],
) -> dict:
    chosen = (
        fields
        if fields is not None
        else tuple(name for name in DEFAULT_FIELDS[obj.kind] if name in readable_fields)
    )
    allowed = PUBLIC_FIELDS[obj.kind] | ({"signInActivity"} if obj.kind == "user" else set())
    if not set(chosen) <= allowed:
        raise GraphError(400, "Request_UnsupportedQuery", "Selected field is not supported")
    return {key: obj.properties.get(key) if key in readable_fields else None for key in chosen}


def selected_fields(request) -> tuple[str, ...] | None:
    items = list(request.query_params.multi_items())
    if not items:
        return None
    if len(items) != 1 or items[0][0] != "$select":
        raise GraphError(400, "Request_UnsupportedQuery", "Query option is not supported here")
    values = items[0][1].split(",")
    if not all(values) or len(values) != len(set(values)):
        raise GraphError(400, "Request_UnsupportedQuery", "Invalid selected fields")
    canonical = {name.lower(): name for name in PUBLIC_FIELDS["user"] | {"signInActivity"}}
    values = tuple(canonical.get(value.lower(), value) for value in values)
    if len(set(values)) != len(values):
        raise GraphError(400, "Request_UnsupportedQuery", "Duplicate selected fields")
    return values
