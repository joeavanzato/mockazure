from mockazure.query.types import QueryError
from mockazure.query.wire import cell

GRAPH_TYPES = {
    "string": "String",
    "datetime": "DateTime",
    "bool": "Boolean",
    "int": "Int32",
    "long": "Int64",
    "real": "Double",
    "decimal": "Decimal",
    "guid": "Guid",
    "dynamic": "Object",
    "timespan": "TimeSpan",
    "null": "Object",
}


def hunting_response(result):
    if result.truncated:
        raise QueryError("Partial hunting results are unsupported", "QueryLimitExceeded")
    if any(kind not in GRAPH_TYPES for _, kind in result.columns):
        raise QueryError("Unsupported hunting result type")
    return {
        "schema": [{"name": name, "type": GRAPH_TYPES[kind]} for name, kind in result.columns],
        "results": [
            {
                name: cell(value, kind)
                for (name, kind), value in zip(result.columns, row, strict=True)
            }
            for row in result.rows
        ],
    }
