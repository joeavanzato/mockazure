"""Purpose-bound local administration continuations and preview receipts."""

import base64
import hashlib
import hmac
import json
import time

from fastapi.encoders import jsonable_encoder

from mockazure.admin.errors import AdminError


def canonical(value):
    return json.dumps(
        jsonable_encoder(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sign(settings, purpose, value):
    raw = canonical({"purpose": purpose, "expires": int(time.time()) + 3600, "value": value})
    encoded = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    mac = hmac.new(settings.token_file.read_bytes().strip(), encoded.encode(), hashlib.sha256)
    return encoded + "." + mac.hexdigest()


def decode(settings, purpose, token):
    try:
        if len(token) > 8192:
            raise ValueError()
        encoded, supplied = token.split(".")
        expected = hmac.new(
            settings.token_file.read_bytes().strip(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise ValueError()
        value = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if value["purpose"] != purpose or value["expires"] <= time.time():
            raise ValueError()
        return value["value"]
    except (ValueError, TypeError, KeyError, UnicodeError, OSError):
        raise AdminError(400, "InvalidContinuation", "Receipt expired or does not match") from None
