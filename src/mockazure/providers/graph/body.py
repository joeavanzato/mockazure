"""Bounded JSON input with sanitized errors; shared by writes and read batches."""

import json

from mockazure.providers.graph.errors import GraphError


def invalid(message="Invalid directory request"):
    raise GraphError(400, "Request_BadRequest", message)


async def read_json(request, limit=1024 * 1024):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        invalid("Content-Type must be application/json")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise GraphError(413, "Request_EntityTooLarge", "Request exceeds the local size limit")
        data.extend(chunk)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def reject(_):
        raise ValueError()

    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=reject)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 16:
                raise ValueError()
            if isinstance(item, (list, dict)):
                pending.extend(
                    (x, depth + 1) for x in (item.values() if isinstance(item, dict) else item)
                )
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        invalid("Invalid JSON object")
