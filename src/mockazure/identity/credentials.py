"""Bounded, salted local credential verification."""

import base64
import hashlib
import hmac
import secrets


def hash_secret(secret: str) -> str:
    encoded = secret.encode("utf-8")
    if not 12 <= len(encoded) <= 4096 or "\x00" in secret:
        raise ValueError("Credential must be 12 to 4096 UTF-8 bytes")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(encoded, salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt + digest).decode("ascii")


def verify_secret(secret: str, verifier: str) -> bool:
    try:
        encoded = secret.encode("utf-8")
        if not 12 <= len(encoded) <= 4096 or "\x00" in secret:
            return False
        if not verifier.startswith("scrypt$"):
            return False
        stored = base64.b64decode(verifier[7:], validate=True)
        if len(stored) != 48:
            return False
        digest = hashlib.scrypt(encoded, salt=stored[:16], n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(digest, stored[16:])
    except (ValueError, TypeError, UnicodeError):
        return False
