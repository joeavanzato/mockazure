"""One HTTP byte range, normalized to the storage end-exclusive interval."""

import re

from mockazure.admin.errors import AdminError


def content_range(header: str | None, size: int) -> tuple[int, int]:
    if header is None:
        return 0, size
    match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", header)
    if match and size > 0:
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last) + 1, size) if last else size
        elif last and int(last) > 0:
            start, end = max(0, size - int(last)), size
        else:
            start, end = size, size
        if 0 <= start < end <= size:
            return start, end
    raise AdminError(416, "InvalidRange", "Requested byte range is not satisfiable")
