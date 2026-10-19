"""KQL scalar binding and values; never Python eval or raw SQL."""

import json
import math
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from .ast import Access, Binary, Call, Literal, Name, Unary, Values
from .types import QueryError

NUMERIC = {"int", "long", "real"}
CASTS = {
    "tostring": "string",
    "string": "string",
    "toint": "int",
    "int": "int",
    "tolong": "long",
    "long": "long",
    "todouble": "real",
    "toreal": "real",
    "real": "real",
    "double": "real",
    "todatetime": "datetime",
    "datetime": "datetime",
    "totimespan": "timespan",
    "timespan": "timespan",
    "tobool": "bool",
    "bool": "bool",
}
ARITIES = {
    "parse_json": (1, 1),
    "isnull": (1, 1),
    "isnotnull": (1, 1),
    "isempty": (1, 1),
    "isnotempty": (1, 1),
    "coalesce": (2, 64),
    "iff": (3, 3),
    "iif": (3, 3),
    "case": (3, 63),
    "strcat": (1, 64),
    "substring": (2, 3),
    "strlen": (1, 1),
    "tolower": (1, 1),
    "toupper": (1, 1),
    "now": (0, 1),
    "ago": (1, 1),
    "bin": (2, 2),
    "not": (1, 1),
}
AGGREGATES = {"count", "countif", "sum", "avg", "min", "max", "dcount", "arg_max"}


def unify(kinds):
    actual = set(kinds) - {"null"}
    if not actual:
        return "null"
    if len(actual) == 1:
        return next(iter(actual))
    if actual <= NUMERIC:
        return "real" if "real" in actual else "long"
    if "dynamic" in actual:
        return "dynamic"
    raise QueryError("Incompatible expression types")


def require(kind, allowed):
    if kind not in allowed:
        raise QueryError("Invalid scalar argument type")


def infer(node, columns, symbols):
    if isinstance(node, Literal):
        return node.kind
    if isinstance(node, Name):
        if node.name in columns:
            return columns[node.name]
        if node.name in symbols:
            return symbols[node.name][0]
        raise QueryError("Unknown column or scalar: " + node.name)
    if isinstance(node, Access):
        require(infer(node.source, columns, symbols), {"dynamic"})
        require(infer(node.key, columns, symbols), {"string", "int", "long"})
        return "dynamic"
    if isinstance(node, Values):
        return unify([infer(v, columns, symbols) for v in node.values])
    if isinstance(node, Unary):
        kind = infer(node.operand, columns, symbols)
        require(kind, NUMERIC | {"timespan", "null"})
        return kind
    if isinstance(node, Binary):
        a, b = infer(node.left, columns, symbols), infer(node.right, columns, symbols)
        op = node.operator
        if op in ("and", "or"):
            require(a, {"bool", "null"})
            require(b, {"bool", "null"})
            return "bool"
        if op in ("+", "-", "*", "/", "%"):
            if a in NUMERIC | {"null"} and b in NUMERIC | {"null"}:
                return "real" if "real" in (a, b) else "long"
            if op in ("+", "-") and a == "datetime" and b == "timespan":
                return "datetime"
            if op == "-" and a == b == "datetime":
                return "timespan"
            if a == b == "timespan" and op in ("+", "-"):
                return "timespan"
            if a == "timespan" and b in NUMERIC and op in ("*", "/"):
                return "timespan"
            if a == b == "timespan" and op == "/":
                return "real"
            raise QueryError("Incompatible arithmetic operands")
        if op in ("==", "!=", ">", "<", ">=", "<=", "in", "!in"):
            unify([a, b])
            return "bool"
        require(a, {"string", "dynamic", "null"})
        require(b, {"string", "dynamic", "null"})
        return "bool"
    if not isinstance(node, Call):
        raise QueryError("Invalid expression node")
    name = node.name
    args = node.arguments
    if name in CASTS:
        if len(args) != 1:
            raise QueryError("Cast requires one argument")
        infer(args[0], columns, symbols)
        return CASTS[name]
    if name not in ARITIES:
        raise QueryError("Unsupported scalar function: " + name)
    low, high = ARITIES[name]
    if not low <= len(args) <= high:
        raise QueryError("Invalid function argument count")
    kinds = [infer(a, columns, symbols) for a in args]
    if name == "parse_json":
        require(kinds[0], {"string", "dynamic"})
        return "dynamic"
    if name in ("isnull", "isnotnull", "isempty", "isnotempty"):
        return "bool"
    if name == "not":
        require(kinds[0], {"bool", "null"})
        return "bool"
    if name == "coalesce":
        return unify(kinds)
    if name in ("iff", "iif"):
        require(kinds[0], {"bool", "null"})
        return unify(kinds[1:])
    if name == "case":
        if len(args) % 2 != 1:
            raise QueryError("case requires predicate/result pairs and a default")
        for kind in kinds[:-1:2]:
            require(kind, {"bool", "null"})
        return unify(kinds[1::2] + [kinds[-1]])
    if name in ("strcat", "substring", "strlen", "tolower", "toupper"):
        if name != "strcat":
            require(kinds[0], {"string", "dynamic"})
        if name == "substring":
            for kind in kinds[1:]:
                require(kind, {"int", "long"})
        return "long" if name == "strlen" else "string"
    if name in ("now", "ago"):
        for kind in kinds:
            require(kind, {"timespan"})
        return "datetime"
    if name == "bin":
        if kinds[0] in {"datetime", "timespan"}:
            require(kinds[1], {"timespan"})
        else:
            require(kinds[0], NUMERIC)
            require(kinds[1], NUMERIC)
        return kinds[0]
    raise QueryError("Unsupported scalar function")


