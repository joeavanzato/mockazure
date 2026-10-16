"""Bounded recursive-descent parser. No eval, SQL fragments or dynamic field lookup."""

import re

from mockazure.providers.graph.query.ast import (
    Any,
    Boolean,
    Compare,
    ExpandSpec,
    Field,
    In,
    Literal,
    OrderTerm,
    QuerySpec,
    StartsWith,
)
from mockazure.providers.graph.query.compiler import compile_filter
from mockazure.providers.graph.query.contracts import QueryContract, unsupported

TOKEN = re.compile(
    r"\s*(?:(?P<string>'(?:[^']|'')*')|"
    r"(?P<date>\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2}))|"
    r"(?P<number>-?\d+)|(?P<name>[A-Za-z_][A-Za-z_0-9]*)|(?P<symbol>[(),/:]))"
)


class FilterParser:
    def __init__(self, value: str):
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError:
            unsupported("Filter must be valid UTF-8 text")
        if size > 8192:
            unsupported("Filter exceeds 8 KiB")
        self.tokens, self.index, self.nodes = [], 0, 0
        position = 0
        while position < len(value):
            if not value[position:].strip():
                break
            match = TOKEN.match(value, position)
            if match is None:
                unsupported("Invalid filter syntax")
            self.tokens.append((match.lastgroup, match.group(match.lastgroup)))
            position = match.end()
            if len(self.tokens) > 2048:
                unsupported("Filter is too complex")

    def peek(self, value=None):
        token = self.tokens[self.index][1] if self.index < len(self.tokens) else None
        return token == value if value is not None else token

    def take(self, value=None):
        if self.index >= len(self.tokens) or (value is not None and not self.peek(value)):
            unsupported("Invalid filter syntax")
        token = self.tokens[self.index]
        self.index += 1
        return token

    def node(self, value):
        self.nodes += 1
        if self.nodes > 256:
            unsupported("Filter exceeds 256 nodes")
        return value

    def literal(self):
        kind, value = self.take()
        if kind == "string":
            result = value[1:-1].replace("''", "'")
        elif kind == "number":
            if len(value.lstrip("-")) > 20:
                unsupported("Numeric literal exceeds its local size limit")
            try:
                result = int(value)
            except ValueError:
                unsupported("Invalid numeric literal")
        elif kind == "date":
            result = value
        elif value in {"true", "false", "null"}:
            result = {"true": True, "false": False, "null": None}[value]
        else:
            unsupported("Expected a filter literal")
        return self.node(Literal(result))

    def field(self):
        kind, value = self.take()
        if kind != "name":
            unsupported("Expected a property")
        parts = [value]
        while self.peek("/") and self.index + 1 < len(self.tokens):
            if self.tokens[self.index + 1][1] == "any":
                break
            self.take("/")
            kind, value = self.take()
            if kind != "name":
                unsupported("Expected a property path")
            parts.append(value)
        return self.node(Field("/".join(parts)))

    def expression(self, depth=0):
        if depth > 16:
            unsupported("Filter nesting exceeds 16")
        children = [self.conjunction(depth)]
        while self.peek("or"):
            self.take("or")
            children.append(self.conjunction(depth))
        return children[0] if len(children) == 1 else self.node(Boolean("or", tuple(children)))

    def conjunction(self, depth):
        children = [self.primary(depth)]
        while self.peek("and"):
            self.take("and")
            children.append(self.primary(depth))
        return children[0] if len(children) == 1 else self.node(Boolean("and", tuple(children)))

    def primary(self, depth):
        if self.peek("("):
            self.take("(")
            result = self.expression(depth + 1)
            self.take(")")
            return result
        if str(self.peek()).lower() == "startswith":
            self.take()
            self.take("(")
            field = self.field()
            self.take(",")
            literal = self.literal()
            self.take(")")
            return self.node(StartsWith(field, literal))
        field = self.field()
        if self.peek("/"):
            self.take("/")
            self.take("any")
            self.take("(")
            kind, variable = self.take()
            if kind != "name":
                unsupported("Expected lambda variable")
            self.take(":")
            body = self.expression(depth + 1)
            self.take(")")
            return self.node(Any(field, variable, body))
        _, operator = self.take()
        if operator == "in":
            self.take("(")
            values = [self.literal()]
            while self.peek(","):
                self.take(",")
                values.append(self.literal())
                if len(values) > 100:
                    unsupported("in exceeds 100 values")
            self.take(")")
            return self.node(In(field, tuple(values)))
        if operator not in {"eq", "ne", "ge", "gt", "le", "lt"}:
            unsupported("Unsupported comparison")
        return self.node(Compare(operator, field, self.literal()))

    def parse(self):
        result = self.expression()
        if self.index != len(self.tokens):
            unsupported("Trailing filter input")
        return result


