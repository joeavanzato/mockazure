from dataclasses import dataclass

from mockazure.directory.models import ObjectVersion
from mockazure.directory.types import GROUP_FIELDS, USER_FIELDS
from mockazure.providers.graph.errors import GraphError


def unsupported(message="Query is outside the supported directory profile"):
    raise GraphError(400, "Request_UnsupportedQuery", message)


@dataclass(frozen=True)
class FieldContract:
    name: str
    data_type: str
    operators: frozenset[str]


@dataclass(frozen=True)
class QueryContract:
    version: str
    resource: str
    kind: str
    fields: dict[str, FieldContract]
    orderable: frozenset[str]


def contract_for(version: str, resource: str) -> QueryContract:
    if version not in {"v1.0", "beta"} or resource not in {"users", "groups"}:
        unsupported("Unsupported query resource/version")
    kind = "user" if resource == "users" else "group"
    fields = {
        name: FieldContract(name, "string", frozenset())
        for name in (USER_FIELDS if kind == "user" else GROUP_FIELDS)
    }
    string_names = (
        {
            "displayName",
            "userPrincipalName",
            "mailNickname",
            "mail",
            "givenName",
            "surname",
            "department",
        }
        if kind == "user"
        else {"displayName", "mailNickname", "mail", "description"}
    )
    for name in string_names:
        fields[name] = FieldContract(name, "string", frozenset({"eq", "ne", "in"}))
    for name in {"displayName", "userPrincipalName"} if kind == "user" else {"displayName"}:
        fields[name] = FieldContract(name, "string", fields[name].operators | {"startswith"})
    for name in {"accountEnabled"} if kind == "user" else {"mailEnabled", "securityEnabled"}:
        fields[name] = FieldContract(name, "bool", frozenset({"eq", "ne"}))
    fields["id"] = FieldContract("id", "uuid", frozenset({"eq", "ne", "in"}))
    fields["createdDateTime"] = FieldContract(
        "createdDateTime", "datetime", frozenset({"eq", "ge", "gt", "le", "lt"})
    )
    if kind == "group":
        fields["groupTypes"] = FieldContract("groupTypes", "array", frozenset({"any"}))
    orderable = frozenset(
        {"id", "displayName", "createdDateTime"}
        | ({"userPrincipalName"} if kind == "user" else set())
    )
    return QueryContract(version, resource, kind, fields, orderable)


def field_column(field: FieldContract):
    typed = {
        "id": ObjectVersion.object_id,
        "displayName": ObjectVersion.display_name,
        "userPrincipalName": ObjectVersion.upn,
        "createdDateTime": ObjectVersion.created_at,
        "accountEnabled": ObjectVersion.account_enabled,
        "mailNickname": ObjectVersion.mail_nickname,
    }
    if field.name in typed:
        return typed[field.name]
    value = ObjectVersion.properties[field.name]
    if field.data_type == "bool":
        return value.as_boolean()
    if field.data_type == "array":
        return value
    return value.as_string()
