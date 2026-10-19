"""Allowlisted recursive-descent pipelines and Pratt scalar expressions."""

import re
from datetime import timedelta

from .ast import (
    Access,
    Binary,
    Binding,
    Call,
    Literal,
    Name,
    Named,
    Operation,
    Pipeline,
    Unary,
    Values,
)
from .profiles import OPERATORS, validate_profile
from .types import QueryError, QueryPlan

TOKEN = re.compile(
    r"\s+|//[^\n]*|(?:'([^'\\]|\\.|'')*'|\"([^\"\\]|\\.|\"\")*\")|\d+(?:\.\d+)?(?:ms|us|ticks|d|h|m|s)\b|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|(?:project-away|project-rename|mv-expand)|!?[A-Za-z_$][A-Za-z0-9_$]*(?:~)?|==|!=|=~|!~|>=|<=|[|(),.;\[\]{}=+*/%<>:-]"
)
PRECEDENCE = {
    "or": 1,
    "and": 2,
    "==": 3,
    "!=": 3,
    "=~": 3,
    "!~": 3,
    ">": 3,
    "<": 3,
    ">=": 3,
    "<=": 3,
    "contains": 3,
    "contains_cs": 3,
    "!contains": 3,
    "!contains_cs": 3,
    "has": 3,
    "has_cs": 3,
    "!has": 3,
    "!has_cs": 3,
    "startswith": 3,
    "startswith_cs": 3,
    "!startswith": 3,
    "!startswith_cs": 3,
    "endswith": 3,
    "endswith_cs": 3,
    "!endswith": 3,
    "!endswith_cs": 3,
    "in": 3,
    "in~": 3,
    "!in": 3,
    "!in~": 3,
    "+": 4,
    "-": 4,
    "*": 5,
    "/": 5,
    "%": 5,
}


def tokens(text):
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 65536:
        raise QueryError("Query must contain 1–65536 UTF-8 bytes")
    result = []
    end = 0
    for match in TOKEN.finditer(text):
        if match.start() != end:
            raise QueryError("Unrecognized query syntax")
        end = match.end()
        value = match.group()
        if not value.isspace() and not value.startswith("//"):
            result.append(value)
    if end != len(text) or len(result) > 10000:
        raise QueryError("Unrecognized or oversized query syntax")
    return result + [""]


def string_value(token):
    quote = token[0]
    value = token[1:-1]
    out = []
    i = 0
    escapes = {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "b": "\b",
        "f": "\f",
        "\\": "\\",
        "'": "'",
        '"': '"',
    }
    while i < len(value):
        if value[i : i + 2] == quote * 2:
            out.append(quote)
            i += 2
        elif value[i] == "\\":
            i += 1
            if i >= len(value) or value[i] not in escapes:
                raise QueryError("Unsupported string escape")
            out.append(escapes[value[i]])
            i += 1
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


