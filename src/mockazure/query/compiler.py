"""Type-bound evaluation over inputs authorized before any join or aggregation."""

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cmp_to_key
from threading import Event

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from .ast import Access, Binary, Binding, Call, Name, Pipeline
from .functions import AGGREGATES, NUMERIC, evaluate, infer, require, unify
from .types import QueryError, QueryLimits, QueryResult


def key(value):
    if isinstance(value, datetime):
        return ("datetime", value.isoformat())
    if isinstance(value, timedelta):
        return ("timespan", value.total_seconds())
    if isinstance(value, dict):
        return ("dynamic", tuple(sorted((str(k), key(v)) for k, v in value.items())))
    if isinstance(value, list):
        return ("array", tuple(key(v) for v in value))
    return (type(value).__name__, value)


def json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    raise TypeError("Unrepresentable query value")


@dataclass
class Table:
    columns: dict
    rows: list[dict]


class Budget:
    def __init__(self, limits, cancelled=None):
        self.limits = limits
        self.deadline = time.monotonic() + limits.seconds
        self.expanded = 0
        self.used_bytes = 0
        self.cancelled = cancelled

    def tick(self):
        if (self.cancelled and self.cancelled.is_set()) or time.monotonic() >= self.deadline:
            raise QueryError("Query execution time limit exceeded", "QueryLimitExceeded")

    def account(self, value, overhead=64):
        self.tick()
        self.used_bytes += (
            len(
                json.dumps(
                    value, default=json_value, separators=(",", ":"), ensure_ascii=False
                ).encode()
            )
            + overhead
        )
        if self.used_bytes > self.limits.bytes:
            raise QueryError("Query accounted memory limit exceeded", "QueryLimitExceeded")

    def materialize(self, columns, rows):
        result = []
        for row in rows:
            self.account(row, 64 + len(row) * 32)
            if len(result) >= self.limits.expanded:
                raise QueryError("Query intermediate row limit exceeded", "QueryLimitExceeded")
            result.append(row)
        return Table(columns, result)

    def expansion(self):
        self.expanded += 1
        self.tick()
        if self.expanded > self.limits.expanded:
            raise QueryError("Query expansion limit exceeded", "QueryLimitExceeded")


