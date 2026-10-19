"""Native secret IDs need a vault origin; path aliases offer DNS-free entry points."""

import re
from urllib.parse import urlsplit


def suffix(origin):
    host = urlsplit(origin).hostname
    return host.replace(".", "-").replace(":", "-") + ".mockazure.test"


def vault_origin(origin, name):
    parsed = urlsplit(origin)
    return (
        "https://"
        + name.lower()
        + "."
        + suffix(origin)
        + ((":" + str(parsed.port)) if parsed.port else "")
    )


def virtual_vault(authority, origin):
    try:
        parsed = urlsplit("//" + authority)
        expected = urlsplit(origin)
        if (
            parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.netloc.lower() != authority.lower()
            or (parsed.port or 443) != (expected.port or 443)
        ):
            return None
    except ValueError:
        return None
    host = parsed.hostname or ""
    ending = "." + suffix(origin)
    name = host[: -len(ending)] if host.endswith(ending) else ""
    return name if re.fullmatch(r"[a-z][a-z0-9-]{1,22}[a-z0-9]", name) else None


def selected_profile(settings, authority):
    for profile in (settings.endpoints, *settings.client_profiles.values()):
        if authority.lower() == urlsplit(profile.keyvault).netloc.lower() or virtual_vault(
            authority, profile.keyvault
        ):
            return profile
    return None
