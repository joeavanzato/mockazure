"""Versioned bounded scrypt password hashing."""

import hashlib
import secrets


def raw_password(password):
    value = password.encode("utf-8")
    if not 12 <= len(value) <= 1024:
        raise ValueError("Password must contain 12 to 1024 UTF-8 bytes")
    return value


def derive(value, salt):
    return hashlib.scrypt(value, salt=salt, n=32768, r=8, p=3, dklen=32, maxmem=67108864)


def hash_password(password):
    value, salt = raw_password(password), secrets.token_bytes(16)
    return "v1:scrypt:32768:8:3:" + salt.hex() + ":" + derive(value, salt).hex()


def verify_password(password, encoded):
    try:
        version, method, n, r, p, salt, expected = encoded.split(":")
        if (version, method, n, r, p) != ("v1", "scrypt", "32768", "8", "3"):
            return False
        salt, expected = bytes.fromhex(salt), bytes.fromhex(expected)
        if len(salt) != 16 or len(expected) != 32:
            return False
        return secrets.compare_digest(derive(raw_password(password), salt), expected)
    except (ValueError, TypeError, UnicodeError):
        return False
