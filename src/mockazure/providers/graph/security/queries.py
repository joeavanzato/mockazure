import re
from dataclasses import replace

from sqlalchemy import Uuid, cast

from mockazure.providers.graph.query.ast import ExpandSpec
from mockazure.providers.graph.query.compiler import compile_filter
from mockazure.providers.graph.query.contracts import FieldContract, QueryContract, unsupported
from mockazure.providers.graph.query.parser import parse_query, selection

FIELDS = {
    "unifiedRoleDefinition": {"id", "templateId", "displayName", "isBuiltIn", "isEnabled"},
    "unifiedRoleAssignment": {
        "id",
        "principalId",
        "roleDefinitionId",
        "directoryScopeId",
        "appScopeId",
    },
    "directoryRole": {"id", "displayName", "roleTemplateId"},
    "directoryRoleMember": {"id", "@odata.type", "displayName", "userPrincipalName", "appId"},
    "oAuth2PermissionGrant": {
        "id",
        "clientId",
        "resourceId",
        "principalId",
        "consentType",
        "scope",
    },
    "appRoleAssignment": {
        "id",
        "principalId",
        "resourceId",
        "appRoleId",
        "principalType",
        "principalDisplayName",
        "resourceDisplayName",
        "createdDateTime",
    },
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
    if kind in {"oAuth2PermissionGrant", "appRoleAssignment"}:
        for name in {"id", "clientId", "resourceId", "principalId", "appRoleId"} & fields.keys():
            fields[name] = FieldContract(name, "uuid", frozenset({"eq"}))
        if "consentType" in fields:
            fields["consentType"] = FieldContract("consentType", "string", frozenset({"eq"}))
        return QueryContract(version, kind, kind, fields, frozenset({"id"}))
    for name in {
        "id",
        "appId",
        "deviceId",
        "templateId",
        "roleTemplateId",
        "principalId",
        "roleDefinitionId",
    } & fields.keys():
        fields[name] = FieldContract(name, "uuid", frozenset({"eq", "ne", "in"}))
    for name in {
        "displayName",
        "servicePrincipalType",
        "operatingSystem",
        "trustType",
    } & fields.keys():
        fields[name] = FieldContract(name, "string", frozenset({"eq", "ne", "in", "startswith"}))
    for name in {
        "accountEnabled",
        "isCompliant",
        "isManaged",
        "isBuiltIn",
        "isEnabled",
    } & fields.keys():
        fields[name] = FieldContract(name, "bool", frozenset({"eq", "ne"}))
    return QueryContract(
        version, kind, kind, fields, frozenset({"id", "displayName"} & fields.keys())
    )


def parse_security_query(request, kind):
    items = list(request.query_params.multi_items())
    expand = request.query_params.get("$expand")
    if len(dict(items)) != len(items):
        unsupported("Duplicate query options")
    query = parse_query(
        [(key, value) for key, value in items if key != "$expand"],
        contract_for(kind, request.url.path.split("/")[1]),
        request.headers.get("consistencylevel"),
    )
    if expand is not None:
        if kind == "unifiedRoleAssignment":
            match = re.fullmatch(r"(principal|roleDefinition)(?:\(\$select=([^()]+)\))?", expand)
            if match is None:
                unsupported("Only a bounded principal or roleDefinition expansion is supported")
            names = (
                {"id", "@odata.type", "displayName", "userPrincipalName", "appId"}
                if match[1] == "principal"
                else FIELDS["unifiedRoleDefinition"]
            )
            fields = selection(match[2], names) if match[2] else None
            return replace(query, expand=(ExpandSpec(match[1], fields),))
        if kind in {"directoryRole", "directoryRoleMember", "unifiedRoleDefinition"}:
            unsupported("Role relationship expansion is outside this profile")
        if kind in {"oAuth2PermissionGrant", "appRoleAssignment"}:
            unsupported("Grant expansion is outside this profile")
        relation = "registeredOwners" if kind == "device" else "owners"
        match = re.fullmatch(relation + r"(?:\(\$select=([^()]+)\))?", expand)
        if match is None or query.count:
            unsupported("Only bounded owner expansion without advanced queries is supported")
        fields = (
            selection(match[1], {"id", "displayName", "userPrincipalName", "@odata.type"})
            if match[1]
            else None
        )
        query = replace(query, expand=(ExpandSpec(relation, fields),))
    return query


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
