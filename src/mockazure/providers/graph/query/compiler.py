from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, case, exists, func, literal, or_, select
from sqlalchemy import column as sql_column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.elements import ColumnElement

from mockazure.providers.graph.query.ast import Any, Boolean, Compare, In, StartsWith
from mockazure.providers.graph.query.contracts import (
    FieldContract,
    QueryContract,
    field_column,
    unsupported,
)


def typed_value(value, field: FieldContract):
    if value is None:
        if field.data_type in {"uuid", "array"}:
            unsupported("Null is not supported for this field")
        return None
    try:
        if field.data_type == "bool":
            if type(value) is not bool:
                raise ValueError()
        elif field.data_type == "int":
            if type(value) is not int or not -(2**31) <= value < 2**31:
                raise ValueError()
        elif field.data_type == "uuid":
            value = UUID(value)
        elif field.data_type == "datetime":
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if value.tzinfo is None:
                raise ValueError()
        elif field.data_type == "string":
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError()
            value.encode("utf-8")
        else:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        unsupported("Filter literal type does not match the property")


def compile_filter(
    node, contract: QueryContract, variables=None, *, resolver=None
) -> ColumnElement[bool]:
    variables = variables or {}

    def field(name, operator):
        if name in variables:
            spec, column = variables[name]
        elif name in contract.fields:
            spec = contract.fields[name]
            column = (resolver or field_column)(spec)
        else:
            unsupported("Unknown filter property")
        if operator not in spec.operators:
            unsupported("Operator is not supported for this property")
        return spec, column

    if isinstance(node, Boolean):
        children = [
            compile_filter(child, contract, variables, resolver=resolver) for child in node.children
        ]
        return (and_ if node.op == "and" else or_)(*children)
    if isinstance(node, Compare):
        spec, column = field(node.left.name, node.op)
        value = typed_value(node.right.value, spec)
        if value is None:
            if node.op not in {"eq", "ne"}:
                unsupported("Null only supports equality comparisons")
            return column.is_(None) if node.op == "eq" else column.is_not(None)
        return {
            "eq": column.__eq__,
            "ne": column.__ne__,
            "ge": column.__ge__,
            "gt": column.__gt__,
            "le": column.__le__,
            "lt": column.__lt__,
        }[node.op](value)
    if isinstance(node, In):
        spec, column = field(node.field.name, "in")
        values = [typed_value(value.value, spec) for value in node.values]
        non_null = [value for value in values if value is not None]
        if len(non_null) == len(values):
            return column.in_(non_null)
        if not non_null:
            return column.is_(None)
        return or_(column.in_(non_null), column.is_(None))
    if isinstance(node, StartsWith):
        spec, column = field(node.field.name, "startswith")
        value = typed_value(node.value.value, spec)
        if value is None:
            unsupported("startswith requires a string")
        return column.startswith(value, autoescape=True)
    if isinstance(node, Any):
        spec, column = field(node.field.name, "any")
        # Valid nullable collections (including omitted fields) have no members.
        column = case(
            (func.jsonb_typeof(column) == "array", column),
            else_=literal([], type_=JSONB),
        )
        if spec.data_type == "structured_array":
            values = (
                func.jsonb_array_elements(column).table_valued(sql_column("value", JSONB)).alias()
            )
            inner = {
                node.variable + "/" + name: (
                    FieldContract(name, "string", frozenset({"eq"})),
                    values.c.value[name].as_string(),
                )
                for name in ("id", "type", "userPrincipalName")
            }
            restricted = QueryContract(
                contract.version, contract.resource, contract.kind, {}, frozenset()
            )
            return exists(
                select(1)
                .select_from(values)
                .where(compile_filter(node.body, restricted, inner, resolver=resolver))
            )
        values = func.jsonb_array_elements_text(column).table_valued("value").alias()
        inner = {
            **variables,
            node.variable: (
                FieldContract(
                    node.variable,
                    "string",
                    frozenset({"eq"})
                    if spec.data_type == "array_eq"
                    else frozenset({"eq", "ne", "in"}),
                ),
                values.c.value,
            ),
        }
        return exists(
            select(1)
            .select_from(values)
            .where(
                compile_filter(
                    node.body,
                    QueryContract(
                        contract.version, contract.resource, contract.kind, {}, frozenset()
                    )
                    if spec.data_type == "array_eq"
                    else contract,
                    inner,
                    resolver=resolver,
                )
            )
        )
    unsupported("Unsupported filter node")