def selection(value: str, allowed) -> tuple[str, ...]:
    values = tuple(part.strip() for part in value.split(","))
    if not all(values) or len(set(values)) != len(values) or not set(values) <= set(allowed):
        unsupported("Selected fields are not supported")
    return values


def needs_advanced(node):
    if isinstance(node, Compare):
        return node.op == "ne"
    if isinstance(node, Boolean):
        return any(needs_advanced(child) for child in node.children)
    if isinstance(node, Any):
        return needs_advanced(node.body)
    return False


def parse_query(
    items: list[tuple[str, str]], contract: QueryContract, consistency: str | None
) -> QuerySpec:
    values = dict(items)
    if len(values) != len(items):
        unsupported("Duplicate query options")
    if not values.keys() <= {"$filter", "$select", "$orderby", "$top", "$count", "$expand"}:
        unsupported("Query option is not supported")
    if sum(len(k.encode()) + len(v.encode()) for k, v in items) > 16384:
        unsupported("Query exceeds 16 KiB")
    filtered = FilterParser(values["$filter"]).parse() if "$filter" in values else None
    if filtered is not None:
        compile_filter(filtered, contract)
    selected = selection(values["$select"], contract.fields) if "$select" in values else None
    order = []
    if "$orderby" in values:
        for term in values["$orderby"].split(","):
            parts = term.split()
            if not 1 <= len(parts) <= 2 or parts[0] not in contract.orderable:
                unsupported("Unsupported order property")
            direction = parts[1] if len(parts) == 2 else "asc"
            if direction not in {"asc", "desc"} or parts[0] in {item.field for item in order}:
                unsupported("Invalid ordering")
            order.append(OrderTerm(parts[0], direction))
    if not any(item.field == "id" for item in order):
        order.append(OrderTerm("id"))
    top = None
    if "$top" in values:
        try:
            if not values["$top"].isascii() or not values["$top"].isdigit():
                raise ValueError()
            top = int(values["$top"])
            if not 1 <= top <= 2**31 - 1:
                raise ValueError()
        except ValueError:
            unsupported("top must be a positive integer")
    if values.get("$count", "false") not in {"true", "false"}:
        unsupported("count must be true or false")
    advanced = consistency == "eventual" and values.get("$count") == "true"
    if "$filter" in values and "$orderby" in values and not advanced:
        unsupported("Filter with order requires ConsistencyLevel:eventual and $count=true")
    if needs_advanced(filtered) and not advanced:
        unsupported("ne requires ConsistencyLevel:eventual and $count=true")
    expand = []
    if "$expand" in values:
        if contract.kind != "group" or advanced:
            unsupported("Expansion is not supported with this query")
        match = re.fullmatch(r"members(?:\(\$select=([^()]+)\))?", values["$expand"])
        if match is None:
            unsupported("Only members with optional select can be expanded")
        from mockazure.directory.types import USER_FIELDS

        fields = selection(match[1], USER_FIELDS) if match[1] else None
        expand.append(ExpandSpec("members", fields))
    return QuerySpec(
        filtered,
        selected,
        tuple(order),
        top,
        values.get("$count") == "true" and consistency == "eventual",
        tuple(expand),
    )
