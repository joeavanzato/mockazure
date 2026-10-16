from urllib.parse import parse_qsl

from mockazure.oauth.errors import OAuthError


async def read_form(request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != (
        "application/x-www-form-urlencoded"
    ):
        raise OAuthError("invalid_request", "Form URL-encoded content is required")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise OAuthError("invalid_request", "OAuth form exceeds the supported size")
    try:
        pairs = parse_qsl(
            body.decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=64,
            encoding="utf-8",
            errors="strict",
        )
    except (ValueError, UnicodeError):
        raise OAuthError("invalid_request", "Invalid OAuth form") from None
    fields = {}
    for name, value in pairs:
        if not name or len(name) > 100 or name in fields or "\x00" in name + value:
            raise OAuthError("invalid_request", "Invalid or repeated OAuth parameter")
        fields[name] = value
    return fields