def cast(value, kind):
    if kind == "string":
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, datetime):
            return value.isoformat().replace("+00:00", "Z")
        if isinstance(value, timedelta):
            return str(value)
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if value is None:
        return None
    try:
        if kind in ("int", "long"):
            if isinstance(value, bool):
                return int(value)
            result = int(Decimal(value)) if isinstance(value, str) else int(value)
            bits = 32 if kind == "int" else 64
            return result if -(2 ** (bits - 1)) <= result < 2 ** (bits - 1) else None
        if kind == "real":
            result = float(value)
            return result if math.isfinite(result) else None
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value != 0
            return {"true": True, "false": False, "1": True, "0": False}.get(str(value).lower())
        if kind == "datetime":
            result = (
                value
                if isinstance(value, datetime)
                else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            )
            return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
        if kind == "timespan":
            if isinstance(value, timedelta):
                return value
            if isinstance(value, (int, float)):
                return timedelta(days=value)
            match = re.fullmatch(r"(-?[\d.]+)(d|h|m|s|ms|us)", str(value))
            if match:
                return timedelta(
                    seconds=float(match[1])
                    * {"d": 86400, "h": 3600, "m": 60, "s": 1, "ms": 0.001, "us": 0.000001}[
                        match[2]
                    ]
                )
            match = re.fullmatch(r"(?:(\d+)\.)?(\d+):(\d+):(\d+(?:\.\d+)?)", str(value))
            if match:
                return timedelta(
                    days=int(match[1] or 0),
                    hours=int(match[2]),
                    minutes=int(match[3]),
                    seconds=float(match[4]),
                )
    except (ValueError, TypeError, OverflowError, InvalidOperation):
        return None
    return None


def binary(op, a, b):
    if op == "and":
        return False if a is False or b is False else None if a is None or b is None else True
    if op == "or":
        return True if a is True or b is True else None if a is None or b is None else False
    if op in ("in", "!in", "in~", "!in~"):
        folded = op.endswith("~")
        negate = op.startswith("!")
        comparison = ("!~" if folded else "!=") if negate else ("=~" if folded else "==")
        result = True if negate else False
        for value in b:
            result = binary("and" if negate else "or", result, binary(comparison, a, value))
        return result
    if op in ("==", "!=", "=~", "!~"):
        if a is None and b is None:
            return None
        if a is None or b is None:
            result = False
        elif op in ("=~", "!~"):
            result = isinstance(a, str) and isinstance(b, str) and a.lower() == b.lower()
        else:
            result = a == b
        return not result if op in ("!=", "!~") else result
    if a is None or b is None:
        return None
    try:
        if op == ">":
            return a > b
        if op == "<":
            return a < b
        if op == ">=":
            return a >= b
        if op == "<=":
            return a <= b
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if not b:
                return None
            return (
                (abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1)
                if isinstance(a, int) and isinstance(b, int)
                else a / b
            )
        if op == "%":
            return a % abs(b) if b else None
        negate = op.startswith("!")
        name = op.lstrip("!")
        sensitive = name.endswith("_cs")
        name = name.removesuffix("_cs")
        if not isinstance(a, str) or not isinstance(b, str):
            return False
        if not sensitive:
            a, b = a.lower(), b.lower()
        if name == "contains":
            result = b in a
        elif name == "has":
            result = b in re.findall(r"[^\W_]+", a)
        elif name == "startswith":
            result = a.startswith(b)
        elif name == "endswith":
            result = a.endswith(b)
        else:
            raise QueryError("Unsupported scalar operator")
        return not result if negate else result
    except (TypeError, ValueError, OverflowError):
        return None


