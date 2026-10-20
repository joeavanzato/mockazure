"""Local authorization-code authentication for explicitly bootstrapped lab users."""

import base64
import hashlib
import secrets
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from mockazure.admin.errors import AdminError

REDIRECT = "http://localhost:8123/mockazure-population"


def rejected():
    raise AdminError(
        401,
        "LocalIdentityRejected",
        "Local delegated login failed or requires an unsupported interactive policy step",
    )


class Form(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.action, self.fields = None, {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            if self.action is not None:
                rejected()
            self.action = attrs.get("action")
        if tag == "input" and attrs.get("name") in {"transaction", "csrf"}:
            name = attrs["name"]
            if name in self.fields or not attrs.get("value"):
                rejected()
            self.fields[name] = attrs["value"]


async def acquire(client, login, tenant, credential):
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    response = await client.get(
        f"{login}/{tenant}/oauth2/v2.0/authorize",
        params={
            "client_id": str(credential.client_id),
            "response_type": "code",
            "redirect_uri": REDIRECT,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": " ".join(
                "https://graph.microsoft.com/" + scope for scope in credential.scopes
            ),
        },
        headers={"Authorization": "", "Cookie": ""},
        follow_redirects=False,
    )
    cookie = response.cookies.get("__Host-mockazure-flow")
    if response.status_code != 200 or not cookie:
        rejected()
    for action, body in (
        (
            "/login",
            {"username": credential.username, "password": credential.password.get_secret_value()},
        ),
        ("/consent", {"decision": "approve"}),
        # Never manufacture MFA success. Current policy decides whether skipping is allowed.
        ("/mfa", {"decision": "skip"}),
    ):
        form = Form(response.text)
        if (
            response.status_code != 200
            or form.action != action
            or set(form.fields) != {"transaction", "csrf"}
        ):
            rejected()
        response = await client.post(
            login + action,
            data={**form.fields, **body},
            headers={"Authorization": "", "Cookie": "__Host-mockazure-flow=" + cookie},
            follow_redirects=False,
        )
    location = response.headers.get("location", "")
    callback = urlsplit(location)
    query = parse_qs(callback.query)
    if (
        response.status_code != 303
        or location.split("?", 1)[0] != REDIRECT
        or query.get("state") != [state]
        or len(query.get("code", [])) != 1
    ):
        rejected()
    return await client.post(
        f"{login}/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "authorization_code",
            "client_id": str(credential.client_id),
            "client_secret": credential.secret.get_secret_value(),
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
            "code": query["code"][0],
        },
        headers={"Authorization": "", "Cookie": ""},
        follow_redirects=False,
    )
