"""Evidence SQL resolves only declared fields; no Python post-filtering."""

from sqlalchemy import DateTime, Integer, String, cast

from mockazure.evidence.models import EvidenceVersion
from mockazure.providers.graph.evidence.contracts import CAPS, DEFAULTS, TIMES, public_fields
from mockazure.providers.graph.query.ast import Any, Boolean, OrderTerm, QuerySpec
from mockazure.providers.graph.query.compiler import compile_filter
from mockazure.providers.graph.query.contracts import unsupported
from mockazure.providers.graph.query.parser import FilterParser, selection


def resolver_for(payload=None):
    payload = EvidenceVersion.payload if payload is None else payload

    def resolve(field):
        if field.name == "id":
            return cast(EvidenceVersion.record_id, String)
        value = payload
        for part in field.name.split("/"):
            value = value[part]
        if field.data_type in {"array_eq", "structured_array"}:
            return value
        if field.data_type == "bool":
            return value.as_boolean()
        if field.data_type == "int":
            return cast(value.as_string(), Integer)
        if field.data_type == "datetime":
            return cast(value.as_string(), DateTime(timezone=True))
        return value.as_string()

    return resolve


def compile_evidence_filter(node, contract, payload=None):
    return compile_filter(node, contract, resolver=resolver_for(payload))


def contains_event_type(node):
    if isinstance(node, Any):
        return node.field.name == "signInEventTypes"
    return isinstance(node, Boolean) and any(contains_event_type(c) for c in node.children)


def parse_evidence_query(items, contract, *, item=False):
    values = dict(items)
    if len(values) != len(items):
        unsupported("Duplicate query options")
    try:
        if sum(len(k.encode("utf-8")) + len(v.encode("utf-8")) for k, v in items) > 16384:
            unsupported("Query exceeds 16 KiB")
    except UnicodeError:
        unsupported("Query must be valid Unicode")
    risk = contract.resource in {"riskyUser", "riskDetection"}
    allowed = {"$select"} if risk else set()
    if not item:
        allowed |= {"$filter", "$top"}
        if contract.orderable:
            allowed.add("$orderby")
    if not values.keys() <= allowed:
        unsupported("Query option is not supported by this evidence operation")
    filtered = (
        FilterParser(values["$filter"], max_bytes=16384).parse() if "$filter" in values else None
    )
    if filtered is not None:
        compile_evidence_filter(filtered, contract)
    selected = (
        selection(values["$select"], public_fields(contract.version, contract.resource))
        if "$select" in values
        else None
    )
    field = "id" if contract.resource == "riskyUser" else TIMES[contract.resource]
    direction = "asc" if field == "id" else "desc"
    if "$orderby" in values:
        parts = values["$orderby"].split()
        if not 1 <= len(parts) <= 2 or parts[0] not in contract.orderable:
            unsupported("Unsupported evidence ordering")
        field, direction = parts[0], parts[1] if len(parts) == 2 else "asc"
        if direction not in {"asc", "desc"}:
            unsupported("Unsupported evidence ordering")
    top = DEFAULTS[contract.resource]
    if "$top" in values:
        text = values["$top"]
        if not text.isascii() or not text.isdigit() or len(text) > 10 or int(text) < 1:
            unsupported("top must be a positive integer")
        top = min(int(text), CAPS[contract.resource])
    return QuerySpec(
        filter=filtered,
        select=selected,
        top=top,
        order=(OrderTerm(field, direction),) + ((OrderTerm("id"),) if field != "id" else ()),
    )