class Evaluator:
    def __init__(self, tables, now, limits, cancelled=None):
        self.tables = {}
        self.symbols = {}
        self.now = now
        self.budget = Budget(limits, cancelled)
        for name, value in tables.items():
            columns = dict(value.columns)
            if len(columns) != len(value.columns):
                raise QueryError("Duplicate input columns")
            rows = []
            for values in value.rows:
                if len(values) != len(columns):
                    raise QueryError("Input row does not match its schema")
                row = {
                    name: ("" if v is None and kind == "string" else v)
                    for (name, kind), v in zip(value.columns, values, strict=True)
                }
                self.budget.account(row, 64 + 32 * len(row))
                if len(rows) >= limits.expanded:
                    raise QueryError("Query input row limit exceeded", "QueryLimitExceeded")
                rows.append(row)
            self.tables[name] = Table(columns, rows)

    def value(self, expression, row):
        self.budget.tick()
        return evaluate(expression, row, self.symbols, self.now, self.budget)

    def kind(self, expression, table):
        return infer(expression, table.columns, self.symbols)

    def names(self, items, table):
        output = []
        for i, item in enumerate(items):
            if isinstance(item.expression, Name) and item.expression.name == "*":
                if item.name:
                    raise QueryError("Wildcard cannot be renamed")
                output.extend((name, Name(name), kind) for name, kind in table.columns.items())
                continue
            name = item.name or (
                item.expression.name if isinstance(item.expression, Name) else "Column" + str(i + 1)
            )
            output.append((name, item.expression, self.kind(item.expression, table)))
        if len({n for n, _, _ in output}) != len(output):
            raise QueryError("Duplicate projected column names")
        return output

    def make(self, columns, rows):
        return self.budget.materialize(columns, rows)

    def pipeline(self, pipe):
        if pipe.source not in self.tables:
            raise QueryError("Unknown or unauthorized table: " + pipe.source)
        table = self.tables[pipe.source]
        for op in pipe.operations:
            self.budget.tick()
            name = op.name
            if name == "where":
                expression = op.arguments[0]
                require(self.kind(expression, table), {"bool", "null"})
                table = self.make(
                    table.columns,
                    (row for row in table.rows if self.value(expression, row) is True),
                )
            elif name in ("project", "extend", "distinct"):
                fields = self.names(op.arguments, table)
                columns = {n: k for n, _, k in fields}
                if name == "extend":
                    columns = table.columns | columns
                original = table

                def projected():
                    seen = set()
                    for row in original.rows:
                        value = {n: self.value(e, row) for n, e, _ in fields}
                        if name == "extend":
                            value = row | value
                        if name == "distinct":
                            identity = key(value)
                            if identity in seen:
                                continue
                            self.budget.account(value)
                            seen.add(identity)
                        yield value

                table = self.make(columns, projected())
            elif name == "project-away":
                removed = []
                for item in op.arguments:
                    if (
                        item.name
                        or not isinstance(item.expression, Name)
                        or item.expression.name not in table.columns
                    ):
                        raise QueryError("project-away requires existing column names")
                    removed.append(item.expression.name)
                columns = {n: k for n, k in table.columns.items() if n not in removed}
                table = self.make(columns, ({n: row[n] for n in columns} for row in table.rows))
            elif name == "project-rename":
                renames = {}
                for item in op.arguments:
                    if (
                        not item.name
                        or not isinstance(item.expression, Name)
                        or item.expression.name not in table.columns
                    ):
                        raise QueryError("project-rename requires new=existing")
                    renames[item.expression.name] = item.name
                columns = {renames.get(n, n): k for n, k in table.columns.items()}
                if len(columns) != len(table.columns):
                    raise QueryError("Rename produces duplicate columns")
                table = self.make(
                    columns, ({renames.get(n, n): v for n, v in row.items()} for row in table.rows)
                )
            elif name == "take":
                table = Table(table.columns, table.rows[: op.arguments[0]])
            elif name in ("order", "top"):
                for expression, _, _ in op.arguments:
                    kind = self.kind(expression, table)
                    if kind == "dynamic":
                        raise QueryError("Sort dynamic values through an explicit scalar cast")

                def compare(left, right):
                    self.budget.tick()
                    for expression, direction, nulls in op.arguments:
                        a, b = self.value(expression, left), self.value(expression, right)
                        if a is None or b is None:
                            if a is b:
                                continue
                            return (-1 if a is None else 1) * (1 if nulls == "first" else -1)
                        if a != b:
                            return (1 if a > b else -1) * (1 if direction == "asc" else -1)
                    return 0

                self.budget.account(None, 16 * len(table.rows))
                rows = sorted(table.rows, key=cmp_to_key(compare))
                table = Table(table.columns, rows[: op.options[0]] if name == "top" else rows)
            elif name == "count":
                table = Table({"Count": "long"}, [{"Count": len(table.rows)}])
            elif name == "summarize":
                table = self.summarize(table, op)
            elif name == "join":
                table = self.join(table, self.pipeline(op.arguments[0]), op.options)
            elif name == "union":
                table = self.union(table, self.pipeline(op.arguments[0]), op.options[0])
            elif name == "mv-expand":
                table = self.expand(table, op)
            else:
                raise QueryError("Unsupported table operator")
        return table

    def summarize(self, table, op):
        groups = self.names(op.options, table)
        aggregates = []
        columns = {n: k for n, _, k in groups}
        for item in op.arguments:
            expression = item.expression
            if not isinstance(expression, Call) or expression.name not in AGGREGATES:
                raise QueryError("Expected aggregate function")
            name = expression.name
            args = expression.arguments
            if name == "count":
                if args:
                    raise QueryError("count takes no arguments")
                kind = "long"
            else:
                if not args or (name != "arg_max" and len(args) != 1):
                    raise QueryError("Invalid aggregate arguments")
                kind = self.kind(args[0], table)
                if name in ("sum", "avg"):
                    require(kind, NUMERIC | {"timespan"})
                if name == "countif":
                    require(kind, {"bool", "null"})
                if name == "arg_max":
                    if len(args) < 2 or item.name:
                        raise QueryError("arg_max requires returned columns without a tuple alias")
                    if kind == "dynamic":
                        raise QueryError("arg_max requires a sortable scalar")
                    selected = [args[0]]
                    for arg in args[1:]:
                        selected.extend(Name(n) for n in table.columns) if isinstance(
                            arg, Name
                        ) and arg.name == "*" else selected.append(arg)
                    outputs = []
                    for arg in selected:
                        if not isinstance(arg, Name):
                            raise QueryError("arg_max returned values must be columns")
                        if arg.name not in columns:
                            columns[arg.name] = self.kind(arg, table)
                            outputs.append(arg.name)
                    aggregates.append((None, name, args, outputs))
                    continue
                if name in ("countif", "dcount"):
                    kind = "long"
                if name == "avg":
                    kind = "real" if kind in NUMERIC else kind
            output = item.name or name + "_" + (
                args[0].name if args and isinstance(args[0], Name) else ""
            )
            if output in columns:
                raise QueryError("Duplicate aggregate output")
            columns[output] = kind
            aggregates.append((output, name, args, ()))
        buckets = {}
        for row in table.rows:
            values = [self.value(e, row) for _, e, _ in groups]
            identity = key(values)
            if identity not in buckets:
                self.budget.account(values, 128)
                buckets[identity] = (values, [])
            self.budget.account(None, 8)
            buckets[identity][1].append(row)
            self.budget.tick()
        if not groups and not buckets:
            buckets[()] = ([], [])

        def aggregate_rows():
            for group, rows in buckets.values():
                result = {n: v for (n, _, _), v in zip(groups, group, strict=True)}
                for output, name, args, returned in aggregates:
                    values = [self.value(args[0], row) for row in rows] if args else []
                    self.budget.account(values, 16 * len(values))
                    present = [v for v in values if v is not None]
                    if name == "count":
                        value = len(rows)
                    elif name == "countif":
                        value = sum(v is True for v in values)
                    elif name == "dcount":
                        value = len({key(v) for v in present})
                    elif name == "sum":
                        value = sum(present, timedelta() if columns[output] == "timespan" else 0)
                    elif name == "avg":
                        value = (
                            (
                                sum(present, timedelta() if columns[output] == "timespan" else 0)
                                / len(present)
                            )
                            if present
                            else None
                        )
                    elif name == "min":
                        value = min(present) if present else None
                    elif name == "max":
                        value = max(present) if present else None
                    elif name == "arg_max":
                        chosen = (
                            max(
                                ((v, i) for i, v in enumerate(values) if v is not None),
                                key=lambda x: x[0],
                            )[1]
                            if present
                            else 0
                        )
                        for n in returned:
                            result[n] = (
                                rows[chosen][n]
                                if rows
                                else ("" if columns[n] == "string" else None)
                            )
                        continue
                    result[output] = value
                yield result

        return self.make(columns, aggregate_rows())

    def join(self, left, right, options):
        flavor, conditions = options
        pairs = []
        for condition in conditions:
            if isinstance(condition, Name):
                pairs.append((condition.name, condition.name))
            elif isinstance(condition, Binary) and condition.operator == "==":
                a, b = condition.left, condition.right
                if not (
                    isinstance(a, Access)
                    and isinstance(b, Access)
                    and isinstance(a.source, Name)
                    and isinstance(b.source, Name)
                    and a.source.name == "$left"
                    and b.source.name == "$right"
                ):
                    raise QueryError("Join condition requires $left.column == $right.column")
                pairs.append((a.key.value, b.key.value))
            else:
                raise QueryError("Join requires equality keys")
        for a, b in pairs:
            if a not in left.columns or b not in right.columns:
                raise QueryError("Unknown join key")
            unify([left.columns[a], right.columns[b]])
            if left.columns[a] == "dynamic" or right.columns[b] == "dynamic":
                raise QueryError("Cast dynamic join keys explicitly")
        columns = dict(left.columns)
        renamed = {}
        for name, kind in right.columns.items():
            target = name
            i = 1
            while target in columns:
                target = name + str(i)
                i += 1
            columns[target] = kind
            renamed[name] = target
        if flavor == "leftanti":
            columns = left.columns

        def identity(row, keys):
            values = [row[n] for n in keys]
            return None if any(v is None for v in values) else tuple(values)

        index = {}
        for i, row in enumerate(right.rows):
            self.budget.tick()
            match = identity(row, [b for _, b in pairs])
            if match is not None:
                self.budget.account(match, 128)
                index.setdefault(match, []).append((i, row))
        empty_left = {n: "" if k == "string" else None for n, k in left.columns.items()}
        empty_right = {renamed[n]: "" if k == "string" else None for n, k in right.columns.items()}

        def joined():
            matched = set()
            unique = set()
            for row in left.rows:
                self.budget.tick()
                match = identity(row, [a for a, _ in pairs])
                matches = index.get(match, [])
                if flavor == "innerunique":
                    if match in unique:
                        continue
                    self.budget.account(match)
                    unique.add(match)
                if flavor == "leftanti":
                    if not matches:
                        self.budget.expansion()
                        yield row
                elif matches:
                    for i, other in matches:
                        self.budget.expansion()
                        if i not in matched:
                            self.budget.account(i)
                        matched.add(i)
                        yield row | {renamed[n]: v for n, v in other.items()}
                elif flavor in ("leftouter", "fullouter"):
                    self.budget.expansion()
                    yield row | empty_right
            if flavor == "fullouter":
                for i, row in enumerate(right.rows):
                    if i not in matched:
                        self.budget.expansion()
                        yield empty_left | {renamed[n]: v for n, v in row.items()}

        return self.make(columns, joined())

    def union(self, left, right, kind):
        columns = {}
        mappings = []
        shared = set(left.columns) & set(right.columns)
        reserved = set(left.columns) | set(right.columns)
        outputs = {}
        for table in (left, right):
            mapping = {}
            for name, type_ in table.columns.items():
                if kind == "inner" and name not in shared:
                    continue
                target = name
                identity = (name, type_)
                if identity in outputs:
                    target = outputs[identity]
                elif name in shared and left.columns[name] != right.columns[name]:
                    base = name + "_" + type_
                    target = base
                    suffix = 1
                    while target in reserved or target in columns:
                        target = base + str(suffix)
                        suffix += 1
                outputs[identity] = target
                columns[target] = type_
                mapping[name] = target
            mappings.append(mapping)

        def rows():
            for table, mapping in zip((left, right), mappings, strict=True):
                for row in table.rows:
                    result = {n: "" if k == "string" else None for n, k in columns.items()}
                    result.update({target: row[name] for name, target in mapping.items()})
                    yield result

        return self.make(columns, rows())

    def expand(self, table, op):
        limit, cast_kind = op.options
        fields = self.names(op.arguments, table)
        if cast_kind not in (
            None,
            "string",
            "long",
            "int",
            "real",
            "bool",
            "datetime",
            "timespan",
            "dynamic",
        ):
            raise QueryError("Unsupported expansion type")
        for _, _, kind in fields:
            require(kind, {"dynamic"})
        columns = table.columns | {n: cast_kind or "dynamic" for n, _, _ in fields}

        def rows():
            for row in table.rows:
                lists = []
                for _, expression, _ in fields:
                    value = self.value(expression, row)
                    lists.append(
                        value
                        if isinstance(value, list)
                        else [{k: v} for k, v in value.items()]
                        if isinstance(value, dict)
                        else [value]
                    )
                for i in range(min(max((len(v) for v in lists), default=0), limit)):
                    result = dict(row)
                    for (name, _, _), values in zip(fields, lists, strict=True):
                        value = values[i] if i < len(values) else None
                        if cast_kind and cast_kind != "dynamic":
                            compatible = {
                                "string": str,
                                "int": int,
                                "long": int,
                                "real": float,
                                "bool": bool,
                                "datetime": datetime,
                                "timespan": timedelta,
                            }
                            value = (
                                value
                                if type(value) is compatible[cast_kind]
                                else ("" if cast_kind == "string" else None)
                            )
                        result[name] = value
                    self.budget.expansion()
                    yield result

        return self.make(columns, rows())


