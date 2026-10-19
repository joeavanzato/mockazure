"""Execution inputs stay encrypted; reports expose only explicit safe metadata."""

import json

from cryptography.fernet import Fernet

from mockazure.identity.keys import read_wrapping_key


def seal(database, value):
    return (
        Fernet(read_wrapping_key(database.settings.data_dir))
        .encrypt(json.dumps(value, sort_keys=False, separators=(",", ":")).encode())
        .decode()
    )


def unseal(database, value):
    return json.loads(Fernet(read_wrapping_key(database.settings.data_dir)).decrypt(value.encode()))


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if any(
                word in key.lower()
                for word in (
                    "secret",
                    "password",
                    "credential",
                    "token",
                    "authorization",
                    "verifier",
                )
            )
            or key.lower() == "value"
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
