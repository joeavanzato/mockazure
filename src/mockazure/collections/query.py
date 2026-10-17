"""Declared property filtering, precise timestamp comparisons and bounded query options."""

import math

from sqlalchemy import BigInteger, Numeric, and_, cast, or_

from mockazure.collections.contracts import MODERN_FIELDS, properties
from mockazure.collections.models import CollectionRecord as R
from mockazure.providers.arm.activity import instant
from mockazure.providers.graph.query.ast import Boolean, Compare
from mockazure.providers.graph.query.compiler import compile_filter
from mockazure.providers.graph.query.contracts import FieldContract, QueryContract, unsupported
from mockazure.providers.graph.query.parser import FilterParser, selection

COMPARISONS = frozenset({"eq", "ne", "gt", "ge", "lt", "le"})


def contract(family):
    fields = {}
    for name, p in properties(family).items():
        kind = p["type"]
        data_type = {
            "Edm.String": "string",
            "Edm.Boolean": "bool",
            "Edm.Int32": "int",
            "Edm.DateTimeOffset": "datetime",
            "Edm.Double": "number",
        }.get(kind)
        if data_type is None and not kind.startswith(("Edm.", "Collection(")):
            from mockazure.providers.graph.schema_reference import schema

            if "enum" in schema("v1.0")["types"].get(
                kind.removeprefix("microsoft.graph.").removeprefix("graph."), {}
            ):
                data_type = "string"
        if data_type:
            operators = (
                COMPARISONS
                if data_type in {"datetime", "int", "number"}
                else frozenset({"eq", "ne", "in"})
            )
            fields[name] = FieldContract(name, data_type, operators)
    if family in {"alerts_v2", "incidents"}:
        names = MODERN_FIELDS | ({"serviceSource"} if family == "alerts_v2" else set())
        fields = {k: v for k, v in fields.items() if k in names}
    if family == "alerts":
        for name in ("provider", "vendor"):
            fields["vendorInformation/" + name] = FieldContract(
                "vendorInformation/" + name, "string", frozenset({"eq", "ne", "in"})
            )
    return QueryContract(
        "v1.0", family, family, fields, frozenset(fields) if family == "alerts" else frozenset()
    )


def column(field):
    if field.data_type == "datetime":
        return cast(R.times[field.name].as_string(), BigInteger)
    value = R.payload
    for part in field.name.split("/"):
        value = value[part]
    if field.data_type == "bool":
        return value.as_boolean()
    if field.data_type in {"int", "number"}:
        return cast(value.as_string(), Numeric)
    return value.as_string()


def compiled(node, profile):
    if isinstance(node, Boolean):
        return (and_ if node.op == "and" else or_)(*(compiled(c, profile) for c in node.children))
    if (
        isinstance(node, Compare)
        and (field := profile.fields.get(node.left.name))
        and field.data_type in {"datetime", "number"}
    ):
        if node.op not in field.operators:
            unsupported("Unsupported property comparison")
        value = node.right.value
        col = column(field)
        if value is None:
            if node.op not in {"eq", "ne"}:
                unsupported("Null only supports equality")
            return col.is_(None) if node.op == "eq" else col.is_not(None)
        try:
            if field.data_type == "datetime":
                value = instant(value)[1]
            elif type(value) not in {int, float} or not math.isfinite(value):
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            unsupported("Filter literal does not match the property type")
        return {
            "eq": col.__eq__,
            "ne": col.__ne__,
            "gt": col.__gt__,
            "ge": col.__ge__,
            "lt": col.__lt__,
            "le": col.__le__,
        }[node.op](value)
    return compile_filter(node, profile, resolver=column)


def parse(items, family, *, item=False):
    values = dict(items)
    if (
        len(items) != len(values)
        or sum(len(k.encode()) + len(v.encode()) for k, v in items) > 16384
    ):
        unsupported("Duplicate or oversized query options")
    allowed = set() if item else {"$filter", "$count", "$skip", "$top"}
    if family == "alerts":
        allowed |= {"$select"} | ({"$orderby"} if not item else set())
    if family == "incidents":
        allowed.add("$expand")
    if not values.keys() <= allowed:
        unsupported("Unsupported query option for this security collection")
    if "$count" in values and values["$count"] not in {"true", "false"}:
        unsupported("Invalid count")
    if "$expand" in values and values["$expand"] != "alerts":
        unsupported("Only incident alerts expansion is supported")
    for key in ("$skip", "$top"):
        if key in values:
            value = values[key]
            if (
                not value.isascii()
                or not value.isdigit()
                or len(value) > 7
                or int(value) < (key == "$top")
            ):
                unsupported("Invalid security collection page bound")
    if int(values.get("$skip", "0")) > (500 if family == "alerts" else 100000):
        unsupported("Skip exceeds the supported resource bound")
    profile = contract(family)
    if "$filter" in values:
        compiled(FilterParser(values["$filter"], max_bytes=16384).parse(), profile)
    if "$select" in values:
        selection(values["$select"], properties(family))
    if "$orderby" in values:
        terms = values["$orderby"].split()
        if (
            not 1 <= len(terms) <= 2
            or terms[0] not in profile.orderable
            or (len(terms) == 2 and terms[1] not in {"asc", "desc"})
        ):
            unsupported("Unsupported collection ordering")
    return values


def ordering(family, values):
    if "$orderby" in values:
        parts = values["$orderby"].split()
        return parts[0], parts[1] if len(parts) == 2 else "asc"
    return ("id", "asc") if family == "secureScoreControlProfiles" else ("createdDateTime", "desc")


def order_field(family, name):
    if name == "id":
        return R.record_id
    if name == "createdDateTime":
        return cast(R.times[name].as_string(), BigInteger)
    return column(contract(family).fields[name])