def evaluate_query(plan, tables, now, *, limits=None, cancelled=None):
    evaluator = Evaluator(tables, now, limits or QueryLimits(), cancelled)
    result = None
    try:
        for node in plan.nodes:
            if isinstance(node, Binding):
                if node.name in evaluator.symbols or node.name in evaluator.tables:
                    raise QueryError("Duplicate query binding")
                if isinstance(node.value, Pipeline):
                    evaluator.tables[node.name] = evaluator.pipeline(node.value)
                else:
                    kind = infer(node.value, {}, evaluator.symbols)
                    evaluator.symbols[node.name] = (
                        kind,
                        evaluate(node.value, {}, evaluator.symbols, now, evaluator.budget),
                    )
            else:
                result = evaluator.pipeline(node)
        if result is None:
            raise QueryError("A tabular query is required")
        if len(result.rows) > evaluator.budget.limits.rows:
            raise QueryError("Query result row limit exceeded", "QueryLimitExceeded")
        return QueryResult(
            list(result.columns.items()),
            [[row[n] for n in result.columns] for row in result.rows],
            False,
        )
    except (RecursionError, OverflowError, TypeError, KeyError) as error:
        raise QueryError("Invalid or excessively complex typed query") from error


async def execute_query(session, actor, scope, plan, now):
    if scope.tenant_id != actor.tenant_id:
        raise QueryError("Query scope does not belong to this tenant")
    values = dict(session.info.get("query_limits", {}))
    values["seconds"] = min(values.get("seconds", 30), plan.parameters.get("timeout", 600))
    limits = QueryLimits(**values)
    started = time.monotonic()
    cancelled = Event()
    try:
        # Savepoint rolls back a cancelled statement, including in legacy batch.
        async with asyncio.timeout(limits.seconds), session.begin_nested():
            previous = await session.scalar(select(func.current_setting("statement_timeout")))
            await session.execute(
                select(
                    func.set_config(
                        "statement_timeout", str(max(1, int(limits.seconds * 1000))), True
                    )
                )
            )
            if plan.profile == "resourcegraph":
                from mockazure.providers.resourcegraph.inventory import authorized_tables

                tables = await authorized_tables(session, actor, scope, limits=limits)
            elif plan.profile == "loganalytics":
                from uuid import UUID

                from mockazure.monitor.logs import input_tables, sources

                tables = await input_tables(
                    session,
                    actor,
                    tuple(UUID(w) for w in scope.workspace_ids),
                    sources(plan),
                    plan.parameters.get("intervals", ()),
                    limits=limits,
                )
            elif plan.profile == "hunting":
                from uuid import UUID

                from mockazure.hunting.service import input_tables

                tables = await input_tables(
                    session,
                    actor,
                    UUID(scope.workspace_ids[0]) if scope.workspace_ids else None,
                    plan,
                    now,
                    limits=limits,
                )
            else:
                raise QueryError("Unknown query profile")
            remaining = limits.seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise QueryError("Query execution time limit exceeded", "QueryLimitExceeded")
            result = await asyncio.to_thread(
                evaluate_query,
                plan,
                tables,
                now,
                limits=QueryLimits(
                    seconds=remaining,
                    rows=limits.rows,
                    bytes=limits.bytes,
                    expanded=limits.expanded,
                ),
                cancelled=cancelled,
            )
            await session.execute(select(func.set_config("statement_timeout", previous, True)))
            return result
    except TimeoutError:
        raise QueryError("Query execution time limit exceeded", "QueryLimitExceeded") from None
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == "57014":
            raise QueryError("Query execution time limit exceeded", "QueryLimitExceeded") from None
        raise
    finally:
        # Cancelling to_thread alone does not stop its worker. Evaluator ticks do.
        cancelled.set()
