"""One-use browser password change bound to the authenticated local login flow."""

import asyncio

from mockazure.identity.credentials import hash_secret, verify_secret
from mockazure.identity.models import User
from mockazure.oauth import pages
from mockazure.oauth.errors import OAuthError
from mockazure.oauth.forms import read_form
from mockazure.oauth.sessions import clock
from mockazure.security.repository import lock_security


async def change_password(request):
    from mockazure.oauth.authorization import browser_transaction
    from mockazure.providers.graph.errors import GraphError
    from mockazure.security.password_reset import validate_password

    fields, now = await read_form(request), clock(request)
    async with request.app.state.database.sessions.begin() as session:
        row, app = await browser_transaction(request, fields, session, "password-change", now)
        await lock_security(session, row.tenant_id)
        user = await session.get(User, row.user_id, populate_existing=True)
        if (
            user is None
            or user.tenant_id != row.tenant_id
            or not user.enabled
            or user.deleted_at
            or not user.password_change_required
            or row.credential_version != user.credential_version
        ):
            raise OAuthError("invalid_grant", "Password change authorization is no longer valid")
        password = fields.get("new_password")
        try:
            validate_password(password)
        except GraphError:
            raise OAuthError(
                "invalid_request", "Password does not meet the local password profile"
            ) from None
        if password != fields.get("confirm_password") or await asyncio.to_thread(
            verify_secret, password, user.verifier
        ):
            raise OAuthError("invalid_request", "Choose and confirm a different password")
        user.verifier = await asyncio.to_thread(hash_secret, password)
        user.credential_version += 1
        user.password_change_required = False
        row.credential_version = user.credential_version
        row.stage = "consent"
        from mockazure.identity.types import PrincipalContext
        from mockazure.security.audit import record_security_audit

        actor = PrincipalContext(
            row.tenant_id,
            row.client_id,
            user.id,
            "delegated",
            "00000003-0000-0000-c000-000000000000",
            frozenset(),
        )
        await record_security_audit(
            session,
            actor,
            "Change user password",
            {"id": str(user.id), "type": "User", "displayName": user.display_name},
            {"passwordProfile": {"forceChangePasswordNextSignIn": True}},
            {"passwordProfile": {"forceChangePasswordNextSignIn": False}},
            now,
        )
        return pages.form(row, fields["csrf"], "consent", app.display_name)
