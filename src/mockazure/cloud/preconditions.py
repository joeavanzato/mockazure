"""Optional local population guards, separate from Microsoft protocol headers.

These only restrict an already-authorized write. Call under the domain write lock.
"""


def check_local_write(headers, row, error_type):
    create = headers.get("x-mockazure-if-none-match")
    version = headers.get("x-mockazure-if-match")
    if create is not None and create != "*":
        raise error_type(400, "InvalidHeaderValue", "Invalid local population precondition")
    if (create == "*" and row is not None) or (
        version is not None and (row is None or getattr(row, "etag", None) != version)
    ):
        raise error_type(412, "ConditionNotMet", "Resource changed during local population")