class Parser:
    def __init__(self, text, profile):
        validate_profile(profile, {})
        self.tokens = tokens(text)
        self.index = 0
        self.profile = profile
        self.depth = 0
        self.counts = {}
        self.table_names = set()
        self.right_tables = set()

    def peek(self):
        return self.tokens[self.index]

    def pop(self):
        value = self.peek()
        if not value:
            raise QueryError("Unexpected end of query")
        self.index += 1
        return value

    def accept(self, value):
        if self.peek() == value:
            self.index += 1
            return True
        return False

    def expect(self, value):
        if not self.accept(value):
            raise QueryError("Expected " + value)

    def name(self):
        value = self.pop()
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", value):
            raise QueryError("Expected identifier")
        return value

    def expression(self, minimum=0):
        self.depth += 1
        if self.depth > 64:
            raise QueryError("Query nesting limit exceeded")
        try:
            token = self.pop()
            if token in ("+", "-"):
                left = Unary(token, self.expression(6))
            elif token == "(":
                left = self.expression()
                self.expect(")")
            elif token[0] in ("'", '"'):
                left = Literal(string_value(token), "string")
            elif token in ("true", "false", "null"):
                left = Literal(
                    {"true": True, "false": False, "null": None}[token],
                    "bool" if token != "null" else "null",
                )
            elif re.fullmatch(r"\d+(?:\.\d+)?(?:ms|us|ticks|d|h|m|s)", token):
                match = re.fullmatch(r"([\d.]+)(\D+)", token)
                units = {
                    "d": 86400,
                    "h": 3600,
                    "m": 60,
                    "s": 1,
                    "ms": 0.001,
                    "us": 0.000001,
                    "ticks": 0.0000001,
                }
                left = Literal(timedelta(seconds=float(match[1]) * units[match[2]]), "timespan")
            elif re.fullmatch(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", token):
                real = any(c in token for c in ".eE")
                left = Literal(float(token) if real else int(token), "real" if real else "long")
            elif re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token):
                if self.accept("("):
                    arguments = []
                    if token in ("datetime", "timespan") and re.match(r"[0-9]", self.peek()):
                        parts = []
                        while self.peek() and self.peek() != ")":
                            part = self.pop()
                            if part in ("|", ";", "(", ","):
                                raise QueryError("Invalid typed literal")
                            parts.append(part)
                        self.expect(")")
                        arguments.append(Literal("".join(parts), "string"))
                    elif not self.accept(")"):
                        while True:
                            arguments.append(Name("*") if self.accept("*") else self.expression())
                            if self.accept(")"):
                                break
                            self.expect(",")
                    left = Call(token, tuple(arguments))
                else:
                    left = Name(token)
            else:
                raise QueryError("Invalid scalar expression")
            while True:
                if self.accept("."):
                    left = Access(left, Literal(self.name(), "string"))
                    continue
                if self.accept("["):
                    key = self.expression()
                    self.expect("]")
                    left = Access(left, key)
                    continue
                op = self.peek()
                precedence = PRECEDENCE.get(op, 0)
                if precedence == 0 or precedence < minimum:
                    break
                self.pop()
                if op in ("in", "in~", "!in", "!in~"):
                    self.expect("(")
                    values = [self.expression()]
                    while self.accept(","):
                        values.append(self.expression())
                    self.expect(")")
                    right = Values(tuple(values))
                else:
                    right = self.expression(precedence + 1)
                left = Binary(op, left, right)
            return left
        finally:
            self.depth -= 1

    def named(self):
        if self.accept("*"):
            return Named(None, Name("*"))
        if self.index + 1 < len(self.tokens) and self.tokens[self.index + 1] == "=":
            name = self.name()
            self.expect("=")
            return Named(name, self.expression())
        return Named(None, self.expression())

    def expressions(self, named=True):
        values = [self.named() if named else self.expression()]
        while self.accept(","):
            values.append(self.named() if named else self.expression())
        return tuple(values)

    def number(self):
        value = self.pop()
        if not value.isdecimal() or len(value) > 7:
            raise QueryError("Expected bounded nonnegative integer")
        return int(value)

    def ordering(self):
        items = []
        while True:
            value = self.expression()
            direction = self.pop() if self.peek() in ("asc", "desc") else "desc"
            nulls = "first" if direction == "asc" else "last"
            if self.accept("nulls"):
                nulls = self.pop()
                if nulls not in ("first", "last"):
                    raise QueryError("Invalid null ordering")
            items.append((value, direction, nulls))
            if not self.accept(","):
                return tuple(items)

    def pipeline(self):
        self.depth += 1
        if self.depth > 64:
            raise QueryError("Query nesting limit exceeded")
        try:
            source = self.name()
            operations = []
            while self.accept("|"):
                op = self.pop()
                if op not in OPERATORS:
                    raise QueryError("Unsupported query operator: " + op)
                op = {"sort": "order", "limit": "take", "mvexpand": "mv-expand"}.get(op, op)
                self.counts[op] = self.counts.get(op, 0) + 1
                args = ()
                options = ()
                if op == "where":
                    args = (self.expression(),)
                elif op in ("project", "project-away", "project-rename", "extend", "distinct"):
                    args = self.expressions()
                elif op == "take":
                    args = (self.number(),)
                elif op == "count":
                    pass
                elif op == "order":
                    self.expect("by")
                    args = self.ordering()
                elif op == "top":
                    count = self.number()
                    self.expect("by")
                    args = self.ordering()
                    options = (count,)
                elif op == "summarize":
                    args = () if self.peek() == "by" else self.expressions()
                    options = self.expressions() if self.accept("by") else ()
                elif op == "union":
                    kind = "outer"
                    if self.accept("kind"):
                        self.expect("=")
                        kind = self.pop()
                    if kind not in ("inner", "outer"):
                        raise QueryError("Unsupported union kind")
                    if self.accept("("):
                        other = self.pipeline()
                        self.expect(")")
                    else:
                        other = Pipeline(self.name(), ())
                    args = (other,)
                    options = (kind,)
                elif op == "join":
                    flavor = "innerunique"
                    if self.accept("kind"):
                        self.expect("=")
                        flavor = self.pop()
                    validate_profile(self.profile, self.counts, flavor=flavor)
                    self.expect("(")
                    other = self.pipeline()
                    self.expect(")")
                    self.expect("on")
                    if self.profile == "resourcegraph":
                        if other.source in self.right_tables:
                            raise QueryError(
                                "A Resource Graph right table cannot be referenced repeatedly"
                            )
                        self.right_tables.add(other.source)
                    args = (other,)
                    options = (flavor, self.expressions(False))
                elif op == "mv-expand":
                    args = self.expressions()
                    kind = None
                    if self.accept("to"):
                        self.expect("typeof")
                        self.expect("(")
                        kind = self.name()
                        self.expect(")")
                    limit = (
                        self.number()
                        if self.accept("limit")
                        else (128 if self.profile == "resourcegraph" else 1000000)
                    )
                    validate_profile(self.profile, self.counts, expansion=limit)
                    options = (limit, kind)
                validate_profile(self.profile, self.counts)
                operations.append(Operation(op, args, options))
            return Pipeline(source, tuple(operations))
        finally:
            self.depth -= 1

    def parse(self):
        nodes = []
        while self.accept("let"):
            if self.profile == "resourcegraph":
                raise QueryError("Resource Graph does not support let")
            name = self.name()
            self.expect("=")
            # A tabular binding has a pipeline or a previously bound table name.
            j = self.index
            while self.tokens[j] not in (";", ""):
                j += 1
            if (
                "|" in self.tokens[self.index : j]
                or self.peek() in self.table_names
                or self.peek() in ("Resources", "ResourceContainers")
            ):
                value = self.pipeline()
                self.table_names.add(name)
            else:
                value = self.expression()
            nodes.append(Binding(name, value))
            self.expect(";")
        if self.profile == "resourcegraph" and self.peek() in OPERATORS:
            self.tokens[self.index : self.index] = ["Resources", "|"]
        nodes.append(self.pipeline())
        self.accept(";")
        if self.peek():
            raise QueryError("Unexpected query suffix")
        return QueryPlan(self.profile, tuple(nodes))


def parse_query(text, profile):
    try:
        return Parser(text, profile).parse()
    except (RecursionError, OverflowError) as error:
        raise QueryError("Query complexity limit exceeded") from error
