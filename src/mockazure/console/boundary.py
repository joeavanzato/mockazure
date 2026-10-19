"""Browser sessions grant management access only, with exact-origin CSRF protection."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from mockazure.admin.auth import verify_admin_token
from mockazure.admin.errors import AdminError
from mockazure.console.sessions import COOKIE, authenticate


@dataclass(frozen=True)
class ManagementIdentity:
    method: str
    session_id: UUID | None = None


def require_origin(request):
    settings = request.app.state.settings
    origin = settings.console_public_origin or str(settings.admin_base_url).rstrip("/")
    if request.headers.getlist("origin") != [origin]:
        raise AdminError(403, "InvalidOrigin", "Use the configured lab origin")


async def authorize_management(request):
    settings = request.app.state.settings
    if "authorization" in request.headers:
        values = request.headers.getlist("authorization")
        scheme, _, token = values[0].partition(" ")
        if (
            len(values) != 1
            or scheme.lower() != "bearer"
            or not verify_admin_token(token, settings.token_file)
        ):
            raise AdminError(401, "Unauthorized", "Management authentication required")
        return ManagementIdentity("bearer")
    if settings.console_enabled:
        async with request.app.state.database.sessions() as session:
            row = await authenticate(session, request.cookies.get(COOKIE), datetime.now(UTC))
            if row:
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    require_origin(request)
                    values = request.headers.getlist("x-mockazure-csrf")
                    if len(values) != 1 or not secrets.compare_digest(values[0], row.csrf_token):
                        raise AdminError(
                            403, "InvalidCSRF", "Refresh the session and retry the operation"
                        )
                return ManagementIdentity("session", row.id)
    raise AdminError(401, "Unauthorized", "Management authentication required")
