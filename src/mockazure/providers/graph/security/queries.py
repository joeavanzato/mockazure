from sqlalchemy import Uuid, cast

from mockazure.providers.graph.query.compiler import compile_filter
from mockazure.providers.graph.query.contracts import FieldContract, QueryContract
from mockazure.providers.graph.query.parser import parse_query

FIELDS = {
    "application": {
        "id",
        "appId",
        "displayName",
        "description",
        "signInAudience",
        "identifierUris",
        "tags",
        "passwordCredentials",
        "keyCredentials",
        "requiredResourceAccess",
        "web",
        "publicClient",
        "spa",
        "api",
        "appRoles",
        "createdDateTime",
        "publisherDomain",
    },
    "servicePrincipal": {
        "id",
        "appId",
        "displayName",
        "description",
        "accountEnabled",
        "servicePrincipalType",
        "tags",
        "passwordCredentials",
        "keyCredentials",
        "appRoles",
        "oauth2PermissionScopes",
        "servicePrincipalNames",
        "appOwnerOrganizationId",
        "appRoleAssignmentRequired",
    },
    "device": {
        "id",
        "deviceId",
        "displayName",
        "accountEnabled",
        "operatingSystem",
        "operatingSystemVersion",
        "trustType",
        "isCompliant",
        "isManaged",
        "approximateLastSignInDateTime",
        "registrationDateTime",
    },
}


def contract_for(kind, version):
    fields = {name: FieldContract(name, "string", frozenset()) for name in FIELDS[kind]}
    for name in {"id", "appId", "deviceId"} & fields.keys():
        fields[name] = FieldContract(name, "uuid", frozenset({"eq", "ne", "in"}))
    for name in {
        "displayName",
        "servicePrincipalType",
        "operatingSystem",
        "trustType",
    } & fields.keys():
        fields[name] = FieldContract(name, "string", frozenset({"eq", "ne", "in", "startswith"}))
    for name in {"accountEnabled", "isCompliant", "isManaged"} & fields.keys():
        fields[name] = FieldContract(name, "bool", frozenset({"eq", "ne"}))
    return QueryContract(version, kind, kind, fields, frozenset({"id", "displayName"}))


def parse_security_query(request, kind):
    return parse_query(
        list(request.query_params.multi_items()),
        contract_for(kind, request.url.path.split("/")[1]),
        request.headers.get("consistencylevel"),
    )


def resolve(source, field):
    if field.name == "id":
        return source.c.id
    value = source.c.payload[field.name]
    if field.data_type == "uuid":
        return cast(value.as_string(), Uuid)
    if field.data_type == "bool":
        return value.as_boolean()
    return value.as_string()


def filtered(source, query, contract):
    if query.filter is None:
        return True
    return compile_filter(query.filter, contract, resolver=lambda field: resolve(source, field))
