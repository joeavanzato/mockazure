from html import escape
from urllib.parse import urlencode

from starlette.responses import HTMLResponse, RedirectResponse

HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
    "form-action https: http://localhost:* http://127.0.0.1:*; "
    "frame-ancestors 'none'; base-uri 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def page(title, content):
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>MockAzure — " + escape(title) + "</title>"
        "<style>body{font:18px system-ui;max-width:640px;margin:8vh auto;padding:24px;"
        "background:#10212b;color:#e6f3fa}input,button{font:inherit;padding:10px;"
        "margin:8px 0}label{display:block}button{cursor:pointer}</style>"
        "<h1>" + escape(title) + "</h1><p>MockAzure · local lab identity</p>" + content + "</html>",
        headers=HEADERS,
    )


def form(transaction, csrf, stage, app_name="", *, methods=None):
    fields = (
        '<input type="hidden" name="transaction" value="' + str(transaction.id) + '">'
        '<input type="hidden" name="csrf" value="' + escape(csrf, quote=True) + '">'
    )
    if stage == "login":
        content = (
            "<p>Sign in to " + escape(app_name) + " using a local lab account.</p>"
            '<label>Username <input name="username" autocomplete="username" required></label>'
            '<label>Password <input name="password" type="password" '
            'autocomplete="current-password" required></label><button>Sign in</button>'
        )
    elif stage == "password-change":
        content = (
            "<p>Choose a new password before continuing.</p>"
            '<label>New password <input name="new_password" type="password" '
            'autocomplete="new-password" required></label>'
            '<label>Confirm password <input name="confirm_password" type="password" '
            'autocomplete="new-password" required></label><button>Change password</button>'
        )
    elif stage == "consent":
        content = (
            "<p>Allow "
            + escape(app_name)
            + " to use:</p><ul>"
            + "".join("<li>" + escape(scope) + "</li>" for scope in transaction.scopes)
            + '</ul><button name="decision" value="approve">Allow</button>'
            '<button name="decision" value="deny">Deny</button>'
        )
    else:
        content = (
            "<p>This is simulated MFA for a local lab. It does not establish "
            "proof from a physical authenticator.</p>"
            '<button name="decision" value="complete">Simulate MFA success</button>'
            '<button name="decision" value="skip">Continue without simulated MFA</button>'
            '<button name="decision" value="deny">Deny sign-in</button>'
        )
        if methods is not None:
            options = "".join(
                '<option value="' + escape(value, quote=True) + '">' + escape(label) + "</option>"
                for value, label in methods
            )
            content = (
                (
                    '<label>Registered local method <select name="method_id">'
                    + options
                    + "</select></label>"
                    + content
                )
                if methods
                else (
                    "<p>No enabled registered MFA method is available.</p>"
                    + content.replace(
                        '<button name="decision" value="complete">Simulate MFA success</button>', ""
                    )
                )
            )
    return page(
        stage.title(), '<form method="post" action="/' + stage + '">' + fields + content + "</form>"
    )


def callback(transaction, values):
    if transaction.state:
        values["state"] = transaction.state
    if transaction.response_mode == "form_post":
        fields = "".join(
            '<input type="hidden" name="'
            + escape(k, quote=True)
            + '" value="'
            + escape(v, quote=True)
            + '">'
            for k, v in values.items()
        )
        return page(
            "Return to application",
            '<form method="post" action="'
            + escape(transaction.redirect_uri, quote=True)
            + '">'
            + fields
            + "<button>Continue to application</button></form>",
        )
    separator = "&" if "?" in transaction.redirect_uri else "?"
    return RedirectResponse(
        transaction.redirect_uri + separator + urlencode(values), status_code=303, headers=HEADERS
    )
