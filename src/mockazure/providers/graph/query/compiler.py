from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
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
        elif field.data_type == "uuid":
            value = UUID(value)
        elif field.data_type == "datetime":
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if value.tzinfo is None:
                raise ValueError()
        elif field.data_type == "string":
            if not isinstance(value, str):
                raise ValueError()
        else:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        unsupported("Filter literal type does not match the property")


def compile_filter(node, contract: QueryContract, variables=None) -> ColumnElement[bool]:
    variables = variables or {}

    def field(name, operator):
        if name in variables:
            spec, column = variables[name]
        elif name in contract.fields:
            spec = contract.fields[name]
            column = field_column(spec)
        else:
            unsupported("Unknown filter property")
        if operator not in spec.operators:
            unsupported("Operator is not supported for this property")
        return spec, column

    if isinstance(node, Boolean):
        children = [compile_filter(child, contract, variables) for child in node.children]
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
        return column.in_([typed_value(value.value, spec) for value in node.values])
    if isinstance(node, StartsWith):
        spec, column = field(node.field.name, "startswith")
        value = typed_value(node.value.value, spec)
        if value is None:
            unsupported("startswith requires a string")
        return column.startswith(value, autoescape=True)
    if isinstance(node, Any):
        _, column = field(node.field.name, "any")
        values = func.jsonb_array_elements_text(column).table_valued("value").alias()
        inner = {
            **variables,
            node.variable: (
                FieldContract(node.variable, "string", frozenset({"eq", "ne", "in"})),
                values.c.value,
            ),
        }
        return exists(
            select(1).select_from(values).where(compile_filter(node.body, contract, inner))
        )
    unsupported("Unsupported filter node")
