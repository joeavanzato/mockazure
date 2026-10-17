"""Graph rule serialization and bounded query profile."""

from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.query.ast import QuerySpec
from mockazure.providers.graph.schema_reference import schema

FIELDS = frozenset(schema("v1.0")["types"]["messageRule"]["properties"])


def rule_view(row):
    return row.payload | {
        "id": row.id,
        "sequence": row.sequence,
        "isReadOnly": row.read_only,
        "hasError": False,
    }


def selected(value, fields):
    return {key: value[key] for key in fields if key in value} if fields else value


def rule_query(pairs, *, item=False):
    if len(dict(pairs)) != len(pairs) or set(dict(pairs)) - {"$select", "$top"}:
        raise GraphError(400, "Request_UnsupportedQuery", "Unsupported rule query option")
    values = dict(pairs)
    fields = None
    if "$select" in values:
        fields = tuple(part.strip() for part in values["$select"].split(","))
        if not fields or not set(fields) <= FIELDS or len(set(fields)) != len(fields):
            raise GraphError(400, "Request_UnsupportedQuery", "Invalid rule property selection")
    top = None
    if "$top" in values:
        value = values["$top"]
        if item or not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 1000:
            raise GraphError(400, "Request_UnsupportedQuery", "Rule top must be between1 and1000")
        top = int(value)
    return QuerySpec(select=fields, top=top)