def evaluate(node, row, symbols, now, budget=None):
    if budget:
        budget.tick()
    value = _evaluate(node, row, symbols, now, budget)
    if budget and isinstance(node, (Call, Binary, Unary, Values)):
        budget.account(value)
    return value


def _evaluate(node, row, symbols, now, budget):
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, Name):
        return row[node.name] if node.name in row else symbols[node.name][1]
    if isinstance(node, Access):
        source = evaluate(node.source, row, symbols, now, budget)
        key = evaluate(node.key, row, symbols, now, budget)
        if isinstance(source, dict):
            return source.get(str(key))
        if isinstance(source, list) and isinstance(key, int) and 0 <= key < len(source):
            return source[key]
        return None
    if isinstance(node, Values):
        return [evaluate(v, row, symbols, now, budget) for v in node.values]
    if isinstance(node, Unary):
        value = evaluate(node.operand, row, symbols, now, budget)
        return None if value is None else value if node.operator == "+" else -value
    if isinstance(node, Binary):
        return binary(
            node.operator,
            evaluate(node.left, row, symbols, now, budget),
            evaluate(node.right, row, symbols, now, budget),
        )
    args = [evaluate(v, row, symbols, now, budget) for v in node.arguments]
    name = node.name
    if name in CASTS:
        return cast(args[0], CASTS[name])
    if name == "parse_json":
        if not isinstance(args[0], str):
            return args[0]
        try:
            return json.loads(
                args[0],
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-JSON constant")),
            )
        except ValueError:
            return args[0]
    if name == "isnull":
        return args[0] is None
    if name == "isnotnull":
        return args[0] is not None
    if name == "isempty":
        return args[0] in (None, "")
    if name == "isnotempty":
        return args[0] not in (None, "")
    if name == "not":
        return None if args[0] is None else not args[0]
    if name == "coalesce":
        return next(
            (v for v in args if v is not None and v != ""),
            "" if any(isinstance(v, str) for v in args) else None,
        )
    if name in ("iff", "iif"):
        return args[1] if args[0] is True else args[2]
    if name == "case":
        return next((args[i + 1] for i in range(0, len(args) - 1, 2) if args[i] is True), args[-1])
    if name == "strcat":
        strings = [cast(v, "string") for v in args]
        if (
            budget
            and sum(len(v.encode()) for v in strings) > budget.limits.bytes - budget.used_bytes
        ):
            raise QueryError("Query scalar memory limit exceeded", "QueryLimitExceeded")
        return "".join(strings)
    if name == "substring":
        start = args[1] if args[1] >= 0 else max(len(args[0]) + args[1], 0)
        return args[0][start:] if len(args) == 2 else args[0][start : start + max(args[2], 0)]
    if name == "strlen":
        return len(args[0])
    if name == "tolower":
        return cast(args[0], "string").lower()
    if name == "toupper":
        return cast(args[0], "string").upper()
    if name == "now":
        return now + (args[0] if args else timedelta())
    if name == "ago":
        return now - args[0]
    if name == "bin":
        value, size = args
        if value is None or size is None:
            return None
        if isinstance(size, timedelta):
            seconds = size.total_seconds()
            if seconds <= 0:
                return None
            if isinstance(value, datetime):
                return datetime.fromtimestamp(
                    math.floor(value.timestamp() / seconds) * seconds, UTC
                )
            return timedelta(seconds=math.floor(value.total_seconds() / seconds) * seconds)
        if size <= 0:
            return None
        return (
            value // size
            if isinstance(value, int) and isinstance(size, int)
            else math.floor(value / size)
        ) * size
    raise QueryError("Unsupported scalar function")
