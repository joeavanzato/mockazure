"""Parse configured local Graph references; never dereference a URL."""

from urllib.parse import urlsplit
from uuid import UUID

from mockazure.providers.graph.body import invalid


def parse_local_reference(
    value: str, allowed_graph_origins: frozenset[str], tenant_id: UUID
) -> UUID:
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(c.isspace() or ord(c) < 32 for c in value)
        or any(c in value for c in ("\\", "%", "?", "#"))
    ):
        invalid("Invalid local directory reference")
    try:
        url = urlsplit(value)
        parts = url.path.split("/")
        if (
            f"{url.scheme}://{url.netloc}" not in allowed_graph_origins
            or url.username
            or url.password
            or len(parts) != 4
            or parts[1] not in {"v1.0", "beta"}
            or parts[2] not in {"directoryObjects", "users", "groups", "servicePrincipals"}
        ):
            raise ValueError()
        identifier = UUID(parts[3])
        if str(identifier) != parts[3].lower():
            raise ValueError()
        return identifier
    except ValueError:
        invalid("Reference must identify an object at a configured local Graph origin")
