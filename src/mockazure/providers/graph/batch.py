"""Read-only Graph batching through the same in-process authenticated ASGI boundary."""

from urllib.parse import urlsplit

import httpx
from fastapi import Request

from mockazure.providers.graph.body import invalid, read_json
from mockazure.providers.graph.probes import principal


def validated_items(items, version):
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        invalid("A batch must contain 1..20 independent GET requests")
    identifiers, result = set(), []
    for item in items:
        if (
            not isinstance(item, dict)
            or not {"id", "method", "url"} <= item.keys()
            or not (item.keys() <= {"id", "method", "url", "headers"})
        ):
            invalid("Unsupported batch request properties")
        identifier, method, url = item["id"], item["method"], item["url"]
        if (
            not isinstance(identifier, str)
            or not 1 <= len(identifier) <= 100
            or identifier.lower() in identifiers
            or any(ord(x) < 32 for x in identifier)
        ):
            invalid("Batch identifiers must be unique strings")
        identifiers.add(identifier.lower())
        if method != "GET":
            invalid("Only independent GET batch requests are supported")
        if (
            not isinstance(url, str)
            or not 1 <= len(url.encode()) <= 16384
            or any(ord(x) < 32 for x in url)
            or "\\" in url
        ):
            invalid("Invalid relative batch URL")
        try:
            parsed = urlsplit(url)
        except ValueError:
            invalid("Invalid relative batch URL")
        path = parsed.path.lstrip("/")
        if parsed.scheme or parsed.netloc or parsed.fragment or "%" in path:
            invalid("Batch URLs must be relative Graph resource paths")
        if path.startswith(version + "/"):
            path = path[len(version) + 1 :]
        if path.split("/")[0].split("(", 1)[0] not in {
            "users",
            "groups",
            "me",
            "organization",
            "auditLogs",
            "identityProtection",
            "subscribedSkus",
            "directoryObjects",
            "applications",
            "servicePrincipals",
            "devices",
            "directoryRoles",
            "roleManagement",
            "oauth2PermissionGrants",
            "identity",
            "reports",
            "security",
        } or any(part in {".", "..", "$batch"} for part in path.split("/")):
            invalid("Batch resource is unsupported")
        headers = item.get("headers", {})
        if (
            not isinstance(headers, dict)
            or any(
                not isinstance(k, str)
                or k.lower() not in {"consistencylevel", "accept", "prefer"}
                or not isinstance(v, str)
                or len(v) > 256
                or not v.isascii()
                or any(ord(c) < 32 for c in v)
                for k, v in headers.items()
            )
            or len({k.lower() for k in headers}) != len(headers)
        ):
            invalid("Unsupported batch headers")
        result.append(
            (
                identifier,
                "/" + version + "/" + path + ("?" + parsed.query if parsed.query else ""),
                headers,
            )
        )
    return result


async def dispatch_batch(request: Request, items: list[dict], context) -> list[dict]:
    version = request.url.path.split("/")[1]
    checked = validated_items(items, version)
    # ASGITransport has no sockets, redirects, proxies or external transport fallback.
    transport = httpx.ASGITransport(app=request.app)
    origin = str(request.base_url).rstrip("/")
    responses, total = [], 0
    async with httpx.AsyncClient(
        transport=transport, base_url=origin, follow_redirects=False, trust_env=False
    ) as client:
        for identifier, url, headers in checked:
            response = await client.get(
                url,
                headers={
                    **headers,
                    "Authorization": request.headers["Authorization"],
                    "Host": request.headers["Host"],
                },
            )
            total += len(response.content)
            if len(response.content) > 8 * 1024 * 1024 or total > 32 * 1024 * 1024:
                invalid("Batch response exceeds the local size limit; request smaller pages")
            body = (
                response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else response.text
            )
            responses.append(
                {
                    "id": identifier,
                    "status": response.status_code,
                    "headers": dict(response.headers),
                    "body": body,
                }
            )
    return responses


def register_batch(router):
    @router.post("/$batch")
    async def batch(request: Request):
        async with request.app.state.database.sessions() as session:
            context = await principal(request, session)
        data = await read_json(request)
        if set(data) != {"requests"}:
            invalid("Batch requires only a requests array")
        return {"responses": await dispatch_batch(request, data["requests"], context)}
